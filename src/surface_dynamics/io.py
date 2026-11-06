"""Validated measured cubes and bounded authenticated HDF5 chip access."""
from pathlib import Path
import hashlib,json,os,sys
import numpy as np
import h5py
from rasterio.warp import transform
from .catalog import science_url

POLS=('HH','HV','VV','VH')

def credential():
    token=os.environ.get('EARTHDATA_TOKEN')
    if token:return token.strip()
    protected_path=os.environ.get('EARTHDATA_TOKEN_DPAPI_PATH')
    if protected_path:
        if os.name!='nt':raise RuntimeError('DPAPI credentials are Windows-only; use EARTHDATA_TOKEN')
        import ctypes
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
        data=Path(protected_path).read_bytes()
        if len(data)>16384:raise RuntimeError('Protected credential exceeds expected size')
        buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
        decrypt=ctypes.WinDLL('crypt32',use_last_error=True).CryptUnprotectData
        decrypt.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];decrypt.restype=wintypes.BOOL
        if not decrypt(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise RuntimeError('Current-user protected credential could not be opened')
        try:return ctypes.string_at(target.data,target.size).decode('utf8').strip()
        finally:
            free=ctypes.windll.kernel32.LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p;free(target.data)
    raise RuntimeError('EARTHDATA_TOKEN must be supplied through the worker environment; never through a request')

def load_range_reader():
    # Our audited bounded reader is distributed with this package.
    from .ranges import RangeReader
    return RangeReader

def fetch_chip(entry,longitude,latitude,destination,pixels=512,token=None,*,cache_only=False):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    sidecar=destination.with_suffix('.json')
    if destination.exists() and sidecar.exists():
        meta=json.loads(sidecar.read_text())
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Cached chip checksum mismatch')
        if meta.get('title')==entry['title'] and meta.get('query_point')==[longitude,latitude] and meta.get('requested_pixels')==pixels and meta.get('crop_policy')=='point_centered':return meta
    if cache_only:raise FileNotFoundError('Validated cached chip unavailable')
    reader=load_range_reader();token=token or credential()
    with reader(science_url(entry),token,budget=128*1024*1024) as remote:
        with h5py.File(remote,'r') as h:
            g=h['science/LSAR/GCOV/grids/frequencyA']
            epsg=int(g['projection'][()]);x=g['xCoordinates'][:];y=g['yCoordinates'][:]
            pols=[p for p in POLS if p+p in g]
            if not pols:raise ValueError('No supported measured diagonal power term')
            xx,yy=transform('EPSG:4326',f'EPSG:{epsg}',[longitude],[latitude])
            col=int(np.argmin(abs(x-xx[0])));row=int(np.argmin(abs(y-yy[0])))
            if abs(x[col]-xx[0])>abs(x[1]-x[0]) or abs(y[row]-yy[0])>abs(y[1]-y[0]):raise ValueError('Point outside quantitative grid')
            c0=max(0,min(len(x)-pixels,col-pixels//2))
            r0=max(0,min(len(y)-pixels,row-pixels//2))
            sl=(slice(r0,r0+pixels),slice(c0,c0+pixels))
            arrays={p+p:g[p+p][sl] for p in pols}
            arrays.update(mask=g['mask'][sl],x=x[c0:c0+pixels],y=y[r0:r0+pixels],epsg=epsg)
            if 'numberOfLooks' in g:arrays['numberOfLooks']=g['numberOfLooks'][sl]
            def text_value(ds):
                value=ds[()];return value.decode() if isinstance(value,bytes) else value.item() if hasattr(value,'item') else value
            ident=h['science/LSAR/identification'];flags={k:text_value(ident[k]) for k in ['productVersion','compositeReleaseId','radarBand','instrumentName','hasInputDataException'] if k in ident}
            params=h['science/LSAR/GCOV/metadata/processingInformation/parameters']
            flags.update({k:text_value(params[k]) for k in ['noiseCorrectionApplied','isFullCovariance'] if k in params})
            freq=float(g['centerFrequency'][()]) if 'centerFrequency' in g else None
        meta={'date':entry['time_start'][:10],'title':entry['title'],'source':science_url(entry),'epsg':epsg,'slice':[r0,r0+pixels,c0,c0+pixels],'pols':pols,'center_frequency_hz':freq,'flags':flags,'transfer_bytes':remote.transferred,'query_point':[longitude,latitude],'requested_pixels':pixels,'crop_policy':'point_centered'}
    temp=destination.with_name(destination.stem+'.partial.npz')
    np.savez_compressed(temp,**arrays);temp.replace(destination)
    meta['sha256']=hashlib.sha256(destination.read_bytes()).hexdigest()
    temp=sidecar.with_suffix('.partial.json')
    temp.write_text(json.dumps(meta,indent=2));temp.replace(sidecar)
    return meta

def cube_from_chips(paths,aggregate=4):
    if aggregate<1:raise ValueError('Invalid aggregation')
    loaded=[];metadata=[]
    for path in paths:
        path=Path(path);meta=json.loads(path.with_suffix('.json').read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Chip checksum mismatch')
        loaded.append(np.load(path,allow_pickle=False));metadata.append(meta)
    order=np.argsort([m['date'] for m in metadata]);loaded=[loaded[i] for i in order];metadata=[metadata[i] for i in order]
    if len({m['date'] for m in metadata})!=len(metadata):raise ValueError('Duplicate acquisition dates are not independent observations')
    pols=[p for p in POLS if all(p+p in d for d in loaded)]
    if not pols:raise ValueError('No common measured polarization')
    first=loaded[0];x0,y0=first['x'],first['y'];epsg=int(first['epsg'])
    if len(x0)%aggregate or len(y0)%aggregate:raise ValueError('Chip dimensions must be divisible by aggregation')
    power=[];valid=[]
    for d in loaded:
        if int(d['epsg'])!=epsg or not np.array_equal(d['x'],x0) or not np.array_equal(d['y'],y0):raise ValueError('Incompatible grids; reproject explicitly before combining')
        values=np.stack([d[p+p] for p in pols]);ok=(d['mask']>0)&(d['mask']<255)&np.isfinite(values).all(0)&(values>0).all(0)
        hh,ww=ok.shape;a=aggregate
        # Strict block support avoids turning mixed invalid pixels into synthetic returns.
        out_ok=ok.reshape(hh//a,a,ww//a,a).all(axis=(1,3))
        averaged=np.where(ok[None],values,0).reshape(len(pols),hh//a,a,ww//a,a).mean(axis=(2,4))
        power.append(np.where(out_ok[None],averaged,np.nan));valid.append(np.broadcast_to(out_ok,averaged.shape))
    result={'power':np.stack(power).astype('float32'),'valid':np.stack(valid),'dates':np.array([m['date'] for m in metadata]),'polarizations':np.array(pols),'x':x0.reshape(-1,aggregate).mean(1),'y':y0.reshape(-1,aggregate).mean(1),'epsg':np.array(epsg),'radar_band':np.array('L'),'source_metadata_json':np.array(json.dumps(metadata))}
    validate_cube(result)
    return result

def validate_cube(cube):
    p=np.asarray(cube['power']);v=np.asarray(cube['valid'])
    if p.ndim!=4 or v.shape!=p.shape:raise ValueError('power and valid must have matching [time,channel,y,x] shape')
    if len(cube['dates'])!=p.shape[0] or len(cube['polarizations'])!=p.shape[1]:raise ValueError('Cube coordinate length mismatch')
    if p.shape[0]>128 or p.shape[1]>4 or max(p.shape[2:])>1024:raise ValueError('Cube exceeds bounded analysis limits')
    if p.size>16_777_216:raise ValueError('Cube exceeds 16,777,216 measured channel/pixel/date values; coarsen or split the analysis')
    dates=np.array(cube['dates'],dtype='datetime64[D]')
    if not np.all(np.diff(dates)>np.timedelta64(0,'D')):raise ValueError('Acquisitions must be unique and increasing')
    if not all(str(p) in POLS for p in cube['polarizations']):raise ValueError('Unknown polarization')
    if len(set(map(str,cube['polarizations'])))!=p.shape[1]:raise ValueError('Duplicate polarization labels')
    if len(cube['x'])!=p.shape[3] or len(cube['y'])!=p.shape[2]:raise ValueError('Spatial coordinate length mismatch')
    if not np.all(np.diff(cube['x'])>0) or not np.all(np.diff(cube['y'])<0):raise ValueError('Expected east-positive, north-up projected grid')
    if not np.isfinite(cube['x']).all() or not np.isfinite(cube['y']).all():raise ValueError('Nonfinite spatial coordinates')
    if not np.allclose(np.diff(cube['x']),np.diff(cube['x'])[0]) or not np.allclose(np.diff(cube['y']),np.diff(cube['y'])[0]):raise ValueError('Expected regular spatial grid')
    from rasterio.crs import CRS
    crs=CRS.from_epsg(int(cube['epsg']))
    if not crs.is_projected or crs.linear_units not in ['metre','meter']:raise ValueError('Input grid must use a projected CRS in metres; reproject geographic-degree inputs explicitly')
    if np.any(v & (~np.isfinite(p) | (p<=0))):raise ValueError('Valid power must be finite and positive linear gamma0')
    if str(cube.get('radar_band','L')) not in ['L','S']:raise ValueError('Unknown radar band')
    return cube

def load_cube(path):
    with np.load(path,allow_pickle=False) as handle:cube={k:handle[k] for k in handle.files}
    return validate_cube(cube)


def exclude_empty_acquisitions(cube):
    """Remove dates with zero supported channel/pixels; preserve the QA record.

    This does not reject partial coverage, interpolate dates, or alter observed
    powers. It is intended for preparing model inputs, not editing raw granules.
    """
    power=cube['power'];valid=cube['valid'].astype(bool)&np.isfinite(power)&(power>0)
    keep=valid.any(axis=(1,2,3))
    excluded=[{'date':str(cube['dates'][i]),'reason':'zero supported finite positive channel/pixels'} for i in np.flatnonzero(~keep)]
    result={**cube,'power':power[keep],'valid':np.asarray(cube['valid'])[keep],'dates':np.asarray(cube['dates'])[keep]}
    if 'source_metadata_json' in cube:
        sources=json.loads(str(cube['source_metadata_json']))
        if len(sources)==len(keep):
            for item,i in zip(excluded,np.flatnonzero(~keep)):item['source']=sources[i]
            result['source_metadata_json']=np.array(json.dumps([s for s,ok in zip(sources,keep) if ok]))
    processing=json.loads(str(cube.get('processing_metadata_json','{}')))
    processing['excluded_empty_acquisitions']=processing.get('excluded_empty_acquisitions',[])+excluded
    result['processing_metadata_json']=np.array(json.dumps(processing))
    return result,excluded

def save_cube(cube,path):
    validate_cube(cube);Path(path).parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(path,**cube)
