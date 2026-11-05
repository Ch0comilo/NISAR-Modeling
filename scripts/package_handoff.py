"""Portable science handoff; excludes UI folders, credentials and virtualenvs."""
from pathlib import Path
import json,hashlib,zipfile,tomllib
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'deliverables';out.mkdir(exist_ok=True)
paths=[]
for name in ['pyproject.toml','requirements-lock.txt','TEAM_HANDOFF.md','SCIENCE_IMPLEMENTATION.md']:
    paths.append(ROOT/name)
for directory in ['src/surface_dynamics','contracts','models/global_explorer_v1','configs','tests','docs']:
    paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
paths.extend((ROOT/'products/cubes').glob('*.npz'))
for name in ['products/insar/kumamoto_pair.npz','products/insar/kumamoto_pair.json','products/verification.json','products/global_surface_atlas.png','products/transformer_validation.png','products/runs/kumamoto-earthquake/interferometry.png','products/runs/forest_river/learned_evolution.gif','products/model_benchmark_all_dates.json','products/networks/kumamoto/result.json','scripts/train_global_example.py','scripts/run_global_examples.py','scripts/finalize_science_artifacts.py','scripts/verify_release.py','scripts/benchmark_global_model.py','scripts/download_examples.py']:
    paths.append(ROOT/name)
archive=out/'nisar-science-teammate-handoff.zip'
inventory=[]
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as z:
    z.writestr('README.md','# NISAR science teammate handoff\n\nStart with TEAM_HANDOFF.md for contracts and SCIENCE_IMPLEMENTATION.md for methods/results. Install the wheel in dist/ or install the included editable source. Trained weights, seven real measured cubes and a real earthquake pair are included; no token is required for offline examples. Re-run scripts/run_global_examples.py to generate the full artifact sets. The recorded verification describes the reference run on the authoring host. Frontend code, credentials, virtualenvs and the optional unverified-unit GSI raster are excluded.\n')
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    wheels=list((ROOT/'dist').glob(f'nisar_surface_dynamics-{version}-*.whl'))
    assert len(wheels)==1,'Build the current version wheel before packaging'
    paths.extend(wheels)
    for p in sorted(set(paths)):
        assert p.resolve().is_relative_to(ROOT.resolve())
        assert p.exists(),p
        name=p.relative_to(ROOT).as_posix();data=p.read_bytes();z.writestr(name,data)
        inventory.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    z.writestr('BUNDLE_INVENTORY.json',json.dumps(inventory,indent=2))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert not any(any(x in n for x in ['mockup/','nisar-hazard-watch','.secrets/','.venv/']) for n in z.namelist())
record={'archive':archive.name,'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'included_files':len(inventory)}
(out/'handoff_manifest.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
