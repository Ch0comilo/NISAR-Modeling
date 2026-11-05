"""Audit actual v2 arrays, coordinate semantics, projections and InSAR guards."""
from pathlib import Path
import hashlib,json
import numpy as np
import xarray as xr
import rasterio
from PIL import Image
from surface_dynamics.engine import validate_output
from surface_dynamics.io import load_cube
from surface_dynamics.representation import apply_basis
from surface_dynamics.analysis import measured_db,supported_reduction
from surface_dynamics.probe import probe

ROOT=Path(__file__).resolve().parents[1]


def run():
    model=ROOT/'models/global_explorer_v2';freeze=json.loads((model/'evaluation_freeze.json').read_text())
    assert hashlib.sha256((model/'masked_temporal_transformer.pt').read_bytes()).hexdigest()==freeze['checkpoint_sha256']
    assert hashlib.sha256((ROOT/'src/surface_dynamics/temporal_v2.py').read_bytes()).hexdigest()==freeze['architecture_source_sha256'],'Prediction architecture changed after test freeze'
    inventory=json.loads((ROOT/'products/global_v2/job_inventory.json').read_text());total=0;checked=[]
    for item in inventory:
        name=item['case_id'];out=ROOT/'products/global_v2/runs'/name;r=json.loads((out/'result.json').read_text());validate_output(r,'analysis')
        cube=load_cube(ROOT/'products/global_v2/cubes'/f'{name}.npz')
        assert np.min(cube['power'][cube['valid']])>1e-12,'Small-power log fix would affect benchmark preprocessing; review frozen results'
        assert r['capabilities']['transformer']['metadata']['checkpoint_sha256']==freeze['checkpoint_sha256']
        for a in r['artifacts']:
            p=(out/a['href']).resolve();assert p.is_relative_to(out.resolve()) and p.exists()
            assert hashlib.sha256(p.read_bytes()).hexdigest()==a['sha256']
            if p.suffix=='.gif':
                with Image.open(p) as im:assert im.n_frames==len(cube['dates'])
        with xr.open_dataset(out/'surface_dynamics.nc') as ds:
            np.testing.assert_allclose(ds.gamma0_power.values,cube['power'],equal_nan=True)
            np.testing.assert_allclose(ds.gamma0_db.values,measured_db(cube),rtol=1e-6,atol=1e-6,equal_nan=True)
            np.testing.assert_array_equal(ds.x.values,cube['x']);np.testing.assert_array_equal(ds.y.values,cube['y'])
            assert np.all(np.diff(ds.x.values)==160) and np.all(np.diff(ds.y.values)==-160)
            np.testing.assert_allclose(ds.learned_state.mean('time'),ds.learned_embedding,rtol=2e-5,atol=2e-5,equal_nan=True)
            rgb,_=apply_basis(ds.dynamic_state.values,model/'dynamic_projection.npz')
            np.testing.assert_allclose(rgb,ds.dynamic_rgb_time.values,rtol=1e-5,atol=1e-5,equal_nan=True)
            rate=np.sqrt(np.mean(np.diff(ds.dynamic_state.values,axis=0)**2,axis=-1))/np.diff(np.array(cube['dates'],dtype='datetime64[D]')).astype('timedelta64[D]').astype(float)[:,None,None]
            np.testing.assert_allclose(rate,ds.dynamic_context_change_per_day.values,rtol=1e-5,atol=1e-5,equal_nan=True)
            with rasterio.open(out/'maximum_controlled_state_change.tif') as cog:
                np.testing.assert_allclose(cog.read(1),supported_reduction(rate,np.nanmax),rtol=1e-5,atol=1e-5,equal_nan=True)
                assert cog.crs.to_epsg()==int(cube['epsg']);assert np.allclose(cog.xy(0,0),(cube['x'][0],cube['y'][0]))
        total+=len(r['artifacts']);checked.append({'case_id':name,'dates':len(cube['dates']),'status':r['status'],'artifacts':len(r['artifacts'])})
    for name in ['kumamoto','kumamoto_wide']:
        out=ROOT/'products/insar_stacks'/name;r=json.loads((out/'result.json').read_text());validate_output(r,'insar-stack');assert r['los_displacement_cm'] is None
        for a in r['artifacts']:assert hashlib.sha256((out/a['href']).read_bytes()).hexdigest()==a['sha256']
        with xr.open_dataset(out/'interferometric_interval_dynamics.nc') as ds:
            assert ds.sizes['pair']==3
            if r['quality']['shared_reference']:
                row,col=r['quality']['shared_reference']['pixel'];np.testing.assert_allclose(ds.phase_relative_to_shared_pixel_rad.values[:,row,col],0)
                assert not r['quality']['shared_reference']['independently_stable']
            else:assert np.isnan(ds.phase_relative_to_shared_pixel_rad.values).all()
        total+=len(r['artifacts'])
    probe_results={}
    for case,longitude,latitude in [('hokkaido',142.5,43.5),('mekong',105.7,10.5)]:
        r=probe({'contract_version':'1.0','request_id':case+'-v2-probe','dataset_path':str(ROOT/'products/global_v2/runs'/case/'surface_dynamics.nc'),'longitude':longitude,'latitude':latitude})
        (ROOT/'products/global_v2'/f'{case}_probe.json').write_text(json.dumps(r,indent=2,allow_nan=False));probe_results[case]=r['status']
    assert probe_results=={'hokkaido':'complete','mekong':'no_valid_observations'}
    report={'new_scientific_jobs':len(inventory)+2,'regional_cases':len(inventory),'accepted_regional_dates':sum(c['dates'] for c in checked),
            'registered_artifacts_verified':total,'checkpoint_and_prediction_source_frozen':True,
            'values_coordinates_fixed_projections_and_gifs_verified':True,'interferometric_displacement_guard':True,
            'point_probe_outcomes':probe_results,'checks':checked}
    (ROOT/'docs/results/global_v2_verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':run()
