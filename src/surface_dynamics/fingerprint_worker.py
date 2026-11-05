"""Portable observed-baseline feature worker, with globally queryable inputs."""
from pathlib import Path
import hashlib
import json
import numpy as np
from rasterio.warp import transform
from .engine import validate_request,validate_output,safe_json,write_cog
from .flood import acquire_cube
from .characterization import selection_mask
from .embedding_atlas import sample_blocks
from .surface_features import fingerprint,DEFAULT_POLICY,pixel_summary


def process_fingerprint(request,output):
    validate_request(request,'fingerprint');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    baseline,assessment=request['baseline'],request['assessment']
    if baseline['start_date']>baseline['end_date'] or assessment['start_date']>assessment['end_date'] or baseline['end_date']>=assessment['start_date']:
        raise ValueError('Ordered nonoverlapping baseline/assessment windows are required')
    result={'contract_version':'1.0','request_id':request['request_id'],'analysis':'observed_surface_fingerprint',
        'status':'no_data','policy':DEFAULT_POLICY,'observations':{},'quality':{},'features':{},'points':[],
        'normal_probability':None,'phenomenon_class':None,'causal_attribution':None,'artifacts':[],
        'limitations':['Observed baselines are not independently verified normal conditions. Baseline MAD bands are not calibrated confidence intervals.',
            'Departures can reflect natural processes, management, seasonality, geometry, noise or product effects; they do not establish a cause.',
            'Assessment counts describe actual observations, not continuous duration between dates.',
            'Local spatial descriptors use surrounding measured cells in the chip and may extend beyond a point selection.',
            'This worker computes physical features. The separately published learned/combined analog comparison is an exploratory experiment, not a global inference service.',
            'Thermal-noise correction, full terrain/SNR validation and seasonal normality are not established.']}
    def finish():
        response=safe_json(result);validate_output(response,'fingerprint')
        (out/'request.json').write_text(json.dumps(request,indent=2));(out/'result.json').write_text(json.dumps(response,indent=2,allow_nan=False))
        return response
    cube=acquire_cube(request,out)
    if cube is None:result['reason']='No compatible measured series brackets both windows';return finish()
    dates=cube['dates'].astype(str)
    has_before=np.any((dates>=baseline['start_date'])&(dates<=baseline['end_date']))
    has_after=np.any((dates>=assessment['start_date'])&(dates<=assessment['end_date']))
    if not has_before or not has_after:result['reason']='Both windows need actual observations';return finish()
    if not ({'HH','HV'}<=set(cube['polarizations']) or {'VV','VH'}<=set(cube['polarizations'])):
        result['reason']='Measured co/cross polarization pair is unavailable';return finish()
    selected=selection_mask(cube,request.get('selection'))
    if not selected.any():result['reason']='Selection is outside the observed grid';return finish()
    data=fingerprint(cube,baseline,assessment);m=data['maps'];support=selected&m['common_support']
    result['quality']={**data['quality'],'selected_grid_cells':int(selected.sum()),'selected_common_cells':int(support.sum()),
        'selection_retained_fraction':float(support.sum()/selected.sum())}
    result['observations']={'dates':data['dates'],'baseline_dates':[data['dates'][i] for i in data['before']],
        'assessment_dates':[data['dates'][i] for i in data['after']],'polarizations':data['polarizations'],
        'grid':{'epsg':int(cube['epsg']),'width':len(cube['x']),'height':len(cube['y']),
                'x_posting_m':float(cube['x'][1]-cube['x'][0]),'y_posting_m':float(cube['y'][0]-cube['y'][1])}}
    if not support.any():result['reason']='No fixed common measured support in the selection';return finish()
    result['status']='complete' if data['quality']['diagnostic_available'] else 'partial'
    result['features']['baseline_state_counts']={label:int((support&(m['state_code']==code)).sum()) for code,label in data['state_legend'].items() if code!=255}
    for name in ['first_scaled_departure','maximum_scaled_departure','departure_frequency','ratio_change_db','local_co_contrast_db','local_co_spatial_mad_change_db']:
        values=m[name][support];values=values[np.isfinite(values)]
        result['features'][name]={'supported_cells':len(values),'median':float(np.median(values)) if len(values) else None,
            'p05':float(np.quantile(values,.05)) if len(values) else None,'p95':float(np.quantile(values,.95)) if len(values) else None}
    ids=sample_blocks(support,request.get('maximum_points',32),2,419) if request.get('maximum_points',32) else np.array([],dtype=int)
    rr,cc=np.unravel_index(ids,support.shape)
    lon,lat=transform(f'EPSG:{int(cube["epsg"])}','EPSG:4326',cube['x'][cc].tolist(),cube['y'][rr].tolist()) if len(ids) else ([],[])
    for j,(r,c) in enumerate(zip(rr,cc)):
        result['points'].append({'row':int(r),'column':int(c),'longitude':lon[j],'latitude':lat[j],**pixel_summary(data,r,c)})
    def register(path,role,units):
        result['artifacts'].append({'id':path.stem,'href':path.name,'role':role,'units':units,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'media_type':'application/x-npz' if path.suffix=='.npz' else 'image/tiff; application=geotiff; profile=cloud-optimized'})
    export={}
    for name,value in m.items():
        a=np.asarray(value)
        if a.dtype==bool:export[name]=a&support if a.ndim==2 else a&support[None]
        elif a.shape[-2:]==support.shape:export[name]=np.where(support,a,np.nan)
    path=out/'fingerprint_arrays.npz';np.savez_compressed(path,**export,x=cube['x'],y=cube['y'],epsg=cube['epsg'],dates=cube['dates'])
    register(path,'full physical descriptors and baseline diagnostic arrays','See feature contract; measured dB, fractions and dimensionless diagnostic scores are separate')
    layers=[('first_change_db','dB'),('maximum_scaled_departure','baseline-scaled units; not sigma or probability'),
        ('departure_frequency','fraction of supported assessment acquisitions'),('ratio_change_db','dB'),
        ('local_co_contrast_db','dB'),('local_co_spatial_mad_change_db','dB'),('state_code','0 insufficient / 1 within / 2 outside; no phenomenon class')]
    for name,units in layers:
        path=out/(name+'.tif');value=np.where(support,m[name],np.nan)
        write_cog(path,value,cube['x'],cube['y'],cube['epsg'],units,angular=name=='state_code')
        register(path,'observed baseline feature map',units)
    return finish()
