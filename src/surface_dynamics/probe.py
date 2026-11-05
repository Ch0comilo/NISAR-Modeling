"""Extract measured trajectories and descriptors at a user-selected map location."""
from pathlib import Path
import json
import numpy as np
import xarray as xr
from rasterio.warp import transform
from .analysis import region_series

def probe(request):
    from .engine import validate_request,validate_output,safe_json
    validate_request(request,'probe')
    with xr.open_dataset(request['dataset_path'],engine='h5netcdf') as ds:
        epsg=int(ds.attrs['epsg_code']);xx,yy=transform('EPSG:4326',f'EPSG:{epsg}',[request['longitude']],[request['latitude']])
        x,y=ds.x.values,ds.y.values;col=int(np.argmin(abs(x-xx[0])));row=int(np.argmin(abs(y-yy[0])))
        dx=float(x[1]-x[0]);dy=float(y[0]-y[1])
        if xx[0]<x[0]-dx/2 or xx[0]>x[-1]+dx/2 or yy[0]<y[-1]-dy/2 or yy[0]>y[0]+dy/2:
            return validate_output({'contract_version':'1.0','request_id':request['request_id'],'status':'outside_observed_footprint','observed_series':[]},'probe')
        radius=request.get('radius_m',0)
        if radius:
            X,Y=np.meshgrid(x,y);mask=(X-xx[0])**2+(Y-yy[0])**2<=radius**2
            if not mask.any():mask[row,col]=True
        else:mask=np.zeros((len(y),len(x)),bool);mask[row,col]=True
        db=ds.gamma0_db.values;valid=np.isfinite(db)&mask[None,None]
        if not valid.any():
            return validate_output({'contract_version':'1.0','request_id':request['request_id'],'status':'no_valid_observations',
                                    'observed_series':[],'selected_pixel':[row,col],'epsg':epsg,
                                    'reason':'Selection is inside the projected grid but has no supported measured samples'},'probe')
        count=valid.sum(axis=(2,3));series=np.divide(np.where(valid,db,0).sum(axis=(2,3)),count,out=np.full(count.shape,np.nan),where=count>0)
        boot=region_series(db,mask,block=4) if radius else None
        records=[]
        for i,date in enumerate(ds.time.values):
            for c,pol in enumerate(ds.polarization.values):
                records.append({'date':str(date)[:10],'polarization':str(pol),'mean_log_power_db':float(series[i,c]),'valid_pixels':int(np.isfinite(db[i,c][mask]).sum()),'spatial_intervals_db':{'50':[boot['quantile_db'][2,i,c],boot['quantile_db'][4,i,c]],'80':[boot['quantile_db'][1,i,c],boot['quantile_db'][5,i,c]],'95':[boot['quantile_db'][0,i,c],boot['quantile_db'][6,i,c]]} if boot else None})
        descriptors={}
        for key in ['median_db','mad_db','slope_db_per_day','maximum_step_db','candidate_period_days','peak_normalized_power','dropout_embedding_sd','contextual_state_change_per_day','dynamic_context_change_per_day']:
            if key in ds:descriptors[key]=ds[key].values[...,row,col] if ds[key].dims[-2:]==('y','x') else None
        latent=ds.learned_embedding.values[row,col] if 'learned_embedding' in ds else None
        latent_time=ds.learned_state.isel(y=row,x=col).values if 'learned_state' in ds else None
        dynamic=ds.dynamic_state.isel(y=row,x=col).values if 'dynamic_state' in ds else None
        return validate_output(safe_json({'contract_version':'1.0','request_id':request['request_id'],'status':'complete','selected_pixel':[row,col],'pixel_center_native':[float(x[col]),float(y[row])],'epsg':epsg,'region_pixels':int(mask.sum()),'radius_m':radius,'observed_series':records,'descriptors_at_selected_pixel':descriptors,'latent_at_selected_pixel':latent,'latent_at_observed_dates':latent_time,'dynamic_latent_at_observed_dates':dynamic,'interval_type':'spatial_block_bootstrap_mean_log_power' if boot else 'unavailable_for_single_or_insufficient_blocks','limitations':['Region is clipped to the observed footprint.','Mean dB is a geometric-power statistic, not 10log10 of mean linear power.','Latent states use all selected dates; the dynamic contrast subtracts a constant-trajectory control, not a causal physical effect.','No event class, sensor-error confidence or future trajectory is inferred.']}),'probe')
