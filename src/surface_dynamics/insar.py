"""Actual GUNW phase/coherence, with component and correction-state gates."""
from pathlib import Path
import json,re,hashlib
import h5py
import numpy as np
from rasterio.warp import transform
from .catalog import science_url
from .io import credential
from .ranges import RangeReader

class NoCoherentSupport(ValueError):
    """Valid measurement file, but no supported component at this threshold."""


def validate_pair(pair,require_wrapped=True):
    """Check native grids without resampling wrapped/unwrapped measurements."""
    from rasterio.crs import CRS
    required=['unwrappedPhase','unwrapped_coherence','unwrapped_mask','connectedComponents','center_frequency_hz']
    if require_wrapped:required+=['wrapped_complex','wrapped_coherence','wrapped_mask']
    for key in required:
        if key not in pair:raise ValueError(f'Missing measured GUNW field: {key}')
    frequency=float(pair['center_frequency_hz'])
    if not np.isfinite(frequency) or frequency<=0:raise ValueError('Invalid measured center frequency')
    grids=[('unwrapped','unwrappedPhase',['unwrapped_coherence','unwrapped_mask','connectedComponents'])]
    if require_wrapped:grids.append(('wrapped','wrapped_complex',['wrapped_coherence','wrapped_mask']))
    for prefix,field,fields in grids:
        shape=pair[field].shape
        if len(shape)!=2 or min(shape)<2 or max(shape)>1536:raise ValueError('Invalid or oversized GUNW raster shape')
        if any(pair[key].shape!=shape for key in fields):raise ValueError('Native GUNW raster shapes do not match')
        x=np.asarray(pair[prefix+'_x']);y=np.asarray(pair[prefix+'_y'])
        if x.shape!=(shape[1],) or y.shape!=(shape[0],):raise ValueError('GUNW coordinate shape mismatch')
        if not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('GUNW coordinates must be finite')
        dx=np.diff(x);dy=np.diff(y)
        if not (dx>0).all() or not (dy<0).all() or not np.allclose(dx,dx[0]) or not np.allclose(dy,dy[0]):raise ValueError('GUNW needs regular east-positive north-up coordinates')
        crs=CRS.from_epsg(int(pair[prefix+'_epsg']))
        if not crs.is_projected or crs.linear_units!='metre':raise ValueError('GUNW grid must use projected metres')
        if not np.issubdtype(pair[prefix+'_mask'].dtype,np.integer):raise ValueError('GUNW mask must preserve integer bit fields')
        c=pair[prefix+'_coherence'];support=valid_gunw_mask(pair[prefix+'_mask'])&np.isfinite(c)
        if ((c[support]<0)|(c[support]>1+1e-5)).any():raise ValueError('Coherence outside measured [0,1] domain')
    if require_wrapped and int(pair['wrapped_epsg'])!=int(pair['unwrapped_epsg']):raise ValueError('Native GUNW grids must share a CRS')
    if not np.issubdtype(pair['connectedComponents'].dtype,np.integer):raise ValueError('Connected components must be integer labels')
    for key in ['ionospherePhaseScreen','ionospherePhaseScreenUncertainty']:
        if key in pair and pair[key].shape!=pair['unwrappedPhase'].shape:raise ValueError('Ionosphere layer grid mismatch')
    return pair

def select_event_pair(entries,event_date):
    candidates=[]
    for e in entries:
        timestamps=re.findall(r'(\d{8}T\d{6})',e['title'])
        if len(timestamps)<4:continue
        dates=sorted([timestamps[0],timestamps[2]])
        a,b=[np.datetime64(f'{d[:4]}-{d[4:6]}-{d[6:8]}') for d in dates]
        if a<np.datetime64(event_date)<b:candidates.append((int((b-a)/np.timedelta64(1,'D')),e))
    return min(candidates,key=lambda p:(p[0],p[1]['title']))[1] if candidates else None

def valid_gunw_mask(mask):
    m=mask.astype('uint32');sub=m&255
    return (sub!=255)&(sub%10>0)&((sub//10)%10>0)&((m&0x00ffff00)==0)

def fetch_pair(entry,longitude,latitude,destination,extent_km=20,token=None,unwrapped_only=False):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists() and destination.with_suffix('.json').exists():
        metadata=json.loads(destination.with_suffix('.json').read_text())
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=metadata['sha256']:raise ValueError('Cached GUNW checksum mismatch')
        if metadata.get('title')==entry['title'] and metadata.get('longitude')==longitude and metadata.get('latitude')==latitude and metadata.get('extent_km')==extent_km and metadata.get('crop_policy')=='point_centered' and (unwrapped_only or metadata.get('quantitative_layers')!='unwrapped_only'):return destination
    arrays={};meta={'title':entry['title'],'source':science_url(entry),'longitude':longitude,'latitude':latitude,'extent_km':extent_km,'crop_policy':'point_centered','quantitative_layers':'unwrapped_only' if unwrapped_only else 'wrapped_and_unwrapped'}
    with RangeReader(science_url(entry),token or credential(),budget=180*1024*1024) as remote:
        with h5py.File(remote,'r') as h:
            root=h['science/LSAR/GUNW/grids/frequencyA'];pols=[p.decode() for p in root['listOfPolarizations'][:]];pol=next((p for p in ['HH','VV'] if p in pols),None)
            if pol is None:raise ValueError('No measured co-polarized interferogram')
            arrays['center_frequency_hz']=root['centerFrequency'][()]
            layers=[('unwrappedInterferogram','unwrapped')] if unwrapped_only else [('wrappedInterferogram','wrapped'),('unwrappedInterferogram','unwrapped')]
            for kind,prefix in layers:
                g=root[kind];pg=g[pol];x=g['xCoordinates'][:];y=g['yCoordinates'][:];epsg=int(g['projection'][()]);dx=abs(float(x[1]-x[0]));n=int(round(extent_km*1000/dx))
                if n>1536:raise ValueError('GUNW scratch extent exceeds bounded raster budget')
                xx,yy=transform('EPSG:4326',f'EPSG:{epsg}',[longitude],[latitude]);col=int(np.argmin(abs(x-xx[0])));row=int(np.argmin(abs(y-yy[0])))
                if abs(x[col]-xx[0])>dx or abs(y[row]-yy[0])>dx:raise ValueError('Query point outside GUNW quantitative grid')
                r0=max(0,min(len(y)-n,row-n//2));c0=max(0,min(len(x)-n,col-n//2));sl=(slice(r0,r0+n),slice(c0,c0+n))
                arrays[prefix+'_x']=x[c0:c0+n];arrays[prefix+'_y']=y[r0:r0+n];arrays[prefix+'_epsg']=epsg
                arrays[prefix+'_mask']=g['mask'][sl];arrays[prefix+'_coherence']=pg['coherenceMagnitude'][sl]
                if prefix=='wrapped':arrays['wrapped_complex']=pg['wrappedInterferogram'][sl]
                else:
                    for key in ['unwrappedPhase','connectedComponents','ionospherePhaseScreen','ionospherePhaseScreenUncertainty']:
                        if key in pg:arrays[key]=pg[key][sl]
            idg=h['science/LSAR/identification']
            def value(ds):
                x=ds[()];return x.decode() if isinstance(x,bytes) else x.item() if hasattr(x,'item') else str(x)
            meta['identification']={k:value(ds) for k,ds in idg.items() if isinstance(ds,h5py.Dataset) and ds.size==1 and any(s in k.lower() for s in ['time','fullframe','urgent','exception','orbit','track','frame','band','version'])}
            meta['polarization']=pol;meta['corrections_applied_by_this_pipeline']=[]
        meta['transfer_bytes']=remote.transferred
    validate_pair(arrays,require_wrapped=not unwrapped_only)
    temp=destination.with_name(destination.stem+'.partial.npz');np.savez_compressed(temp,**arrays);temp.replace(destination)
    meta['sha256']=hashlib.sha256(destination.read_bytes()).hexdigest();destination.with_suffix('.json').write_text(json.dumps(meta,indent=2))
    return destination

def analyze_pair(pair,coherence_threshold=.35):
    if not np.isfinite(coherence_threshold) or not 0<=coherence_threshold<=1:raise ValueError('Invalid coherence threshold')
    wrapped=pair['wrapped_complex']
    if wrapped.dtype.fields:
        keys=list(wrapped.dtype.fields);wrapped=wrapped[keys[0]].astype(float)+1j*wrapped[keys[1]].astype(float)
    phase=np.angle(wrapped);cw=pair['wrapped_coherence'];cu=pair['unwrapped_coherence'];u=pair['unwrappedPhase'];cc=pair['connectedComponents']
    vw=valid_gunw_mask(pair['wrapped_mask'])&np.isfinite(phase)&np.isfinite(cw)&(abs(wrapped)>0)&(cw>=0)&(cw<=1+1e-5)
    base=valid_gunw_mask(pair['unwrapped_mask'])&np.isfinite(u)&np.isfinite(cu)&(cc>0)&(cu>=0)&(cu<=1+1e-5)
    vu=base&(cu>=coherence_threshold)
    ids,counts=np.unique(cc[vu],return_counts=True)
    if not len(ids):raise NoCoherentSupport('No coherent connected unwrapped support at the requested threshold')
    component=int(ids[np.argmax(counts)]);chosen=vu&(cc==component)
    datum=float(np.median(u[chosen]));referenced=np.where(chosen,u-datum,np.nan)
    wavelength=299792458/float(pair['center_frequency_hz'])
    ionofilled=(pair['unwrapped_mask'].astype('uint32')&(1<<24))!=0
    sweep=[]
    for threshold in sorted(set([.2,.35,.5,.7,float(coherence_threshold)])):
        ok=base&(cu>=threshold);labels,n=np.unique(cc[ok],return_counts=True)
        sweep.append({'coherence_threshold':threshold,'valid_fraction':float(ok.mean()),
                      'connected_components':len(labels),'largest_component_fraction':float(n.max()/ok.size) if len(n) else 0.,
                      'valid_ionosphere_unfilled_fraction':float((ok&~ionofilled).mean())})
    quality={'threshold_sensitivity':sweep,'unwrapped_component_count_at_threshold':len(ids),
             'selected_component_ionosphere_filled_fraction':float(ionofilled[chosen].mean()),
             'unwrapped_valid_fraction_at_threshold':float(vu.mean()),
             'wrapped_coherence_quantiles':dict(zip(['q05','q25','q50','q75','q95'],map(float,np.quantile(cw[vw],[.05,.25,.5,.75,.95])))) if vw.any() else None,
             'interpretation':'Coherence and connected support control observability, not earthquake likelihood or surface safety.'}
    return {'wrapped_phase_rad':np.where(vw&(cw>=coherence_threshold),phase,np.nan),'coherence20':np.where(vw,cw,np.nan),'referenced_unwrapped_rad':referenced,'phase_equivalent_cm':referenced*wavelength/(4*np.pi)*100,'component_valid':chosen,'ionosphere_filled':ionofilled,'metadata':{'coherence_threshold':coherence_threshold,'selected_component':component,'display_reference':'median of coherent selected component; stability is not established','datum_phase_radians':datum,'selected_fraction':float(chosen.mean()),'median_coherence':float(np.nanmedian(np.where(vw,cw,np.nan))) if vw.any() else None,'ionosphere_filled_fraction':float(ionofilled.mean()),'wavelength_m':wavelength,'one_fringe_phase_equivalent_cm':wavelength*50,'phase_equivalent_sign':'positive referenced interferometric phase; not validated toward/away sign','los_displacement_status':'unavailable_uncorrected_phase_and_unverified_stable_reference','corrections_applied':[],'observability':quality,'interpretation':'Phase-equivalent range scale is for teaching wavelength sensitivity. It includes atmospheric/ionospheric/unwrapping contributions and is not a deformation estimate.'}}
