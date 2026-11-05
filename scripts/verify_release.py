"""Independent checks of final real-data products and published contracts."""
from pathlib import Path
import json,hashlib
import numpy as np
import xarray as xr
import rasterio
from PIL import Image
from jsonschema import Draft202012Validator,FormatChecker
from surface_dynamics.io import load_cube
from surface_dynamics.representation import apply_basis
from surface_dynamics.engine import validate_output

ROOT=Path(__file__).resolve().parents[1];cases=json.loads((ROOT/'products/global_examples.json').read_text());checks=[];total=0
checkpoint=ROOT/'models/global_explorer_v1/masked_temporal_transformer.pt';model_hash=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
schema=json.loads((ROOT/'contracts/analysis-result.schema.json').read_text());validator=Draft202012Validator(schema,format_checker=FormatChecker())
for case in cases+[{'case_id':'live-world-point','dates':3}]:
    out=ROOT/'products/runs'/case['case_id'];r=json.loads((out/'result.json').read_text());validator.validate(r)
    model=r['capabilities'].get('transformer',{})
    if model.get('status')=='experimental':model['metadata'].update(checkpoint=checkpoint.name,checkpoint_sha256=model_hash)
    ids={a['id'] for a in r['artifacts']}
    for insight in r['insights']:
        assert set(insight.get('evidence_artifacts',[]))<=ids
    for a in r['artifacts']:
        p=(out/a['href']).resolve();assert p.is_relative_to(out.resolve()) and p.exists()
        assert hashlib.sha256(p.read_bytes()).hexdigest()==a['sha256']
        if p.suffix=='.gif':
            with Image.open(p) as im:assert im.n_frames==case['dates']
        if p.suffix=='.tif':
            with rasterio.open(p) as ds:assert ds.tags(ns='IMAGE_STRUCTURE').get('LAYOUT')=='COG'
    total+=len(r['artifacts'])
    with xr.open_dataset(out/'surface_dynamics.nc') as ds:
        assert ds.sizes['time']==case['dates'];assert 'spatial_ref' in ds
        assert np.all(np.diff(ds.time.values)>np.timedelta64(0,'D'))
        if case['case_id']!='live-world-point':
            cube=load_cube(ROOT/'products/cubes'/(case['case_id']+'.npz'))
            np.testing.assert_allclose(ds.gamma0_power.values,cube['power'],equal_nan=True);np.testing.assert_array_equal(ds.x.values,cube['x']);np.testing.assert_array_equal(ds.y.values,cube['y'])
        if 'learned_state' in ds:
            np.testing.assert_allclose(ds.learned_state.mean('time').values,ds.learned_embedding.values,rtol=2e-5,atol=2e-5,equal_nan=True)
            rgb,_=apply_basis(ds.learned_embedding.values,ROOT/'models/global_explorer_v1/embedding_projection.npz')
            np.testing.assert_allclose(rgb,ds.learned_rgb.values,equal_nan=True)
            assert ds.learned_state.sizes['latent']==48
        with rasterio.open(out/'median_db.tif') as cog:
            np.testing.assert_allclose(cog.read(),ds.median_db.values,rtol=1e-6,atol=1e-6,equal_nan=True)
            assert cog.crs.to_epsg()==ds.attrs['epsg_code'];assert np.allclose(cog.xy(0,0),(ds.x.values[0],ds.y.values[0]))
    (out/'result.json').write_text(json.dumps(r,indent=2,allow_nan=False))
    checks.append({'case':case['case_id'],'status':r['status'],'dates':case['dates'],'artifact_hashes':True,'coordinates_and_values':True,'gif_frame_counts':True,'frozen_global_projection':True})
q=json.loads((ROOT/'products/runs/kumamoto-earthquake/result.json').read_text());validate_output(q,'insar');assert q['los_displacement_cm'] is None
for a in q['artifacts']:assert hashlib.sha256((ROOT/'products/runs/kumamoto-earthquake'/a['href']).read_bytes()).hexdigest()==a['sha256']
total+=len(q['artifacts'])
report={'real_data_jobs_verified':len(checks)+1,'regional_gcov_cases':len(cases),'regional_acquisition_chips':sum(c['dates'] for c in cases),'registered_artifacts':total,'checks':checks,'earthquake_deformation_guard':True,'checkpoint_sha256':model_hash}
(ROOT/'products/verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
