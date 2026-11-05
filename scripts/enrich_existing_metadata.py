"""Add explicit CF CRS and interpretation attributes without changing scientific values."""
from pathlib import Path
import json,hashlib
import h5netcdf
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT/'products/runs').glob('*/surface_dynamics.nc'):
    with h5netcdf.File(path,'a') as f:
        wkt=f.attrs['spatial_ref'];epsg=int(f.attrs['epsg_code'])
        if 'spatial_ref' not in f.variables:f.create_variable('spatial_ref',(),data=np.int32(0))
        f.variables['spatial_ref'].attrs.update({'spatial_ref':wkt,'crs_wkt':wkt,'epsg_code':f'EPSG:{epsg}'})
        for name,v in f.variables.items():
            if 'x' in v.dimensions and 'y' in v.dimensions:v.attrs['grid_mapping']='spatial_ref'
        f.variables['gamma0_power'].attrs.update({'units':'1','radiometry':'linear gamma0; not amplitude, dB or RGB'})
        if 'learned_state' in f.variables:f.variables['learned_state'].attrs.update({'units':'latent coordinate','context':'bidirectional context of all selected dates; derived representation, not a forecast'})
        if 'learned_rgb_time' in f.variables:f.variables['learned_rgb_time'].attrs.update({'units':'1','projection':'frozen training-only global latent PCA and scaling; not class colors'})
        if 'gp_conditional_sd_db' in f.variables:f.variables['gp_conditional_sd_db'].attrs.update({'units':'dB','uncertainty_type':'conditional empirical-Bayes latent SD; hyperparameter/model uncertainty omitted; not calibrated sensor noise'})
    manifest=path.parent/'result.json'
    if manifest.exists():
        r=json.loads(manifest.read_text())
        for a in r['artifacts']:a['sha256']=hashlib.sha256((path.parent/a['href']).read_bytes()).hexdigest()
        manifest.write_text(json.dumps(r,indent=2,allow_nan=False))
print('NETCDF_METADATA_ENRICHED')
