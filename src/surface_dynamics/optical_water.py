"""Independent OPERA DSWx-HLS reference discovery and conservative class support.

WTR 0/1/2 are nonwater/open-water/partial-water classes. 252..255 are invalid
for a binary water comparison. Fractions below describe source *class cells*,
not a retrieved subpixel water fraction or independently surveyed ground truth.
"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
import hashlib,json,math
from time import monotonic
import numpy as np
import requests
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject,transform_bounds,Resampling
from rasterio.windows import from_bounds,Window
from .io import credential

COLLECTION='C2617126679-POCLOUD'
VALID_CODES=(0,1,2)
KNOWN_CODES={0,1,2,252,253,254,255}


def discover(longitude,latitude,date,max_offset_days=7,*,timeout=(10,45)):
    center=datetime.fromisoformat(str(date)[:10]);start=center-timedelta(days=max_offset_days);end=center+timedelta(days=max_offset_days)
    response=requests.get('https://cmr.earthdata.nasa.gov/search/granules.json',params={
        'collection_concept_id':COLLECTION,'point':f'{longitude},{latitude}',
        'temporal':start.strftime('%Y-%m-%dT00:00:00Z')+','+end.strftime('%Y-%m-%dT23:59:59Z'),
        'page_size':200,'sort_key':'start_date'},timeout=timeout)
    response.raise_for_status()
    if int(response.headers.get('CMR-Hits','0'))>200:raise ValueError('Narrow optical reference dates; catalog is not silently truncated')
    entries={}
    for e in response.json()['feed']['entry']:
        parts=e['title'].split('_')
        if len(parts)<7:continue
        identity=(parts[3],parts[4],parts[6])
        if identity not in entries or e['title']>entries[identity]['title']:entries[identity]=e
    return {'query_url':response.url,'queried_at_utc':datetime.now(timezone.utc).isoformat(),'entries':list(entries.values())}


def asset_url(entry):
    for link in entry['links']:
        url=link.get('href','')
        if url.endswith('_B01_WTR.tif') and urlparse(url).hostname=='archive.podaac.earthdata.nasa.gov' and url.startswith('https://'):return url
    raise ValueError('No official quantitative DSWx-HLS WTR asset')


def fetch_asset(entry,directory,token=None,budget=16*1024*1024,*,timeout=(10,45),deadline=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    url=asset_url(entry);title=entry['title']
    if not title or not all(c.isascii() and (c.isalnum() or c in '_.-') for c in title):
        raise ValueError('Unexpected optical granule title')
    path=directory/(title+'_B01_WTR.tif');sidecar=path.with_suffix('.json')
    if path.exists() and sidecar.exists():
        meta=json.loads(sidecar.read_text())
        if meta['source']==url and hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']:return path,meta
        raise ValueError('Optical reference cache identity/checksum mismatch')
    try:
        with requests.get(url,headers={'Authorization':'Bearer '+(token or credential())},stream=True,timeout=timeout) as response:
            if response.status_code!=200:raise RuntimeError(f'Optical archive returned HTTP {response.status_code}')
            data=bytearray()
            for chunk in response.iter_content(1024*1024):
                if deadline is not None and monotonic() >= deadline:
                    raise TimeoutError('Optical evidence deadline elapsed')
                if len(data)+len(chunk)>budget:raise ValueError('Optical asset exceeds bounded transfer budget')
                data.extend(chunk)
    except requests.RequestException:
        raise RuntimeError('Optical archive request failed; authentication/redirect values are not logged') from None
    with rasterio.io.MemoryFile(bytes(data)) as memory:
        with memory.open() as ds:
            tags=ds.tags()
            if tags.get('PRODUCT_SOURCE')!='HLS' or tags.get('PRODUCT_TYPE')!='DSWx-HLS':raise ValueError('Reference is not an independent optical DSWx-HLS product')
            meta={'source_family':'OPERA_DSWX_HLS','source_id':entry['title'],'source':url,
                  'observation_time':tags.get('SENSING_TIME',entry['time_start']),
                  'crs':str(ds.crs),'native_posting_m':abs(ds.transform.a),'tags':tags,
                  'reference_kind':'independent_optical_classification_product_not_ground_truth',
                  'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    temp=path.with_name(path.name+'.partial');temp.write_bytes(data);temp.replace(path)
    sidecar.write_text(json.dumps(meta,indent=2));return path,meta


def align_classes(path,cube):
    x=np.asarray(cube['x']);y=np.asarray(cube['y']);dx=float(x[1]-x[0]);dy=float(y[0]-y[1]);epsg=int(cube['epsg'])
    if min(dx,dy)<30:raise ValueError('Optical class fractions require a grid at least as coarse as the 30 m reference; no invented fine detail')
    target=from_origin(x[0]-dx/2,y[0]+dy/2,dx,dy)
    with rasterio.open(path) as ds:
        bounds=transform_bounds(f'EPSG:{epsg}',ds.crs,x[0]-dx/2,y[-1]-dy/2,x[-1]+dx/2,y[0]+dy/2,densify_pts=21)
        win=from_bounds(*bounds,transform=ds.transform)
        r0=math.floor(win.row_off)-1;c0=math.floor(win.col_off)-1
        height=math.ceil(win.row_off+win.height)-r0+1;width=math.ceil(win.col_off+win.width)-c0+1
        if max(height,width)>2048:raise ValueError('Reference crop exceeds bounded spatial budget')
        window=Window(c0,r0,width,height);classes=ds.read(1,window=window,boundless=True,fill_value=255)
        if not set(np.unique(classes)).issubset(KNOWN_CODES):raise ValueError('Unrecognized DSWx-HLS class values; review product version')
        transform=ds.window_transform(window);valid=np.isin(classes,VALID_CODES)
        outputs={}
        for key,source in [('valid_coverage',valid),('open_class_support',classes==1),('partial_class_support',classes==2)]:
            dest=np.zeros((len(y),len(x)),dtype='float32')
            # Invalid/cloud/fill pixels contribute zero to coverage, rather than
            # disappearing from a nodata-normalized denominator.
            reproject(source.astype('float32'),dest,src_transform=transform,src_crs=ds.crs,
                      dst_transform=target,dst_crs=f'EPSG:{epsg}',src_nodata=None,dst_nodata=None,
                      resampling=Resampling.average,num_threads=1)
            outputs[key]=np.clip(dest,0,1)
    coverage=outputs['valid_coverage']
    for key in ['open','partial']:
        outputs[key+'_class_fraction']=np.divide(outputs.pop(key+'_class_support'),coverage,out=np.full_like(coverage,np.nan),where=coverage>0)
    return outputs


def select_reference(catalog,cube,date,directory,max_candidates=8,min_coverage=.8,*,
                     support=None,accept_sensing_day=None,timeout=(10,45),deadline=None):
    target=np.datetime64(str(date)[:10]);entries=sorted(catalog['entries'],key=lambda e:(abs(int((np.datetime64(e['time_start'][:10])-target)/np.timedelta64(1,'D'))),e['title']))[:max_candidates]
    candidates=[];errors=[]
    for entry in entries:
        if deadline is not None and monotonic() >= deadline:
            break
        try:
            path,meta=fetch_asset(entry,directory,timeout=timeout,deadline=deadline)
            sensing_day=meta['observation_time'][:10]
            if accept_sensing_day is not None and not accept_sensing_day(sensing_day):
                continue
            aligned=align_classes(path,cube)
            valid=aligned['valid_coverage']>=min_coverage
            coverage=float((valid if support is None else valid[support]).mean())
            offset=abs(int((np.datetime64(sensing_day)-target)/np.timedelta64(1,'D')))
            candidates.append((coverage,offset,entry['title'],aligned,meta))
        except (RuntimeError,ValueError,OSError) as error:
            errors.append({'source_id':entry['title'],'reason':str(error)})
    if not candidates:return None,{'status':'unavailable','candidate_errors':errors}
    # Coverage is the only image-quality criterion. Never select an optical
    # product by agreement with the SAR response being evaluated.
    adequate=[c for c in candidates if c[0]>=.5]
    chosen=min(adequate,key=lambda c:(c[1],-c[0],c[2])) if adequate else max(candidates,key=lambda c:(c[0],-c[1],c[2]))
    coverage,offset,_,aligned,meta=chosen
    selection={'status':'selected','valid_analysis_cell_fraction':coverage,'absolute_date_offset_days':offset,
               'candidate_count':len(candidates),'selection_policy':'nearest date among >=50% usable-cell candidates; otherwise highest usable coverage; no SAR agreement criterion',
               'candidate_errors':errors,'source':meta}
    return aligned,selection


def prepare_reference_pair(cube,longitude,latitude,pre_sar_date,post_sar_date,output,max_offset_days=7,max_candidates=8,boundary_date=None):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);arrays={};metadata={}
    for epoch,date in [('pre',pre_sar_date),('post',post_sar_date)]:
        catalog=discover(longitude,latitude,date,max_offset_days)
        if boundary_date:
            catalog['entries']=[e for e in catalog['entries'] if (e['time_start'][:10]<boundary_date if epoch=='pre' else e['time_start'][:10]>=boundary_date)]
            catalog['epoch_constraint']='pre before assessment start; post on/after assessment start'
        (output.parent/(epoch+'_optical_catalog.json')).write_text(json.dumps(catalog,indent=2))
        aligned,selection=select_reference(catalog,cube,date,output.parent/'optical_raw',max_candidates)
        metadata[epoch]=selection
        if aligned is None:return {'status':'unavailable','epochs':metadata}
        for key,value in aligned.items():arrays[epoch+'_'+key]=value
        arrays[epoch+'_date']=np.array(selection['source']['observation_time'][:10])
        arrays[epoch+'_sar_date']=np.array(str(date)[:10])
    arrays.update(x=cube['x'],y=cube['y'],epsg=cube['epsg'],source_family=np.array('OPERA_DSWX_HLS'),source_metadata_json=np.array(json.dumps(metadata)))
    np.savez_compressed(output,**arrays)
    provenance={'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'source_family':'OPERA_DSWX_HLS','epochs':metadata,
                'class_semantics':'Fractions of valid source cells assigned a class; partial water is not counted as full water and is excluded from strict binary comparison.'}
    output.with_suffix('.json').write_text(json.dumps(provenance,indent=2));return {'status':'prepared','epochs':{k:{a:v[a] for a in ['status','valid_analysis_cell_fraction','absolute_date_offset_days']} for k,v in metadata.items()}}
