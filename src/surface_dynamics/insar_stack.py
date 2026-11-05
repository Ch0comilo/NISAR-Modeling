"""Observed multi-pair coherence and common-reference phase, never displacement."""
from pathlib import Path
import hashlib
import json
import numpy as np
import xarray as xr
import rasterio
from .catalog import search, group_entries
from .insar import fetch_pair, validate_pair, valid_gunw_mask
from .network import acquisition_dates


def analyze_stack(pairs,metadata,coherence_threshold=.35):
    if not np.isfinite(coherence_threshold) or not 0<=coherence_threshold<=1:raise ValueError('Coherence threshold must be finite in [0,1]')
    if not 2<=len(pairs)<=6 or len(metadata)!=len(pairs):raise ValueError('Stack requires two to six measured pairs with provenance')
    for pair in pairs:validate_pair(pair,require_wrapped=False)
    groups=group_entries([{'title':m['title'],'time_start':acquisition_dates(m['title'])[0]} for m in metadata],'GUNW')
    if len(groups)!=1:raise ValueError('InSAR stack must retain a single track/pass/frame/bandwidth/polarization series')
    if len({tuple(sorted(acquisition_dates(m['title']))) for m in metadata})!=len(metadata):raise ValueError('Duplicate day-pair revisions are not independent intervals')
    if len({m.get('polarization') for m in metadata})!=1 or not all(m.get('polarization') in ['HH','VV'] for m in metadata):raise ValueError('Stack needs the same named measured co-polarization')
    epsg=int(pairs[0]['unwrapped_epsg'])
    if any(int(p['unwrapped_epsg'])!=epsg for p in pairs):raise ValueError('Stack grids must share a projected CRS')
    spacing=float(pairs[0]['unwrapped_x'][1]-pairs[0]['unwrapped_x'][0])
    if any(not np.isclose(p['unwrapped_x'][1]-p['unwrapped_x'][0],spacing) or not np.isclose(p['unwrapped_y'][0]-p['unwrapped_y'][1],spacing) for p in pairs):raise ValueError('Stack needs identical native posting; no implicit resampling')
    x=pairs[0]['unwrapped_x'];y=pairs[0]['unwrapped_y']
    for pair in pairs[1:]:
        x=np.intersect1d(x,pair['unwrapped_x']);y=np.intersect1d(y,pair['unwrapped_y'])
    y=np.sort(y)[::-1]
    if len(x)<2 or len(y)<2:raise ValueError('No overlapping native pixel centers; explicit reprojection is required')
    coherence=[];phase=[];component_masks=[];ionosphere=[];observations=[];common_data=[]
    order=sorted(range(len(pairs)),key=lambda i:acquisition_dates(metadata[i]['title']))
    for i in order:
        pair=pairs[i];source=metadata[i]
        cols=np.searchsorted(pair['unwrapped_x'],x);rows=np.searchsorted(-pair['unwrapped_y'],-y);index=np.ix_(rows,cols)
        c=pair['unwrapped_coherence'][index];u=pair['unwrappedPhase'][index]
        cc=pair['connectedComponents'][index];mask=pair['unwrapped_mask'][index]
        data=valid_gunw_mask(mask)&np.isfinite(c)&(c>=0)&(c<=1+1e-5)
        valid=data&np.isfinite(u)&(cc>0)&(c>=coherence_threshold)
        labels,counts=np.unique(cc[valid],return_counts=True)
        selected=int(labels[np.argmax(counts)]) if len(labels) else None
        support=valid&(cc==selected) if selected is not None else np.zeros_like(valid)
        coherence.append(np.where(data,c,np.nan));phase.append(u);component_masks.append(support);common_data.append(data)
        filled=(mask.astype('uint32')&(1<<24))!=0;ionosphere.append(filled)
        reference,secondary=acquisition_dates(source['title'])
        interval=abs(int((np.datetime64(secondary)-np.datetime64(reference))/np.timedelta64(1,'D')))
        observations.append({'source_id':source['title'],'reference_date':reference,'secondary_date':secondary,
                             'interval_days':interval,'selected_component':selected,
                             'coherent_component_fraction':float(support.mean()),
                             'ionosphere_filled_fraction':float(filled.mean()),
                             'source_sha256':source['sha256']})
    coherence=np.stack(coherence);phase=np.stack(phase);support=np.stack(component_masks);filled=np.stack(ionosphere)
    common=np.all(support,axis=0);comparable=np.all(common_data,axis=0)
    shared_reference=None;referenced=np.full_like(phase,np.nan,dtype='float32')
    if int(common.sum())>=8:
        score=np.where(common,np.min(coherence,axis=0),-np.inf);row,col=np.unravel_index(np.argmax(score),score.shape)
        referenced=np.where(support,phase-phase[:,row,col,None,None],np.nan).astype('float32')
        shared_reference={'pixel':[int(row),int(col)],'x':float(x[col]),'y':float(y[row]),'epsg':epsg,
                          'selection':'maximum minimum coherence on common selected-component support',
                          'independently_stable':False,'phase_datum_rad':[float(p[row,col]) for p in phase]}
    for i,observation in enumerate(observations):
        observation['common_spatial_support_median_coherence']=float(np.median(coherence[i][comparable])) if comparable.any() else None
        observation['common_spatial_support_coherence_histogram']=np.histogram(np.clip(coherence[i][comparable],0,1),bins=np.linspace(0,1,11))[0].tolist()
    ds=xr.Dataset({'coherence':(('pair','y','x'),coherence.astype('float32')),
                   'coherence_interval_contrast':(('pair_transition','y','x'),np.where(comparable,np.diff(coherence,axis=0),np.nan).astype('float32')),
                   'selected_component_support':(('pair','y','x'),support.astype('uint8')),
                   'ionosphere_filled':(('pair','y','x'),filled.astype('uint8')),
                   'phase_relative_to_shared_pixel_rad':(('pair','y','x'),referenced),
                   'common_phase_support':(('y','x'),common.astype('uint8')),
                   'common_coherence_support':(('y','x'),comparable.astype('uint8')),
                   'reference_time':(('pair',),np.array([o['reference_date'] for o in observations],dtype='datetime64[D]')),
                   'secondary_time':(('pair',),np.array([o['secondary_date'] for o in observations],dtype='datetime64[D]')),
                   'interval_days':(('pair',),np.array([o['interval_days'] for o in observations],dtype='int32'))},
                  coords={'pair':np.arange(len(pairs)),'pair_transition':np.arange(len(pairs)-1),'x':x,'y':y},
                  attrs={'description':'Measured GUNW interval dynamics; no displacement inversion',
                         'epsg_code':epsg,'series_key':next(iter(groups)),
                         'phase_datum':'same common-coherence pixel; stability is not established',
                         'corrections_applied':'none','los_displacement_status':'unavailable'})
    wkt=rasterio.crs.CRS.from_epsg(epsg).to_wkt();ds['spatial_ref']=xr.DataArray(np.int32(0),attrs={'spatial_ref':wkt,'crs_wkt':wkt})
    for name in ds.data_vars:
        if 'x' in ds[name].dims:ds[name].attrs['grid_mapping']='spatial_ref'
    ds.x.attrs.update(units='m',standard_name='projection_x_coordinate');ds.y.attrs.update(units='m',standard_name='projection_y_coordinate')
    ds.coherence.attrs.update(units='1',interpretation='measured interferometric coherence for the declared pair interval')
    ds.coherence_interval_contrast.attrs.update(units='coherence difference',interpretation='Later pair minus earlier pair on fixed common spatial support; unequal temporal baselines are a confound, not a physical change rate or causal effect')
    ds.phase_relative_to_shared_pixel_rad.attrs.update(units='radian',interpretation='uncorrected interferometric phase relative to common pixel; no temporal integration or validated motion')
    quality={'common_phase_support_fraction':float(common.mean()),'common_phase_support_pixels':int(common.sum()),
             'common_coherence_support_fraction':float(comparable.mean()),'shared_reference':shared_reference,
             'phase_comparison_status':'common_pixel_uncorrected_phase' if shared_reference else 'insufficient_common_component_support',
             'coherence_threshold':coherence_threshold,'coherence_histogram_bin_edges':np.linspace(0,1,11).tolist()}
    return ds,observations,quality


def process_stack(request,output):
    from .engine import validate_request,validate_output,write_cog
    validate_request(request,'insar-stack');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    source=request['source'];paths=[]
    if source['mode']=='local_pairs':paths=list(map(Path,source['pair_paths']))
    else:
        catalog=search(source['longitude'],source['latitude'],'GUNW',source['start_date']+'T00:00:00Z',source['end_date']+'T23:59:59Z')
        (out/'catalog.json').write_text(json.dumps(catalog,indent=2))
        groups=group_entries(catalog['entries'],'GUNW');candidates=[]
        for key,entries in groups.items():
            accepted=[e for e in entries if min(acquisition_dates(e['title']))>=source['start_date'] and max(acquisition_dates(e['title']))<=source['end_date']]
            if len(accepted)>=2 and (not source.get('series_key') or source['series_key']==key):candidates.append((key,accepted))
        if candidates:
            key,entries=max(candidates,key=lambda item:(len(item[1]),item[0]))
            count=min(len(entries),source.get('max_pairs',3))
            entries=[entries[i] for i in np.linspace(0,len(entries)-1,count,dtype=int)]
            for i,e in enumerate(entries):
                paths.append(fetch_pair(e,source['longitude'],source['latitude'],out/'cache'/f'pair_{i}.npz',source.get('extent_km',5),unwrapped_only=True))
    limitations=['Each pair has its own reference/secondary interval. Unequal baselines, moisture, scattering and processing can change coherence; differences are not isolated event effects.',
                 'The shared reference pixel maximizes minimum coherence; independent stability is not established.',
                 'No atmospheric, ionospheric or tidal correction, LOS/vertical displacement, phase integration, SBAS inversion or forecasting is performed.',
                 'Native common pixel centers are intersected exactly. Connected-component IDs are local to each pair and are never matched numerically across dates.',
                 'Ionosphere-filled flags remain explicit; lack of a fill flag alone is not correction validation.']
    result={'contract_version':'1.0','request_id':request['request_id'],'analysis':'interferometric_interval_dynamics',
            'status':'no_data','observations':[],'quality':{},'grid':{},'artifacts':[],
            'los_displacement_cm':None,'limitations':limitations}
    if len(paths)<2:result['reason']='Fewer than two compatible pairs have both acquisition dates inside the requested window'
    else:
        pairs=[];metadata=[]
        for path in paths:
            meta=json.loads(path.with_suffix('.json').read_text())
            if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('GUNW stack input checksum mismatch')
            with np.load(path,allow_pickle=False) as h:pairs.append({k:h[k] for k in h.files})
            metadata.append(meta)
        ds,observations,quality=analyze_stack(pairs,metadata,request.get('coherence_threshold',.35))
        target=out/'interferometric_interval_dynamics.nc'
        ds.to_netcdf(target,engine='h5netcdf',encoding={k:{'zlib':True,'complevel':3} for k in ds.data_vars if ds[k].ndim>0})
        result.update(status='complete' if quality['shared_reference'] else 'partial',observations=observations,quality=quality,
                      grid={'epsg':ds.attrs['epsg_code'],'width':len(ds.x),'height':len(ds.y),'pixel_spacing_m':float(ds.x[1]-ds.x[0]),'alignment':'exact common native pixel centers; no resampling'})
        minimum=ds.coherence.min('pair',skipna=False).values
        write_cog(out/'minimum_coherence.tif',minimum,ds.x.values,ds.y.values,ds.attrs['epsg_code'],'coherence')
        write_cog(out/'coherence_interval_contrast.tif',ds.coherence_interval_contrast.values,ds.x.values,ds.y.values,ds.attrs['epsg_code'],'coherence difference')
        write_cog(out/'common_phase_support.tif',ds.common_phase_support.values,ds.x.values,ds.y.values,ds.attrs['epsg_code'],'mask',angular=True)
        from .render import insar_stack_figure
        insar_stack_figure(ds,observations,quality,out/'interval_dynamics.png',request['request_id'])
        for name,role,media in [('interferometric_interval_dynamics.nc','observed_interval_arrays','application/x-netcdf'),('minimum_coherence.tif','minimum_measured_pair_coherence','image/tiff'),('coherence_interval_contrast.tif','descriptive_pair_coherence_contrast','image/tiff'),('common_phase_support.tif','common_reference_support_mask','image/tiff'),('interval_dynamics.png','observed_interval_science_figure','image/png')]:
            result['artifacts'].append({'id':Path(name).stem,'href':name,'role':role,'media_type':media,'sha256':hashlib.sha256((out/name).read_bytes()).hexdigest()})
    validate_output(result,'insar-stack')
    (out/'request.json').write_text(json.dumps(request,indent=2));(out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    return result
