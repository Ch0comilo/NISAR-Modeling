"""Independent optical vegetation-disturbance evidence, not a fire classifier."""
from pathlib import Path
from datetime import datetime,timedelta
from urllib.parse import urlparse
import hashlib,json,math
import numpy as np
import requests
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject,transform_bounds,Resampling
from rasterio.windows import from_bounds,Window
from .io import credential
from .analysis import measured_db
from .flood_signatures import match_controls,spatial_contrast

COLLECTION='C2746980408-LPCLOUD'
EPOCH=np.datetime64('2020-12-31')
CONFIRMED=(3,6,7,8)
LAYERS=('VEG-DIST-STATUS','VEG-DIST-DATE','VEG-LAST-DATE')


def discover(longitude,latitude,date,days=7):
    center=datetime.fromisoformat(date);lo=center-timedelta(days=days);hi=center+timedelta(days=days)
    r=requests.get('https://cmr.earthdata.nasa.gov/search/granules.json',params={
        'collection_concept_id':COLLECTION,'point':f'{longitude},{latitude}',
        'temporal':lo.strftime('%Y-%m-%dT00:00:00Z')+','+hi.strftime('%Y-%m-%dT23:59:59Z'),
        'page_size':200,'sort_key':'start_date'},timeout=(10,45));r.raise_for_status()
    if int(r.headers.get('CMR-Hits','0'))>200:raise ValueError('Disturbance reference catalog exceeds bounded query')
    unique={}
    for e in r.json()['feed']['entry']:
        parts=e['title'].split('_');key=(parts[3],parts[4],parts[6])
        if key not in unique or e['title']>unique[key]['title']:unique[key]=e
    return {'query_url':r.url,'entries':list(unique.values())}


def fetch_layer(entry,layer,directory,token=None):
    if layer not in LAYERS or not entry['title'].startswith('OPERA_L3_DIST-ALERT-HLS_') or not entry['title'].endswith('_v1'):
        raise ValueError('Only the documented optical DIST-ALERT-HLS V1 layers are admitted')
    urls=[x['href'] for x in entry['links'] if x.get('href','').endswith('_'+layer+'.tif')
          and urlparse(x['href']).scheme=='https' and urlparse(x['href']).hostname=='data.lpdaac.earthdatacloud.nasa.gov']
    if not urls:raise ValueError('Official quantitative optical disturbance layer is absent')
    url=urls[0];directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(entry['title']+'_'+layer+'.tif');sidecar=path.with_suffix('.json')
    if path.exists() and sidecar.exists():
        meta=json.loads(sidecar.read_text())
        if meta['source']!=url or meta['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Disturbance cache identity/checksum mismatch')
        return path,meta
    try:
        with requests.get(url,headers={'Authorization':'Bearer '+(token or credential())},stream=True,timeout=(10,45)) as r:
            if r.status_code!=200:raise RuntimeError(f'Optical disturbance archive returned HTTP {r.status_code}')
            data=bytearray()
            for chunk in r.iter_content(1024*1024):
                if len(data)+len(chunk)>12*1024*1024:raise ValueError('Disturbance layer exceeds bounded transfer budget')
                data.extend(chunk)
    except requests.RequestException:raise RuntimeError('Disturbance archive request failed; signed/authentication URLs are not logged') from None
    with rasterio.io.MemoryFile(bytes(data)) as memory:
        with memory.open() as ds:
            if ds.count!=1 or abs(ds.transform.a)!=30 or ds.width!=3660:raise ValueError('Unexpected native optical disturbance grid')
            meta={'source_family':'OPERA_DIST_HLS','source_id':entry['title'],'source':url,
                  'observation_time':entry['time_start'],'layer':layer,'tags':ds.tags(),
                  'native_posting_m':30,'crs':str(ds.crs),'sha256':hashlib.sha256(data).hexdigest()}
    temp=path.with_name(path.name+'.partial');temp.write_bytes(data);temp.replace(path)
    sidecar.write_text(json.dumps(meta,indent=2));return path,meta


def status_masks(status,onset,last,assessment_start,assessment_end,sar_date,max_days=7):
    if not set(np.unique(status)).issubset(set(range(9))|{255}):raise ValueError('Unknown optical disturbance status codes; review product version')
    day=int((np.datetime64(sar_date)-EPOCH)/np.timedelta64(1,'D'))
    start=int((np.datetime64(assessment_start)-EPOCH)/np.timedelta64(1,'D'))
    end=int((np.datetime64(assessment_end)-EPOCH)/np.timedelta64(1,'D'))
    recent=(last>0)&(abs(last.astype('int64')-day)<=max_days)
    valid=(status!=255)&recent
    within=(onset>=start)&(onset<=end)&(onset>0)&(onset<=last)
    confirmed=np.isin(status,CONFIRMED)
    return {'valid_coverage':valid,'confirmed_loss':valid&confirmed&within,
            'provisional_loss':valid&np.isin(status,[1,2,4,5])&within,
            'non_disturbed':valid&(status==0),
            'confirmed_outside_window':valid&confirmed&~within}


def align_layers(paths,cube,assessment_start,assessment_end,sar_date):
    x,y=cube['x'],cube['y'];dx=float(x[1]-x[0]);dy=float(y[0]-y[1]);epsg=int(cube['epsg'])
    if min(dx,dy)<30:raise ValueError('No invented optical detail below native 30 m posting')
    arrays={};grid=None
    for layer,path in paths.items():
        with rasterio.open(path) as ds:
            current=(str(ds.crs),ds.transform,ds.shape)
            if grid is not None and current!=grid:raise ValueError('Optical disturbance source layers must have the same native grid')
            grid=current
            bounds=transform_bounds(f'EPSG:{epsg}',ds.crs,x[0]-dx/2,y[-1]-dy/2,x[-1]+dx/2,y[0]+dy/2,densify_pts=21)
            win=from_bounds(*bounds,transform=ds.transform);r0=math.floor(win.row_off)-1;c0=math.floor(win.col_off)-1
            h=math.ceil(win.row_off+win.height)-r0+1;w=math.ceil(win.col_off+win.width)-c0+1
            if max(h,w)>2048:raise ValueError('Bounded disturbance crop exceeds spatial budget')
            window=Window(c0,r0,w,h)
            arrays[layer]=ds.read(1,window=window,boundless=True,fill_value=255 if layer=='VEG-DIST-STATUS' else -1)
            source_transform=ds.window_transform(window);source_crs=ds.crs
    masks=status_masks(arrays['VEG-DIST-STATUS'],arrays['VEG-DIST-DATE'],arrays['VEG-LAST-DATE'],assessment_start,assessment_end,sar_date)
    outputs={};target=from_origin(x[0]-dx/2,y[0]+dy/2,dx,dy)
    for key,value in masks.items():
        dest=np.zeros((len(y),len(x)),dtype='float32')
        reproject(value.astype('float32'),dest,src_transform=source_transform,src_crs=source_crs,
                  dst_transform=target,dst_crs=f'EPSG:{epsg}',src_nodata=None,dst_nodata=None,
                  resampling=Resampling.average,num_threads=1)
        outputs[key]=np.clip(dest,0,1)
    coverage=outputs['valid_coverage']
    for key in list(outputs):
        if key!='valid_coverage':outputs[key+'_fraction']=np.divide(outputs.pop(key),coverage,out=np.full_like(coverage,np.nan),where=coverage>0)
    return outputs


def prepare_disturbance(cube,longitude,latitude,assessment,sar_date,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    catalog=discover(longitude,latitude,sar_date);(output.parent/'disturbance_catalog.json').write_text(json.dumps(catalog,indent=2))
    target=np.datetime64(sar_date);entries=sorted(catalog['entries'],key=lambda e:(abs(int((np.datetime64(e['time_start'][:10])-target)/np.timedelta64(1,'D'))),e['title']))[:16]
    candidates=[];errors=[]
    for e in entries:
        try:
            fetched={key:fetch_layer(e,key,output.parent/'disturbance_raw') for key in LAYERS}
            arrays=align_layers({k:v[0] for k,v in fetched.items()},cube,assessment['start_date'],assessment['end_date'],sar_date)
            coverage=float((arrays['valid_coverage']>=.8).mean())
            offset=abs(int((np.datetime64(e['time_start'][:10])-target)/np.timedelta64(1,'D')))
            candidates.append((coverage,offset,e['title'],arrays,{k:v[1] for k,v in fetched.items()}))
        except (ValueError,RuntimeError) as error:errors.append({'source_id':e['title'],'reason':str(error)})
    if not candidates:return {'status':'unavailable','candidate_errors':errors}
    adequate=[c for c in candidates if c[0]>=.5]
    selected=min(adequate,key=lambda c:(c[1],-c[0],c[2])) if adequate else max(candidates,key=lambda c:(c[0],-c[1],c[2]))
    coverage,offset,title,arrays,metadata=selected
    arrays.update(x=cube['x'],y=cube['y'],epsg=cube['epsg'],source_family=np.array('OPERA_DIST_HLS'),
                  sar_date=np.array(sar_date),observation_date=np.array(next(iter(metadata.values()))['observation_time'][:10]),
                  assessment_start=np.array(assessment['start_date']),assessment_end=np.array(assessment['end_date']))
    np.savez_compressed(output,**arrays)
    report={'status':'prepared','source_family':'OPERA_DIST_HLS','source_id':title,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'valid_analysis_cell_fraction':coverage,
            'optical_offset_days':offset,'source_layers':metadata,'candidate_errors':errors,
            'selection_policy':'Nearest date among >=50% recent usable-cell candidates; otherwise highest recent usable coverage; no radar-agreement criterion',
            'semantics':'Source-class shares conditioned on recent usable optical coverage; confirmed vegetation loss is not a wildfire cause or burned-area label'}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2));return report


def summarize_disturbance(path,cube,support,baseline,assessment):
    path=Path(path);meta=json.loads(path.with_suffix('.json').read_text())
    if meta['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Disturbance reference checksum mismatch')
    with np.load(path,allow_pickle=False) as h:r={k:h[k] for k in h.files}
    if str(r['source_family'])!='OPERA_DIST_HLS':raise ValueError('Independent optical DIST-HLS evidence is required')
    if int(r['epsg'])!=int(cube['epsg']) or any(not np.array_equal(r[k],cube[k]) for k in ['x','y']):raise ValueError('Disturbance reference grid mismatch')
    if str(r['assessment_start'])!=assessment['start_date'] or str(r['assessment_end'])!=assessment['end_date']:raise ValueError('Disturbance reference assessment window mismatch')
    for key in ['valid_coverage','confirmed_loss_fraction','non_disturbed_fraction']:
        v=r[key]
        if v.shape!=support.shape or np.any(np.isfinite(v)&((v<0)|(v>1+1e-5))):raise ValueError('Invalid disturbance support fractions')
    sar=str(r['sar_date']);optical=str(r['observation_date']);offset=abs(int((np.datetime64(sar)-np.datetime64(optical))/np.timedelta64(1,'D')))
    if offset>7 or sar not in cube['dates'] or not assessment['start_date']<=sar<=assessment['end_date']:raise ValueError('Disturbance reference lacks corresponding observed SAR/temporal support')
    valid=support&(r['valid_coverage']>=.8);loss=valid&(r['confirmed_loss_fraction']>=.8);control=valid&(r['non_disturbed_fraction']>=.8)
    db=measured_db(cube);dates=cube['dates'].astype(str)
    pre=np.flatnonzero((dates>=baseline['start_date'])&(dates<=baseline['end_date']));idx=int(np.flatnonzero(dates==sar)[0])
    pols=list(map(str,cube['polarizations']));co=pols.index('HH') if 'HH' in pols else pols.index('VV')
    cross_name='HV' if pols[co]=='HH' else 'VH';channels=[co]+([pols.index(cross_name)] if cross_name in pols else [])
    base=np.full(db.shape[1:],np.nan);base[:,support]=np.median(db[pre][:,:,support],axis=0)
    controls,matched,matching=match_controls(base[channels],loss,control)
    delta=db[idx,co]-base[co];contrast=spatial_contrast(delta,matched,controls)
    return {'kind':'independent_optical_vegetation_disturbance','status':'usable' if int(valid.sum())>=8 else 'insufficient_support',
            'source_id':meta['source_id'],'reference_sha256':meta['sha256'],'provenance':meta,
            'comparison_sar_date':sar,'optical_date':optical,'optical_offset_days':offset,
            'recent_optical_supported_cells':int(valid.sum()),'fraction_of_fixed_radar_support':float(valid.sum()/support.sum()) if support.any() else None,
            'confirmed_in_window_loss_cells':int(loss.sum()),'reference_non_disturbed_cells':int(control.sum()),
            'median_loss_group_co_pol_change_db':float(np.median(delta[loss])) if loss.any() else None,
            'matched_controls':matching,'matched_contrast':contrast,
            'wildfire_attribution':None,'burned_area_ha':None,
            'limitation':'Optical vegetation loss can arise from fire, harvest, clearing, drought or other processes. Recent-observation and onset-window gates exclude stale/old disturbances; no fire perimeter validation is supplied.'}
