"""One traceable report of observed behavior across exploration themes."""
from pathlib import Path
import csv,hashlib,json
import numpy as np
import rasterio
from rasterio._err import CPLE_BaseError
from rasterio.warp import transform
import xarray as xr
from .behavior import summarize,POLICY,LEGEND
from .flood import acquire_cube
from .io import save_cube

ALTERNATIVES={
 'floods':['Water extent or roughness','Wet soil','Vegetation or management change','Terrain visibility and product effects'],
 'wildfires':['Vegetation structure or moisture change','Newly exposed ground','Harvest or other clearing','Terrain visibility and product effects'],
 'agriculture':['Growth and canopy structure','Soil/vegetation moisture','Harvest or management','Geometry and product effects'],
 'landslides':['Surface roughness or vegetation removal','Soil moisture','Terrain shadow or layover','Displacement requires independent geodetic or offset evidence'],
 'earthquakes':['Scattering/moisture changes','Acquisition geometry','Atmospheric/ionospheric phase in separate InSAR data','Deformation requires corrected geodetic evidence']}


def selection_mask(cube,selection):
    shape=cube['power'].shape[-2:]
    if not selection:return np.ones(shape,bool)
    try:
        xx,yy=transform('EPSG:4326',f"EPSG:{int(cube['epsg'])}",[selection['longitude']],[selection['latitude']])
    except (rasterio.errors.RasterioError,CPLE_BaseError):
        return np.zeros(shape,bool)
    if not np.isfinite(xx+yy).all():return np.zeros(shape,bool)
    x,y=cube['x'],cube['y'];dx=x[1]-x[0];dy=y[0]-y[1]
    if xx[0]<x[0]-dx/2 or xx[0]>x[-1]+dx/2 or yy[0]<y[-1]-dy/2 or yy[0]>y[0]+dy/2:return np.zeros(shape,bool)
    radius=selection.get('radius_m',0)
    if radius:
        X,Y=np.meshgrid(x,y);mask=(X-xx[0])**2+(Y-yy[0])**2<=radius**2
    else:mask=np.zeros(shape,bool)
    if not mask.any():mask[np.argmin(abs(y-yy[0])),np.argmin(abs(x-xx[0]))]=True
    return mask


def process_characterization(request,output):
    from .engine import validate_request,validate_output,safe_json,write_cog,analyze
    validate_request(request,'characterization');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    b,a=request['baseline'],request['assessment']
    if b['start_date']>b['end_date'] or a['start_date']>a['end_date'] or b['end_date']>=a['start_date']:raise ValueError('Ordered non-overlapping baseline/assessment windows are required')
    theme=request.get('theme','explore')
    result={'contract_version':'1.0','request_id':request['request_id'],'analysis':'surface_behavior_characterization',
            'status':'no_data','theme':theme,'policy':POLICY,'observations':{},'quality':{},
            'what_changed':{},'when':{},'behavior':{},'evidence':{'status':'not_supplied','items':[]},
            'methods':{},'insights':[],'alternative_explanations':ALTERNATIVES.get(theme,['Scattering geometry','Moisture and vegetation','Surface structure','Product and visibility effects']),
            'artifacts':[],'phenomenon_probability':None,'causal_attribution':None,'displacement_cm':None,
            'limitations':['Patterns name observed radar trajectories, not confirmed natural-phenomenon classes.',
                           'The same spatial cells contribute at all declared comparison dates; gaps do not establish continuous behavior.',
                           'A threshold crossing is a diagnostic policy, not a calibrated event probability.',
                           'Thermal-noise correction and complete terrain/SNR validation are not performed.',
                           'GP conditional bands and spatial resampling intervals answer different questions and omit important model/reference errors.']}
    def finish():
        response=safe_json(result);validate_output(response,'characterization')
        (out/'request.json').write_text(json.dumps(request,indent=2));(out/'result.json').write_text(json.dumps(response,indent=2,allow_nan=False));return response
    def register(path,role,media,units=None):
        item={'id':path.stem,'href':path.relative_to(out).as_posix(),'role':role,'media_type':media,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if units:item['units']=units
        result['artifacts'].append(item)
    cube=acquire_cube(request,out)
    if cube is None:result['reason']='No compatible measured series brackets the declared windows';return finish()
    result['observations']={'dates':list(map(str,cube['dates'])),'polarizations':list(map(str,cube['polarizations'])),
        'grid':{'epsg':int(cube['epsg']),'width':len(cube['x']),'height':len(cube['y'])}}
    selection=selection_mask(cube,request.get('selection'))
    if not selection.any():result['reason']='Selection is outside the observed grid';return finish()
    features=summarize(cube,b,a,selection)
    result['quality']=features.get('quality',{})
    if features['status']=='no_data':result['reason']=features['reason'];return finish()
    dates=cube['dates'].astype(str);chosen=features['chosen'];before=features['before'];after=features['after']
    sufficient=len(before)>=POLICY['minimum_baseline_dates'] and len(after)>=POLICY['minimum_assessment_dates']
    result['status']='complete' if sufficient and features['comparison_support'].sum()>=POLICY['minimum_comparison_pixels'] else 'partial'
    result['observations']={'dates':dates.tolist(),'comparison_dates':dates[chosen].tolist(),
        'baseline_dates':dates[before].tolist(),'assessment_dates':dates[after].tolist(),
        'first_assessment_date':str(dates[after[0]]),'polarizations':list(map(str,cube['polarizations'])),
        'co_polarization':features['co_polarization'],'cross_polarization':features['cross_polarization'],
        'grid':{'epsg':int(cube['epsg']),'width':len(cube['x']),'height':len(cube['y']),
                'posting_m':[float(cube['x'][1]-cube['x'][0]),float(cube['y'][0]-cube['y'][1])]},
        'sources':json.loads(str(cube.get('source_metadata_json','[]'))),
        'processing':json.loads(str(cube.get('processing_metadata_json','{}')))}
    if request['source']['mode']=='local_cube':result['observations']['input_cube_sha256']=hashlib.sha256(Path(request['source']['cube_path']).read_bytes()).hexdigest()
    if request.get('context'):result['context']=request['context']
    result['what_changed']={'channels':features['channel_summary'],'polarization_ratio_change':features['polarization_ratio_change'],
                            'statistic_scope':'Cellwise dB relative to per-cell baseline median, summarized across fixed common support'}
    result['when']=features['timing']
    result['behavior']={'regional_pattern':features['regional_pattern'],'spatial_pattern_counts':features['pattern_counts'],
                        'legend':{str(k):v for k,v in LEGEND.items()},'spatial_pattern_unit':'analysis cells',
                        'persistence_semantics':'Fraction/count of observed assessment acquisitions; no continuous duration or ecosystem recovery inferred'}
    counts=features['pattern_counts']
    result['behavior']['most_frequent_cell_pattern']=max(counts,key=counts.get)
    x,y=cube['x'],cube['y'];epsg=int(cube['epsg'])
    data={k:(('y','x'),features[k].astype('uint8') if features[k].dtype==bool else features[k]) for k in
          ['pattern_code','comparison_support','selection_mask','observed_change_frequency','valid_assessment_count']}
    data['target_change_db']=(('polarization','y','x'),features['target_change_db'])
    ds=xr.Dataset(data,coords={'x':x,'y':y,'polarization':list(map(str,cube['polarizations']))},
                  attrs={'epsg_code':epsg,'policy_id':POLICY['id'],'target_date':str(dates[after[0]]),
                         'description':'Observed radar patterns and fixed support; not phenomenon classes'})
    wkt=rasterio.crs.CRS.from_epsg(epsg).to_wkt();ds['spatial_ref']=xr.DataArray(np.int32(0),attrs={'spatial_ref':wkt,'crs_wkt':wkt})
    for key in data:ds[key].attrs['grid_mapping']='spatial_ref'
    ds.pattern_code.attrs['legend']=json.dumps(LEGEND);ds.target_change_db.attrs['units']='dB'
    ds.observed_change_frequency.attrs.update(units='fraction',interpretation='Co-pol absolute change >=3 dB divided by actual supported assessment count; not event probability')
    ds.x.attrs.update(units='m',standard_name='projection_x_coordinate');ds.y.attrs.update(units='m',standard_name='projection_y_coordinate')
    path=out/'behavior_arrays.nc';ds.to_netcdf(path,engine='h5netcdf',encoding={k:{'zlib':True,'complevel':3} for k in data});register(path,'observed_behavior_arrays','application/x-netcdf')
    for key,units in [('pattern_code','categorical observed pattern'),('target_change_db','dB'),('observed_change_frequency','fraction of supported assessment observations')]:
        path=out/(key+'.tif');write_cog(path,features[key],x,y,epsg,units,angular=key=='pattern_code');register(path,'observed_behavior_map','image/tiff',units)
    path=out/'fixed_support_trajectories.csv'
    with path.open('w',newline='',encoding='utf8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(features['series'][0]));writer.writeheader();writer.writerows(features['series'])
    register(path,'observed_fixed_support_series','text/csv','dB')
    common=features['comparison_support'];selected_cube={**cube,'valid':cube['valid']&common[None,None]}
    # Retain only comparison dates for downstream methods. No unobserved dates
    # or implicit reference-to-other-grid comparisons enter those workers.
    selected_cube.update(power=cube['power'][chosen],valid=selected_cube['valid'][chosen],dates=cube['dates'][chosen])
    if 'source_metadata_json' in selected_cube:
        sources=json.loads(str(selected_cube['source_metadata_json']))
        if len(sources)==len(dates):selected_cube['source_metadata_json']=np.array(json.dumps([sources[i] for i in chosen]))
    selected_path=out/'selected_measured_cube.npz';save_cube(selected_cube,selected_path)
    if request.get('include_methods',True) and int(common.sum())>=8 and len(chosen)>=3:
        job={'contract_version':'1.0','request_id':request['request_id'],'theme':theme,
             'source':{'mode':'local_cube','cube_path':str(selected_path)},'analyses':['physical','spectral','stochastic']}
        if request.get('transformer_checkpoint'):job['analyses'].append('transformer');job['transformer_checkpoint']=request['transformer_checkpoint']
        report=analyze(job,out/'methods');result['methods']=report['capabilities']
        for artifact in report['artifacts']:result['artifacts'].append({**artifact,'id':'methods.'+artifact['id'],'href':'methods/'+artifact['href']})
    else:result['methods']={'status':'not_requested_or_insufficient_spatial_temporal_support'}
    water=request.get('water_reference')
    if water:
        from .flood import process_flood
        flood_request={'contract_version':'1.0','request_id':request['request_id'],
            'source':{'mode':'local_cube','cube_path':str(selected_path)},'baseline':b,'assessment':a,'reference':water}
        if request.get('context'):flood_request['context']=request['context']
        r=process_flood(flood_request,out/'water_evidence')
        evidence={'kind':'independent_optical_water_product','status':r['status'],
                  'manifest':'water_evidence/result.json','reference_evidence':r['reference_evidence'],
                  'proxy_comparison':r['proxy_comparison'],'matched_controls':r['matched_controls']}
        result['evidence']['items'].append(evidence)
        ref=r['reference_evidence'];count=ref.get('comparison_supported_pixels',0)
        result['evidence']['status']=('independent_product_comparison' if ref.get('fraction_of_radar_support',0)>=.5 else 'limited_independent_support') if count>=8 else 'insufficient_independent_support'
        path=out/'water_evidence/result.json';register(path,'independent_water_evidence_manifest','application/json')
        for artifact in r['artifacts']:result['artifacts'].append({**artifact,'id':'water.'+artifact['id'],'href':'water_evidence/'+artifact['href']})
    if request.get('disturbance_reference'):
        from .disturbance import summarize_disturbance
        evidence=summarize_disturbance(request['disturbance_reference'],cube,common,b,a)
        result['evidence']['items'].append(evidence)
        result['evidence']['status']='independent_product_comparison' if evidence['status']=='usable' else 'insufficient_independent_support'
        with np.load(request['disturbance_reference'],allow_pickle=False) as reference:
            for key in ['valid_coverage','confirmed_loss_fraction','provisional_loss_fraction','confirmed_outside_window_fraction']:
                path=out/('optical_disturbance_'+key+'.tif')
                write_cog(path,reference[key],x,y,epsg,'source-class share or coverage; not burned area')
                register(path,'independent_optical_disturbance_support','image/tiff','fraction')
        from .characterization_render import disturbance_figure
        path=out/'optical_disturbance_evidence.png'
        disturbance_figure(cube,common,b,evidence,request['disturbance_reference'],path)
        register(path,'independent_optical_disturbance_figure','image/png')
    metric=features['channel_summary'][0]['first_assessment_change_db']
    result['insights']=[{'id':'measured_change','observation':f"Median {features['co_polarization']} change is {metric['median']:+.2f} dB on {int(common.sum())} fixed-support cells; the spatial interquartile range is {metric['p25']:+.2f} to {metric['p75']:+.2f} dB.",
        'interpretation_limit':'This is a radar response; alternative physical mechanisms and product effects remain.',
        'evidence_artifacts':['target_change_db','behavior_arrays']},
       {'id':'observed_pattern','observation':'The regional observed pattern is '+features['regional_pattern']+'.',
        'interpretation_limit':'Pattern names follow a fixed descriptive policy; the spatial distribution can differ from the regional median.',
        'evidence_artifacts':['pattern_code','fixed_support_trajectories']}]
    dominant=result['behavior']['most_frequent_cell_pattern']
    result['insights'].append({'id':'spatial_pattern_support',
        'observation':f"The most frequent cell pattern is {dominant}: {counts[dominant]} of {int(common.sum())} fixed-support cells.",
        'interpretation_limit':'A regional median can hide localized behavior; these are trajectory patterns rather than cause labels.',
        'evidence_artifacts':['pattern_code','behavior_arrays']})
    if not sufficient:result['limitations'].append('The declared minimum baseline/assessment acquisition count is not met; pattern interpretation remains limited.')
    if result['evidence']['status']=='not_supplied':result['limitations'].append('No independent phenomenon reference is supplied; the report supports observed radar behavior only.')
    from .characterization_render import report_figure
    path=out/'characterization.png';report_figure(cube,features,result,path);register(path,'observed_behavior_scientific_figure','image/png')
    return finish()
