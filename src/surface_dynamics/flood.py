"""Evidence-linked retrospective flood characterization worker; no forecasts."""
from pathlib import Path
import csv,hashlib,json
import numpy as np
import xarray as xr
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_geom
from rasterio.transform import from_origin
from .io import load_cube,fetch_chip,cube_from_chips,save_cube,exclude_empty_acquisitions
from .analysis import measured_db
from .catalog import search,group_entries
from .flood_signatures import characterize,POLICY

GROUP_LEGEND={'0':'reference-stable predominantly nonwater','1':'new strict open-water class','2':'stable strict open-water class','3':'strict open-water class loss','4':'mixed/partial or other ambiguous transition','255':'unsupported SAR/reference or temporally ineligible'}


def load_reference(path,cube,before_date,target_date,assessment_start):
    path=Path(path);sidecar=path.with_suffix('.json')
    if not sidecar.exists():raise ValueError('Independent reference requires a checksum/provenance sidecar')
    provenance=json.loads(sidecar.read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=provenance['sha256']:raise ValueError('Independent reference checksum mismatch')
    with np.load(path,allow_pickle=False) as h:reference={k:h[k] for k in h.files}
    if str(reference.get('source_family',''))!='OPERA_DSWX_HLS':raise ValueError('This policy requires the documented independent optical product, not another SAR-derived water mask')
    for key in ['x','y']:
        if not np.array_equal(reference[key],cube[key]):raise ValueError('Reference class fractions must be explicitly aligned to the measured analysis grid')
    if int(reference['epsg'])!=int(cube['epsg']):raise ValueError('Reference CRS mismatch')
    for epoch in ['pre','post']:
        for key in ['open_class_fraction','partial_class_fraction','valid_coverage']:
            a=reference[epoch+'_'+key]
            if a.shape!=cube['power'].shape[-2:] or np.any(np.isfinite(a)&((a<0)|(a>1+1e-5))):raise ValueError('Invalid reference class support fractions')
        total=reference[epoch+'_open_class_fraction']+reference[epoch+'_partial_class_fraction']
        if np.any(np.isfinite(total)&(total>1+1e-5)):raise ValueError('Mutually exclusive reference class shares exceed one')
    offsets=[abs(int((np.datetime64(str(reference[epoch+'_date']))-np.datetime64(date))/np.timedelta64(1,'D'))) for epoch,date in [('pre',before_date),('post',target_date)]]
    temporal_ok=max(offsets)<=POLICY['maximum_optical_offset_days'] and str(reference['pre_date'])<assessment_start<=str(reference['post_date'])
    info={'status':'temporally_eligible' if temporal_ok else 'outside_temporal_support','pre_date':str(reference['pre_date']),
          'post_date':str(reference['post_date']),'pre_offset_days':offsets[0],'post_offset_days':offsets[1],
          'source_family':'OPERA_DSWX_HLS','sha256':provenance['sha256'],'provenance':provenance,
          'reference_kind':'independent_optical_classification_product_not_ground_truth'}
    return reference if temporal_ok else None,info


def acquire_cube(request,out):
    s=request['source'];baseline=request['baseline'];assessment=request['assessment']
    if s['mode']=='local_cube':return load_cube(s['cube_path'])
    catalog=search(s['longitude'],s['latitude'],start=baseline['start_date']+'T00:00:00Z',end=assessment['end_date']+'T23:59:59Z',collection=s.get('collection'))
    (out/'catalog.json').write_text(json.dumps(catalog,indent=2));options=[]
    for key,entries in group_entries(catalog['entries']).items():
        pre=[e for e in entries if baseline['start_date']<=e['time_start'][:10]<=baseline['end_date']]
        post=[e for e in entries if assessment['start_date']<=e['time_start'][:10]<=assessment['end_date']]
        if pre and post:
            lag=int((np.datetime64(post[0]['time_start'][:10])-np.datetime64(assessment['start_date']))/np.timedelta64(1,'D'))
            options.append(((len(pre)>=3,-lag,min(len(pre),6),len(post),key),pre,post))
    if not options:return None
    _,pre,post=max(options,key=lambda v:v[0]);cap=s.get('max_dates',12)
    npre=min(len(pre),max(1,cap//2));npost=min(len(post),cap-npre)
    entries=[pre[i] for i in np.linspace(0,len(pre)-1,npre,dtype=int)]+[post[i] for i in np.linspace(0,len(post)-1,npost,dtype=int)]
    paths=[]
    for e in entries:
        path=out/'sar_cache'/(e['time_start'][:10]+'.npz');fetch_chip(e,s['longitude'],s['latitude'],path,s.get('native_chip_pixels',512));paths.append(path)
    with np.load(paths[0],allow_pickle=False) as measured:spacing=float(measured['x'][1]-measured['x'][0])
    target=s.get('target_posting_m',40);factor=int(round(target/spacing))
    if factor<1 or not np.isclose(factor*spacing,target):raise ValueError('No implicit interpolation of measured power to requested posting')
    cube=cube_from_chips(paths,factor);cube,excluded=exclude_empty_acquisitions(cube)
    cube['processing_metadata_json']=np.array(json.dumps({'collection':s.get('collection','NISAR_L2_GCOV_PROVISIONAL_V1'),'target_posting_m':target,'excluded_empty_acquisitions':excluded,'aggregation':'arithmetic linear power before dB; strict block support'}))
    save_cube(cube,out/'measured_cube.npz');return cube


def process_flood(request,output):
    from .engine import validate_request,validate_output,safe_json,write_cog
    validate_request(request,'flood');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    for key in ['baseline','assessment']:
        if request[key]['end_date']<request[key]['start_date']:raise ValueError('Window end precedes start')
    if request['baseline']['end_date']>=request['assessment']['start_date']:raise ValueError('Baseline and assessment must not overlap')
    if request.get('context') and request['context']['end_date']<request['context']['start_date']:raise ValueError('Context end precedes start')
    (out/'request.json').write_text(json.dumps(request,indent=2))
    result={'contract_version':'1.0','request_id':request['request_id'],'analysis':'flood_characterization','scope':'retrospective_observed_surface_signatures',
            'status':'no_data','policy':POLICY,'observations':{},'reference_evidence':{'status':'not_supplied'},
            'signature_summary':{},'matched_controls':{},'proxy_comparison':{},'objects':[],'artifacts':[],'insights':[],
            'flood_depth_m':None,'flood_probability':None,'causal_attribution':None,
            'limitations':['This is a development characterization pilot, not a globally validated flood classifier or forecast.',
                           'Radar thresholds describe signal changes. Radar-signature area is not inundated area.',
                           'The optical product is independent of NISAR but has classification, cloud, temporal and mixed-pixel errors; agreement is not ground-truth flood accuracy.',
                           'Candidate controls are reference-stable predominantly nonwater cells matched on baseline radar levels; unmeasured confounders and intervening events remain.',
                           'Thermal-noise correction is not performed here. Missing SNR, terrain-visibility, land-cover and under-canopy water validation constrain physical interpretation; wind, wet soil and double bounce can change radar water response.',
                           'Persistence counts actual changed observations; gaps do not establish continuous flood duration. Signal return is not ecosystem or hydrologic recovery.']}
    if request.get('context'):result['context']=request['context']
    def finish():
        response=safe_json(result);validate_output(response,'flood');(out/'result.json').write_text(json.dumps(response,indent=2,allow_nan=False));return response
    cube=acquire_cube(request,out)
    if cube is None:result['reason']='No compatible measured series brackets both windows';return finish()
    dates=np.asarray(cube['dates']).astype(str);b=request['baseline'];a=request['assessment']
    before=[d for d in dates if b['start_date']<=d<=b['end_date']];after=[d for d in dates if a['start_date']<=d<=a['end_date']]
    if not before or not after:result['reason']='Both windows need actual supported acquisitions';return finish()
    ref=None;ref_request=request.get('reference')
    if ref_request:
        if ref_request['mode']=='local_optical_pair':path=Path(ref_request['path'])
        else:
            from .optical_water import prepare_reference_pair
            path=out/'reference_pair.npz';report=prepare_reference_pair(cube,ref_request['longitude'],ref_request['latitude'],before[-1],after[0],path,POLICY['maximum_optical_offset_days'],16,boundary_date=a['start_date'])
            if report['status']!='prepared':path=None;result['reference_evidence']=report
        if path is not None:ref,result['reference_evidence']=load_reference(path,cube,before[-1],after[0],a['start_date'])
    features=characterize(cube,b['start_date'],b['end_date'],a['start_date'],a['end_date'],ref)
    if features['status']=='no_data':result['reason']=features['reason'];return finish()
    x,y=cube['x'],cube['y'];dx=float(x[1]-x[0]);dy=float(y[0]-y[1]);epsg=int(cube['epsg']);cell_area_ha=dx*dy/10000
    result['observations']={'dates':dates.tolist(),'polarizations':list(map(str,cube['polarizations'])),'baseline_dates':before,'assessment_dates':after,
                            'fixed_assessment_date':after[0],'assessment_selection':'first observed acquisition in declared assessment window; not selected on signal magnitude',
                            'grid':{'epsg':epsg,'width':len(x),'height':len(y),'pixel_spacing_m':[dx,dy]},
                            'sources':json.loads(str(cube.get('source_metadata_json','[]'))),
                            'processing':json.loads(str(cube.get('processing_metadata_json','{}')))}
    support=features['comparison_support'];groups=features['reference_group'];ref_count=int((support&(groups!=255)).sum());joint_fraction=ref_count/max(int(support.sum()),1)
    result['reference_evidence'].update(comparison_supported_pixels=ref_count,fraction_of_radar_support=joint_fraction,group_legend=GROUP_LEGEND,
        group_counts={GROUP_LEGEND[str(code)]:int((support&(groups==code)).sum()) for code in [0,1,2,3,4,255]})
    result.update(status='complete' if ref is not None and joint_fraction>=.5 and len(before)>=3 and len(after)>=2 else 'partial',
                  matched_controls=features['matching'],proxy_comparison=features['proxy_agreement'],objects=features['objects'])
    for obj in result['objects']:obj['radar_signature_analysis_cell_area_ha']=obj['analysis_cells']*cell_area_ha
    result['matched_controls']['co_pol_contrast']=features['matched_contrast']
    result['signature_summary']={'interpretation_status':features['interpretation_status'],'co_polarization':features['co_polarization'],
        'cross_polarization':features['cross_polarization'],'radar_supported_pixels':int(support.sum()),
        'darkening_pixels':int(features['candidate_darkening'].sum()),'brightening_pixels':int(features['co_pol_brightening'].sum()),
        'darkening_signature_analysis_cell_area_ha':float(features['candidate_darkening'].sum()*cell_area_ha),
        'new_open_water_reference_analysis_cell_area_ha':float((groups==1).sum()*cell_area_ha),
        'area_semantics':'Nominal analysis-cell area of the specified mask, not independently retrieved inundation extent or water volume',
        'signature_objects_total':features['total_signature_objects'],'reported_objects':len(features['objects'])}
    def register(path,role,media,units=None):
        item={'id':path.stem,'href':path.relative_to(out).as_posix(),'role':role,'media_type':media,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if units:item['units']=units
        result['artifacts'].append(item)
    co=features['co_index'];new=support&(groups==1)
    median=float(np.median(features['target_change_db'][co][support]))
    result['insights'].append({'id':'observed_radar_response','observation':f'Median {features["co_polarization"]} change on common radar support is {median:+.2f} dB at {after[0]} relative to {len(before)} baseline acquisitions.',
                               'interpretation_limit':'Signal change can reflect water, moisture, vegetation, management, geometry or processing. It is not a flood label.','evidence_artifacts':['target_change_db','characterization_arrays']})
    result['insights'].append({'id':'independent_water_support','observation':f'{int(new.sum())} analysis cells have a strict new-open-water transition in the usable optical reference comparison; reference support covers {joint_fraction:.1%} of radar support.',
                               'interpretation_limit':'Optical class shares, cloud exclusions and different acquisition dates limit the comparison; partial water is not treated as complete water.','evidence_artifacts':['reference_group','characterization_arrays']})
    if len(after)<2:result['limitations'].append('Only one assessment acquisition exists; temporal persistence and confirmed signal return are unavailable.')
    if any('BETA' in str(s.get('source','')) for s in result['observations']['sources']):result['limitations'].append('The selected beta NISAR products are not fully calibrated; instrument/product effects can affect quantitative interpretation.')
    context=request.get('context')
    if context:
        lag=max(0,int((np.datetime64(after[0])-np.datetime64(context['end_date']))/np.timedelta64(1,'D')))
        result['observations']['days_after_reported_event_interval']=lag
        if lag:result['limitations'].append(f'The first radar assessment is {lag} day(s) after the reported event interval. A transient flood may have receded before observation.')
    data={'gamma0_power':(('time','polarization','y','x'),cube['power']),
          'change_db':(('time','polarization','y','x'),features['change_db']),
          'baseline_db':(('polarization','y','x'),features['baseline_db']),
          'baseline_mad_db':(('polarization','y','x'),features['baseline_mad_db']),
          'baseline_valid_count':(('polarization','y','x'),features['baseline_valid_count']),
          'target_change_db':(('polarization','y','x'),features['target_change_db']),
          'target_minus_last_baseline_db':(('polarization','y','x'),features['target_minus_last_baseline_db']),
          'robust_scaled_target_change':(('polarization','y','x'),features['robust_scaled_target_change'])}
    for key in ['reference_group','matched_control_mask','matched_positive_mask','comparison_support','candidate_darkening','co_pol_brightening','darkening_observation_frequency','brightening_observation_frequency','co_pol_valid_assessment_count','object_labels']:
        value=features[key];data[key]=(('y','x'),value.astype('uint8') if value.dtype==bool else value)
    if ref is not None:
        for epoch in ['pre','post']:
            for key in ['open_class_fraction','partial_class_fraction','valid_coverage']:data[epoch+'_'+key]=(('y','x'),ref[epoch+'_'+key])
    ds=xr.Dataset(data,coords={'time':np.array(dates,dtype='datetime64[D]'),'polarization':list(map(str,cube['polarizations'])),'x':x,'y':y},
                  attrs={'description':'Retrospective radar signatures and independent optical-product evidence; no forecast, flood-depth or causal estimate','epsg_code':epsg,'policy_id':POLICY['policy_id'],'target_date':str(after[0])})
    wkt=rasterio.crs.CRS.from_epsg(epsg).to_wkt();ds['spatial_ref']=xr.DataArray(np.int32(0),attrs={'spatial_ref':wkt,'crs_wkt':wkt})
    for key in ds.data_vars:
        if 'x' in ds[key].dims:ds[key].attrs['grid_mapping']='spatial_ref'
        if key.endswith('_db'):ds[key].attrs['units']='dB'
    ds.gamma0_power.attrs.update(units='1',radiometry='linear gamma0 power; arithmetic spatial averaging before dB')
    ds.reference_group.attrs['legend']=json.dumps(GROUP_LEGEND)
    ds.robust_scaled_target_change.attrs.update(units='1',interpretation='Baseline MAD scaled deviation with declared dB floor; not a Gaussian significance test')
    ds.darkening_observation_frequency.attrs['criterion']='Co-pol change <= -3 dB only; unlike the first-date dual-channel candidate this does not require cross-pol darkening'
    for key in ['darkening_observation_frequency','brightening_observation_frequency']:ds[key].attrs.update(units='fraction of supported assessment acquisitions',interpretation='Observed signal frequency, not flood probability or continuous duration')
    ds.x.attrs.update(units='m',standard_name='projection_x_coordinate');ds.y.attrs.update(units='m',standard_name='projection_y_coordinate')
    path=out/'characterization_arrays.nc';ds.to_netcdf(path,engine='h5netcdf',encoding={k:{'zlib':True,'complevel':3} for k in ds.data_vars if ds[k].ndim>0});register(path,'all_observed_signatures_and_reference_support','application/x-netcdf')
    for key,unit in [('target_change_db','dB'),('robust_scaled_target_change','robust scaled deviation; not a Gaussian z-test'),('reference_group','categorical support group'),('matched_control_mask','mask'),('darkening_observation_frequency','fraction of observed assessment acquisitions')]:
        path=out/(key+'.tif');write_cog(path,features[key],x,y,epsg,unit,angular=key in ['reference_group','matched_control_mask']);register(path,'descriptive_map','image/tiff',unit)
    path=out/'group_trajectories.csv'
    with path.open('w',newline='',encoding='utf8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(features['series'][0]));writer.writeheader();writer.writerows(features['series'])
    register(path,'measured_date_group_change_series','text/csv','dB')
    by_id={o['object_id']:o for o in features['objects']};geometries=[]
    transform=from_origin(x[0]-dx/2,y[0]+dy/2,dx,dy)
    for geometry,value in shapes(features['object_labels'].astype('int32'),mask=np.isin(features['object_labels'],list(by_id)),transform=transform,connectivity=4):
        geometries.append({'type':'Feature','geometry':transform_geom(f'EPSG:{epsg}','EPSG:4326',geometry),'properties':by_id[int(value)]})
    path=out/'signature_objects.geojson';path.write_text(json.dumps({'type':'FeatureCollection','features':geometries},indent=2,allow_nan=False));register(path,'radar_signature_objects_not_flood_polygons','application/geo+json')
    from .flood_render import characterization_figure
    path=out/'flood_characterization.png';characterization_figure(cube,features,result,path);register(path,'evidence_linked_scientific_figure','image/png')
    return finish()
