"""Science artifact jobs. A web/backend team can call these functions or the CLI."""
from pathlib import Path
import csv,hashlib,json
import numpy as np
import xarray as xr
import rasterio
from rasterio.transform import from_origin
from jsonschema import Draft202012Validator,FormatChecker
from .io import load_cube,cube_from_chips,fetch_chip,save_cube
from .catalog import search,group_entries,choose_series
from .analysis import measured_db,elapsed_days,physical_statistics,spatial_structure,region_series,supported_reduction
from .spectral import spectral_analysis,local_time_frequency
from .stochastic import historical_series,smooth_observations
from . import render

SCHEMAS=Path(__file__).resolve().parent/'schemas'
BASE_LIMITS=[
 'Descriptive observed states only; no future forecasts, event labels or hazard probabilities.',
 'Native focused-support masks do not replace SNR, layover/shadow or radiometric QA. Thermal-noise correction is not performed here.',
 'Colors encode measurements or fixed representation axes, not land-cover classes. Derived ratios add no independent polarization measurement.',
 'Catalog coverage and regional tests do not establish globally validated learned-model accuracy.'
]

def safe_json(value):
    if isinstance(value,dict):return {str(k):safe_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [safe_json(v) for v in value]
    if isinstance(value,np.ndarray):return safe_json(value.tolist())
    if isinstance(value,np.generic):return safe_json(value.item())
    if isinstance(value,float) and not np.isfinite(value):return None
    return value

def validate_request(request,kind='analysis'):
    def finite(obj):
        if isinstance(obj,dict):return all(finite(v) for v in obj.values())
        if isinstance(obj,list):return all(finite(v) for v in obj)
        return not isinstance(obj,float) or np.isfinite(obj)
    if not finite(request):raise ValueError('Request numbers must be finite JSON values')
    schema=json.loads((SCHEMAS/f'{kind}-request.schema.json').read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(request)
    if kind in ['analysis','insar-network','insar-stack'] and request['source']['mode']=='catalog_point':
        s=request['source']
        if s['end_date']<s['start_date']:raise ValueError('end_date precedes start_date')
    return request

def write_cog(path,array,x,y,epsg,units,descriptions=None,angular=False):
    a=np.asarray(array,dtype='float32');a=a[None] if a.ndim==2 else a
    dx=float(x[1]-x[0]);dy=float(y[0]-y[1])
    with rasterio.open(path,'w',driver='COG',height=len(y),width=len(x),count=a.shape[0],dtype='float32',crs=f'EPSG:{int(epsg)}',transform=from_origin(x[0]-dx/2,y[0]+dy/2,dx,dy),nodata=np.nan,compress='DEFLATE',blocksize=256,overview_resampling='NEAREST' if angular else 'AVERAGE') as dst:
        dst.write(a);dst.update_tags(units=units,interpretation='descriptive measured radar features; no event class')
        for i in range(a.shape[0]):dst.set_band_description(i+1,(descriptions or [f'band_{j+1}' for j in range(a.shape[0])])[i])

def analyze(request,output):
    validate_request(request);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    (out/'request.json').write_text(json.dumps(request,indent=2,allow_nan=False))
    result={'contract_version':'1.0','request_id':request['request_id'],'status':'complete','scope':'descriptive_observed_surface_dynamics','observations':{'dates':[],'polarizations':[],'grid':{}},'capabilities':{m:{'status':'not_requested'} for m in ['physical','spectral','stochastic','transformer']},'artifacts':[],'insights':[],'limitations':BASE_LIMITS.copy()}
    if 'context' in request:result['context']=request['context']
    def register(path,role,units=None,dimensions=None,legend=None):
        item={'id':Path(path).stem,'href':Path(path).relative_to(out).as_posix(),'media_type':{'.png':'image/png','.gif':'image/gif','.nc':'application/x-netcdf','.tif':'image/tiff; application=geotiff; profile=cloud-optimized','.npz':'application/x-npz','.csv':'text/csv','.json':'application/json'}[Path(path).suffix],'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'role':role}
        if units:item['units']=units
        if dimensions:item['dimensions']=dimensions
        if legend:item['legend']=legend
        result['artifacts'].append(item)
    source=request['source']
    if source['mode']=='local_cube':cube=load_cube(source['cube_path'])
    else:
        catalog=search(source['longitude'],source['latitude'],start=source['start_date']+'T00:00:00Z',end=source['end_date']+'T23:59:59Z')
        catalog_path=out/'catalog.json';catalog_path.write_text(json.dumps(catalog,indent=2));register(catalog_path,'source_catalog')
        groups=group_entries(catalog['entries']);key=source.get('series_key')
        if key:
            if key not in groups:raise ValueError('Requested compatible series is absent from catalog')
            _,entries=choose_series({key:groups[key]},max_dates=source.get('max_dates',10))
        else:key,entries=choose_series(groups,max_dates=source.get('max_dates',10))
        if not entries:
            result['status']='no_data';result['limitations'].append('No compatible GCOV series with at least three dates was found in this request window.')
            result['observations']['selection']={'catalog_hits':catalog['hits'],'groups':{k:len(v) for k,v in groups.items()}}
            for m in request['analyses']:result['capabilities'][m]={'status':'insufficient_data','reason':'No compatible measured time series'}
            return finish(result,out)
        paths=[]
        for entry in entries:
            path=out/'cache'/(entry['time_start'][:10]+'.npz');fetch_chip(entry,source['longitude'],source['latitude'],path,source.get('native_chip_pixels',512));paths.append(path)
        aggregate=source.get('aggregate_pixels',4)
        if 'target_posting_m' in source:
            if 'aggregate_pixels' in source:raise ValueError('Choose target_posting_m or aggregate_pixels, not both')
            with np.load(paths[0],allow_pickle=False) as measured:spacing=float(measured['x'][1]-measured['x'][0])
            aggregate=int(round(source['target_posting_m']/spacing))
            if aggregate<1 or not np.isclose(aggregate*spacing,source['target_posting_m']):raise ValueError('Target posting must be a whole native pixel multiple; no interpolation or upsampling')
        cube=cube_from_chips(paths,aggregate);save_cube(cube,out/'measured_cube.npz');register(out/'measured_cube.npz','measured_linear_power_cube','gamma0 (unitless)',['time','polarization','y','x'])
        result['observations']['selection']={'catalog_hits':catalog['hits'],'compatible_group':key,'selected_dates':len(entries),'available_compatible_dates':len(groups[key]),'selection_policy':'evenly spaced acquisition indices when capped; omitted dates are not evidence of outages'}
    if len(cube['dates'])<3:raise ValueError('At least three observed acquisitions are required')
    db=measured_db(cube);t=elapsed_days(cube['dates']);x,y=cube['x'],cube['y'];pols=list(map(str,cube['polarizations']));H,W=db.shape[-2:];pixel_m=float(x[1]-x[0]);title=request['request_id'].replace('-',' ').replace('_',' ')
    pixel_m_y=float(y[0]-y[1])
    result['observations'].update(dates=list(map(str,cube['dates'])),polarizations=pols,grid={'epsg':int(cube['epsg']),'width':W,'height':H,'pixel_spacing_m':[pixel_m,pixel_m_y],'bounds_native':[float(x[0]-pixel_m/2),float(y[-1]-pixel_m_y/2),float(x[-1]+pixel_m/2),float(y[0]+pixel_m_y/2)],'coordinate_units':'metres','pixel_coordinates':'centers; upper left origin; north-up'},sources=json.loads(str(cube.get('source_metadata_json','[]'))))
    result['observations']['input_quality']={'valid_channel_pixel_fraction_by_date':cube['valid'].mean(axis=(1,2,3)).tolist(),
                                           'complete_channel_pixel_fraction':float(cube['valid'].all(axis=0).mean()),
                                           'support_scope':'Focused positive-power sample support; not SNR, stable terrain or atmospheric quality'}
    if 'processing_metadata_json' in cube:result['observations']['processing']=json.loads(str(cube['processing_metadata_json']))
    if source['mode']=='catalog_point':
        from rasterio.warp import transform
        qx,qy=transform('EPSG:4326',f'EPSG:{int(cube["epsg"])}',[source['longitude']],[source['latitude']])
        qc=int(np.argmin(abs(x-qx[0])));qr=int(np.argmin(abs(y-qy[0])))
        result['observations']['query_point_quality']={'selected_pixel':[qr,qc],
            'observed_date_count':int(cube['valid'][:,:,qr,qc].any(1).sum()),
            'valid_polarizations_by_date':[[str(pols[c]) for c in range(len(pols)) if cube['valid'][i,c,qr,qc]] for i in range(len(cube['dates']))]}
    if (np.isfinite(db).sum(0)>=3).sum()<8:
        result['status']='no_data';result['limitations'].append('Insufficient valid measured support: fewer than eight channel/pixels have three observations.')
        for m in request['analyses']:result['capabilities'][m]={'status':'insufficient_data','reason':'Insufficient finite positive measured support'}
        return finish(result,out)
    stat=physical_statistics(db,t);structure=spatial_structure(db,pixel_m,pixel_m_y);common=np.isfinite(db).all(axis=(0,1));regional=region_series(db,common)
    data={'gamma0_power':(('time','polarization','y','x'),cube['power']),'gamma0_db':(('time','polarization','y','x'),db),'valid_observation':(('time','polarization','y','x'),cube['valid'].astype('uint8'))}
    for name in ['valid_date_count','median_db','mad_db','slope_db_per_day','maximum_step_db']:data[name]=(('polarization','y','x'),stat[name])
    data['change_rate_db_per_day']=(('transition_time','polarization','y','x'),stat['change_rate_db_per_day'])
    data['spatial_anisotropy']=(('y','x'),structure['spatial_anisotropy']);data['spatial_orientation_radians']=(('y','x'),structure['spatial_orientation_radians'])
    if 'spatial_modes' in stat:
        data['pod_spatial_modes']=(('mode','polarization','y','x'),stat['spatial_modes']);data['pod_temporal_coefficients']=(('time','mode'),stat['temporal_coefficients'])
    if regional is not None:
        result['observations']['regional_aggregation']={'supported_pixels':regional['supported_pixels'],
            'support_scope':regional['support_scope'],'interval_type':regional['interval_type']}
        path=out/'regional_timeseries.csv'
        with path.open('w',newline='',encoding='utf8') as stream:
            writer=csv.writer(stream);writer.writerow(['date','polarization','mean_log_power_db','lower_50_db','upper_50_db','lower_80_db','upper_80_db','lower_95_db','upper_95_db','spatial_block_count'])
            for i,date in enumerate(cube['dates']):
                for c,p in enumerate(pols):writer.writerow([date,p,regional['mean_db'][i,c],regional['quantile_db'][2,i,c],regional['quantile_db'][4,i,c],regional['quantile_db'][1,i,c],regional['quantile_db'][5,i,c],regional['quantile_db'][0,i,c],regional['quantile_db'][6,i,c],regional['block_count']])
        register(path,'spatial_block_bootstrap_regional_series','dB',['time','polarization']);result['limitations'].append('Regional bands resample spatial blocks of mean dB on common support; they omit sensor/process error and assume approximate block independence.')
    if 'physical' in request['analyses']:
        render.physical_figure(cube,db,stat,structure,out/'physical.png',title);register(out/'physical.png','physical_descriptive_figure')
        for key,unit in [('median_db','dB'),('mad_db','dB'),('slope_db_per_day','dB/day'),('maximum_step_db','dB'),('valid_date_count','count')]:
            path=out/(key+'.tif');write_cog(path,stat[key],x,y,cube['epsg'],unit,pols);register(path,'physical_map',unit,['polarization','y','x'])
        render.observed_gif(cube,db,out/'observed_evolution.gif',title);register(out/'observed_evolution.gif','observed_acquisition_animation',legend={'red':'co-pol dB [-25,0]','green':'cross-pol dB [-32,-5] when present','blue':'cross/co ratio dB [-18,-2] when present','time':'measured dates only; uniform playback is not uniform sampling'})
        result['capabilities']['physical']={'status':'available','metadata':{'common_support_fraction':float(common.mean()),'native_quality_scope':'focused sample support only','pod_variance_fraction':stat.get('variance_fraction',[]).tolist() if 'variance_fraction' in stat else []}}
    if 'spectral' in request['analyses']:
        spectral=spectral_analysis(db,t)
        if spectral['status']=='available':
            channel=next(i for i in range(len(pols)) if np.isfinite(spectral['candidate_period_days'][i]).any())
            complete=np.isfinite(db[:,channel]).all(0)
            local=local_time_frequency(db[:,channel,complete].mean(1),t)
            render.spectral_figure(cube,spectral,local,out/'spectral.png',title);register(out/'spectral.png','actual_time_spectral_figure')
            for key in ['candidate_period_days','peak_normalized_power']:
                data[key]=(('polarization','y','x'),spectral[key]);path=out/(key+'.tif');unit='days' if key.endswith('days') else 'normalized power';write_cog(path,spectral[key],x,y,cube['epsg'],unit,pols);register(path,'tentative_spectral_map',unit,['polarization','y','x'])
            np.savez_compressed(out/'spectral_arrays.npz',frequencies_cycles_day=spectral['frequencies_cycles_day'],roi_spectra=spectral['roi_spectra'],sampling_window=spectral['sampling_window'],spatial_fft_power=structure['spatial_fft_power'],spatial_fx_cycles_km=structure['spatial_fx_cycles_km'],spatial_fy_cycles_km=structure['spatial_fy_cycles_km'],**({'local_power':local['power'],'local_periods_days':local['periods_days']} if 'power' in local else {}));register(out/'spectral_arrays.npz','spectral_and_spatial_frequency_arrays')
            if (out/'time_frequency.png').exists() and local['status']=='available':register(out/'time_frequency.png','actual_time_local_spectral_figure')
            path=out/'spectral_series.csv'
            with path.open('w',newline='',encoding='utf8') as stream:
                writer=csv.writer(stream)
                writer.writerow(['frequency_cycles_day','period_days','sampling_window',*[f'{pol}_normalized_power' for pol in pols]])
                for f,period,window,scores in zip(spectral['frequencies_cycles_day'],spectral['periods_days'],
                                                  spectral['sampling_window'],spectral['roi_spectra'].T):
                    writer.writerow([f,period,window,*safe_json(scores)])
            register(path,'regional_spectrum_and_sampling_window')
            result['capabilities']['spectral']={'status':'experimental','metadata':{'channels':spectral['channels'],'local_time_frequency_status':local['status'],'frequency_search_policy':'2/span to below 0.5/median_cadence; conservative display band, not a universal Nyquist theorem'}}
            for pol,summary in zip(pols,spectral['channels']):
                if summary['status']=='available':
                    result['insights'].append({'id':'spectral_support','observation':f'{pol} regional candidate is {summary["candidate_period_days"]:.1f} days; max-statistic permutation p={summary["max_statistic_permutation_p"]:.3f}.','interpretation_limit':'Sampling, trend, null assumptions and record duration limit interpretation.','evidence_artifacts':['spectral','spectral_series']})
                    break
        else:result['capabilities']['spectral']={'status':'insufficient_data','reason':spectral['reason']}
    if 'stochastic' in request['analyses']:
        stochastic=smooth_observations(db,t,draws=request.get('stochastic_draws',256))
        history=historical_series(stochastic,cube['dates'],pols)
        fit_available=any(item['status']=='fit_returned' for item in history.values())
        capability={'status':'experimental' if fit_available else 'insufficient_data',
                    'metadata':{'parameters':stochastic['parameters'],'draws':request.get('stochastic_draws',256),
                                'interval_type':'conditional_empirical_Bayes_GP_latent','calibrated':False}}
        if fit_available:
            data['gp_latent_mean_db']=(('time','polarization','y','x'),stochastic['mean_db'])
            data['gp_conditional_sd_db']=(('time','polarization','y','x'),stochastic['sd_db'])
            np.savez_compressed(out/'stochastic_draws.npz',draws_db=stochastic['region_draws_db'],dates=cube['dates'],polarizations=cube['polarizations'])
            register(out/'stochastic_draws.npz','conditional_historical_GP_ensemble','dB',['draw','polarization','time'])
            render.stochastic_figure(cube,regional,stochastic,out/'stochastic.png',title)
            register(out/'stochastic.png','stochastic_historical_state_figure')
        else:
            capability['reason']='No supported nonconstant channel produced a converged historical GP fit'
        path=out/'gp_historical_series.csv'
        with path.open('w',newline='',encoding='utf8') as stream:
            writer=csv.writer(stream)
            writer.writerow(['date','polarization','supported_cells','status','observed_mean_db','latent_median_db',
                             'conditional_80_lower_db','conditional_80_upper_db','conditional_95_lower_db','conditional_95_upper_db'])
            for pol,record in history.items():
                for row in record['series']:
                    writer.writerow([row['date'],pol,record['supported_cells'],record['status'],row['observed_mean_db'],
                                     row['latent_median_db'],*(row['conditional_80_db'] or (None,None)),
                                     *(row['conditional_95_db'] or (None,None))])
        register(path,'observed_and_conditional_GP_history','dB',['time','polarization'])
        result['capabilities']['stochastic']=capability
        result['limitations'].append(stochastic['interpretation'])
    if 'transformer' in request['analyses']:
        checkpoint=Path(request.get('transformer_checkpoint','models/global_explorer_v1/masked_temporal_transformer.pt'))
        if not checkpoint.exists():result['capabilities']['transformer']={'status':'unsupported_domain','reason':'Requested checkpoint is unavailable; physical methods remain usable'}
        else:
            from .transformer import infer
            from .representation import apply_basis,descriptor_features
            learned=infer(cube,checkpoint)
            if learned['status']=='available':
                data['learned_embedding']=(('y','x','latent'),learned['embedding']);data['learned_state']=(('time','y','x','latent'),learned['state']);data['dropout_embedding_sd']=(('y','x'),learned['dropout_embedding_sd'])
                if 'contextual_state_change_per_day' in learned:
                    data['contextual_state_change_per_day']=(('transition_time','y','x'),learned['contextual_state_change_per_day'])
                    path=out/'maximum_contextual_state_change.tif'
                    write_cog(path,supported_reduction(learned['contextual_state_change_per_day'],np.nanmax),x,y,cube['epsg'],'RMS latent-coordinate difference/day')
                    register(path,'experimental_contextual_state_change','RMS latent-coordinate difference/day',['y','x'],{'interpretation':'Derived change in bidirectional latent states; not speed, displacement or event probability'})
                    dynamic_projection=checkpoint.parent/'dynamic_projection.npz'
                    if dynamic_projection.exists():
                        from .dynamic_view import controlled_view
                        dynamic=controlled_view(cube,checkpoint,learned['state'])
                        dynamic_rgb,_=apply_basis(dynamic['state'],dynamic_projection)
                        data['dynamic_state']=(('time','y','x','latent'),dynamic['state'])
                        data['dynamic_rgb_time']=(('time','y','x','rgb'),dynamic_rgb)
                        data['dynamic_context_change_per_day']=(('transition_time','y','x'),dynamic['change_per_day'])
                        render.learned_gif(cube,db,dynamic_rgb,out/'controlled_dynamics.gif',title+' / constant-control contrast',learned_label='Actual minus constant-trajectory state RGB')
                        register(out/'controlled_dynamics.gif','constant_trajectory_control_contrast_animation',legend={'RGB':'frozen training-only PCA of actual minus constant-trajectory contextual states','interpretation':dynamic['interpretation']})
                        path=out/'maximum_controlled_state_change.tif'
                        write_cog(path,supported_reduction(dynamic['change_per_day'],np.nanmax),x,y,cube['epsg'],'RMS control-contrast coordinate difference/day')
                        register(path,'experimental_control_contrast_change','RMS control-contrast coordinate difference/day',['y','x'])
                        learned['metadata']['dynamic_view']=dynamic['interpretation']
                        learned['metadata']['dynamic_projection_sha256']=hashlib.sha256(dynamic_projection.read_bytes()).hexdigest()
                projection=checkpoint.parent/'embedding_projection.npz'
                if projection.exists():
                    learned['metadata']['embedding_projection_sha256']=hashlib.sha256(projection.read_bytes()).hexdigest()
                    rgb,distance=apply_basis(learned['embedding'],projection);data['learned_rgb']=(('y','x','rgb'),rgb);data['embedding_standardized_distance']=(('y','x'),distance)
                    rgb_time,_=apply_basis(learned['state'],projection);data['learned_rgb_time']=(('time','y','x','rgb'),rgb_time)
                    render.learned_gif(cube,db,rgb_time,out/'learned_evolution.gif',title);register(out/'learned_evolution.gif','bidirectional_contextual_observed_date_state_animation',legend={'RGB':'same frozen latent projection for every geography/date','context':'all selected observations; no forecast'})
                    render.learned_figure(cube,db,rgb,learned['dropout_embedding_sd'],distance,out/'learned.png',title);register(out/'learned.png','shared_transformer_embedding_figure',legend={'RGB':'frozen training-only PCA of the shared latent; no class semantics','uncertainty':'4-draw MC dropout latent sensitivity, uncalibrated'})
                features=descriptor_features(cube);physical_basis=checkpoint.parent/'physical_projection.npz'
                if features is not None and physical_basis.exists():data['physical_descriptor_rgb']=(('y','x','rgb'),apply_basis(features,physical_basis)[0])
                np.savez_compressed(out/'learned_arrays.npz',embedding=learned['embedding'],state=learned['state'],dates=cube['dates'],mc_dropout_latent_sd=learned['dropout_embedding_sd'],x=x,y=y,epsg=cube['epsg']);register(out/'learned_arrays.npz','learned_state_arrays')
                result['capabilities']['transformer']={'status':'experimental','metadata':learned['metadata']}
            else:result['capabilities']['transformer']={'status':'unsupported_domain','reason':learned['reason']}
    ds=xr.Dataset(data,coords={'time':np.array(cube['dates'],dtype='datetime64[D]'),'transition_time':np.array(cube['dates'][1:],dtype='datetime64[D]'),'polarization':pols,'x':x,'y':y},attrs={'description':'Observed radar surface dynamics; no forecasting or event classification','epsg_code':int(cube['epsg']),'radiometry':'linear gamma0 power; arithmetic aggregation before dB','request_id':request['request_id'],'contract_version':'1.0','spatial_ref':rasterio.crs.CRS.from_epsg(int(cube['epsg'])).to_wkt()})
    for name in ds.data_vars:
        if name.endswith('_db'):ds[name].attrs['units']='dB'
        elif name.endswith('_days'):ds[name].attrs['units']='days'
        elif name.endswith('_per_day'):ds[name].attrs['units']='dB/day'
    ds.gamma0_power.attrs.update(units='1',radiometry='linear gamma0; not amplitude, dB or RGB')
    if 'learned_state' in ds:ds.learned_state.attrs.update(units='latent coordinate',context='bidirectional context of all selected dates; derived representation, not a forecast')
    if 'learned_rgb_time' in ds:ds.learned_rgb_time.attrs.update(units='1',projection='frozen training-only global latent PCA and scaling; not class colors')
    if 'gp_conditional_sd_db' in ds:ds.gp_conditional_sd_db.attrs.update(units='dB',uncertainty_type='conditional empirical-Bayes latent SD; hyperparameter/model uncertainty omitted; not calibrated sensor noise')
    if 'contextual_state_change_per_day' in ds:ds.contextual_state_change_per_day.attrs.update(units='RMS latent-coordinate difference/day',interpretation='Difference between contextual learned states at adjacent observed dates divided by actual elapsed days; no physical speed, motion or event likelihood')
    if 'dynamic_state' in ds:ds.dynamic_state.attrs.update(units='latent contrast coordinate',interpretation='Frozen actual contextual state minus a constant-trajectory control at identical dates and masks; not causal attribution or physical motion')
    if 'dynamic_context_change_per_day' in ds:ds.dynamic_context_change_per_day.attrs.update(units='RMS control-contrast coordinate difference/day',interpretation='Actual-date rate of contextual control contrast; not physical speed, hazard or calibrated anomaly likelihood')
    if 'dynamic_rgb_time' in ds:ds.dynamic_rgb_time.attrs.update(units='1',projection='training-only frozen dynamic_projection.npz; no class semantics')
    ds.spatial_orientation_radians.attrs.update(units='radian',convention=structure['orientation_convention'])
    crs_wkt=ds.attrs['spatial_ref'];ds['spatial_ref']=xr.DataArray(np.int32(0),attrs={'spatial_ref':crs_wkt,'crs_wkt':crs_wkt,'epsg_code':f'EPSG:{int(cube["epsg"])}'})
    for name in ds.data_vars:
        if 'x' in ds[name].dims and 'y' in ds[name].dims:ds[name].attrs['grid_mapping']='spatial_ref'
    ds.x.attrs.update(units='m',standard_name='projection_x_coordinate');ds.y.attrs.update(units='m',standard_name='projection_y_coordinate')
    ds.to_netcdf(out/'surface_dynamics.nc',engine='h5netcdf',encoding={k:{'zlib':True,'complevel':3} for k in ds.data_vars if ds[k].ndim>0});register(out/'surface_dynamics.nc','all_scientific_arrays_and_coordinates')
    np.savez_compressed(out/'modal_arrays.npz',**{k:stat[k] for k in ['spatial_modes','temporal_coefficients','variance_fraction'] if k in stat});register(out/'modal_arrays.npz','proper_orthogonal_decomposition_modes')
    if regional is not None:
        delta=np.diff(regional['mean_db'][:,0]);i=int(np.argmax(abs(delta)))
        result['insights'].insert(0,{'id':'largest_observed_regional_step','observation':f'Largest regional {pols[0]} mean-log-power step is {delta[i]:+.2f} dB between {cube["dates"][i]} and {cube["dates"][i+1]} ({t[i+1]-t[i]:.0f} days).','interpretation_limit':'This identifies an observed interval, not an event date, cause or affected area.','evidence_artifacts':['regional_timeseries','physical' if 'physical' in request['analyses'] else 'surface_dynamics']})
    if request.get('context',{}).get('event_date'):
        event=np.datetime64(request['context']['event_date']);dates=np.array(cube['dates'],dtype='datetime64[D]');before=int((dates<event).sum());after=int((dates>event).sum())
        result['insights'].append({'id':'event_temporal_support','observation':f'The independently supplied context date has {before} earlier and {after} later selected acquisitions.','interpretation_limit':'Aftermath-only data cannot measure the event transition.' if before==0 else 'Temporal bracketing is not causal attribution; no independently validated event footprint is used.'})
    if any(result['capabilities'][m]['status'] in ['unsupported_domain','insufficient_data','failed'] for m in request['analyses']):result['status']='partial'
    return finish(result,out)

def finish(result,out):
    result=safe_json(result);schema=json.loads((SCHEMAS/'analysis-result.schema.json').read_text());Draft202012Validator(schema,format_checker=FormatChecker()).validate(result)
    (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf8')
    return result

def validate_output(result,kind):
    schema=json.loads((SCHEMAS/f'{kind}-result.schema.json').read_text());Draft202012Validator(schema,format_checker=FormatChecker()).validate(result)
    return result

def analyze_insar(request,output):
    validate_request(request,'insar');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    (out/'request.json').write_text(json.dumps(request,indent=2,allow_nan=False))
    from .insar import select_event_pair,fetch_pair,analyze_pair,validate_pair,NoCoherentSupport
    source=request['source'];selection={}
    if source['mode']=='local_pair':path=Path(source['pair_path'])
    else:
        catalog=search(source['longitude'],source['latitude'],'GUNW');(out/'catalog.json').write_text(json.dumps(catalog,indent=2))
        entry=select_event_pair(catalog['entries'],source['event_date'])
        if entry is None:
            response={'contract_version':'1.0','request_id':request['request_id'],'status':'no_data','analysis':'interferometric_observations','reason':'No real GUNW pair brackets the context date','artifacts':[]};validate_output(response,'insar');(out/'result.json').write_text(json.dumps(response,indent=2));return response
        path=fetch_pair(entry,source['longitude'],source['latitude'],out/'measured_pair.npz',source.get('extent_km',20));selection={'event_date':source['event_date']}
    source_meta=json.loads(path.with_suffix('.json').read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source_meta['sha256']:raise ValueError('GUNW source checksum mismatch')
    with np.load(path,allow_pickle=False) as h:pair={k:h[k] for k in h.files}
    validate_pair(pair)
    try:result=analyze_pair(pair,request.get('coherence_threshold',.35))
    except NoCoherentSupport as error:
        response={'contract_version':'1.0','request_id':request['request_id'],'status':'no_data','analysis':'interferometric_observations','source':source_meta,'reason':str(error),'artifacts':[],'los_displacement_cm':None,'deformation_attribution':None}
        validate_output(response,'insar');(out/'result.json').write_text(json.dumps(response,indent=2,allow_nan=False));return response
    import re
    times=re.findall(r'(\d{8}T\d{6})',source_meta.get('title',''))
    dates=[f'{t[:4]}-{t[4:6]}-{t[6:8]}' for t in times]
    subtitle=f'{dates[0]} → {dates[2]} | observed pair interval; not an isolated instantaneous event' if len(dates)>=4 else ''
    render.insar_figure(pair,result,out/'interferometry.png',request['request_id'].replace('-',' '),subtitle)
    artifacts=[]
    for key in ['referenced_unwrapped_rad','phase_equivalent_cm','component_valid','ionosphere_filled']:
        unit='radians' if key.endswith('rad') else 'phase-equivalent cm' if key.endswith('cm') else 'mask'
        target=out/(key+'.tif');write_cog(target,result[key],pair['unwrapped_x'],pair['unwrapped_y'],pair['unwrapped_epsg'],unit,angular=key.endswith('rad') or unit=='mask')
        artifacts.append({'id':key,'href':target.name,'media_type':'image/tiff','role':'uncorrected_interferometric_measurement','units':unit,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    np.savez_compressed(out/'interferometric_arrays.npz',**{k:v for k,v in result.items() if k!='metadata'},wrapped_x=pair['wrapped_x'],wrapped_y=pair['wrapped_y'],unwrapped_x=pair['unwrapped_x'],unwrapped_y=pair['unwrapped_y'],epsg=pair['unwrapped_epsg'])
    for name,media in [('interferometry.png','image/png'),('interferometric_arrays.npz','application/x-npz')]:artifacts.append({'id':Path(name).stem,'href':name,'media_type':media,'sha256':hashlib.sha256((out/name).read_bytes()).hexdigest(),'role':'phase_coherence_quality'})
    response={'contract_version':'1.0','request_id':request['request_id'],'status':'complete','analysis':'interferometric_observations','source':source_meta,'selection':selection,'quality':result['metadata'],'artifacts':artifacts,'context_source_url':request.get('context_source_url'),'los_displacement_cm':None,'deformation_attribution':None}
    response=safe_json(response);validate_output(response,'insar');(out/'result.json').write_text(json.dumps(response,indent=2,allow_nan=False));return response
