"""Portable science handoff; excludes UI folders, credentials and virtualenvs."""
from pathlib import Path
import json,hashlib,zipfile,tomllib
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'deliverables';out.mkdir(exist_ok=True)
paths=[]
for name in ['pyproject.toml','requirements-lock.txt','TEAM_HANDOFF.md','SCIENCE_IMPLEMENTATION.md']:
    paths.append(ROOT/name)
for directory in ['src/surface_dynamics','contracts','models/global_explorer_v1','models/global_explorer_v2','configs','tests','docs']:
    paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
paths.extend((ROOT/'products/cubes').glob('*.npz'))
fixtures=list((ROOT/'products/cubes').glob('*.npz'))+[ROOT/'products/insar/kumamoto_pair.npz',ROOT/'products/insar/kumamoto_pair.json']
fixtures.extend((ROOT/'products/global_v2/cubes').glob('*.npz'));fixtures.append(ROOT/'products/global_v2/data_inventory.json')
for case in ['kumamoto','kumamoto_wide']:
    fixtures.extend((ROOT/'products/insar_stacks'/case/'cache').glob('pair_*.npz'))
    fixtures.extend((ROOT/'products/insar_stacks'/case/'cache').glob('pair_*.json'))
for case in ['chokwe','qadirabad','richmond']:
    fixtures.extend(ROOT/'products/flood_pilot/cases'/case/name for name in ['cube.npz','reference_pair.npz','reference_pair.json','admission.json'])
paths.extend(fixtures)
paths.extend(p for p in (ROOT/'scripts').glob('*.py') if p.name!='live_catalog_smoke.py')
for name in ['products/insar/kumamoto_pair.npz','products/insar/kumamoto_pair.json','products/verification.json','products/global_surface_atlas.png','products/transformer_validation.png','products/runs/kumamoto-earthquake/interferometry.png','products/runs/forest_river/learned_evolution.gif','products/model_benchmark_all_dates.json','products/networks/kumamoto/result.json','scripts/train_global_example.py','scripts/run_global_examples.py','scripts/finalize_science_artifacts.py','scripts/verify_release.py','scripts/benchmark_global_model.py','scripts/download_examples.py']:
    paths.append(ROOT/name)
archive=out/'nisar-science-teammate-handoff.zip'
inventory=[]
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as z:
    z.writestr('README.md','# NISAR science teammate handoff v0.4.0\n\nStart with TEAM_HANDOFF.md and docs/CHARACTERIZATION_CONTRACTS.md. Install the wheel in dist/ or install the included source. Both frozen models, thirteen common-posting regional cubes, legacy inputs, measured three-pair InSAR windows and three flood-pilot cubes with independent optical evidence are included. No token is needed for offline examples. Run scripts/download_examples.py to verify bundled fixtures, scripts/run_flood_pilot.py and scripts/run_insar_readiness.py to generate the 33 new artifacts, then scripts/verify_flood_pilot.py. The prior scripts/run_global_v2.py and scripts/verify_global_v2.py remain available. See PROJECT_README.md for the repository overview. Recorded verification describes the authoring-host reference run. Frontend/mockup files, credentials, virtualenvs, full generated datasets, raw optical tiles and the unverified-unit external GSI raster are excluded.\n')
    z.writestr('PROJECT_README.md',(ROOT/'README.md').read_bytes())
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    wheels=list((ROOT/'dist').glob(f'nisar_surface_dynamics-{version}-*.whl'))
    assert len(wheels)==1,'Build the current version wheel before packaging'
    paths.extend(wheels)
    for p in sorted(set(paths)):
        assert p.resolve().is_relative_to(ROOT.resolve())
        assert p.exists(),p
        name=p.relative_to(ROOT).as_posix();data=p.read_bytes();z.writestr(name,data)
        inventory.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    fixture_names=sorted(p.relative_to(ROOT).as_posix() for p in set(fixtures))
    bundled={'mode':'bundled','fixtures':[r for r in inventory if r['path'] in fixture_names]}
    data=json.dumps(bundled,indent=2).encode();z.writestr('data/example_bundle.json',data)
    inventory.append({'path':'data/example_bundle.json','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    z.writestr('FIXTURE_INVENTORY.json',json.dumps(fixture_names,indent=2))
    z.writestr('BUNDLE_INVENTORY.json',json.dumps(inventory,indent=2))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert not any(any(x in n for x in ['mockup/','nisar-hazard-watch','.secrets/','.venv/']) for n in z.namelist())
record={'archive':archive.name,'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'included_files':len(inventory),'measured_fixture_files':len(fixture_names)}
(out/'handoff_manifest.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
