"""Package verified feature exploration, worker and existing measured inputs."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NAME='nisar-surface-features-2026-11-05.zip'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('Commit the reviewable science source snapshot before packaging')
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    paths={ROOT/n for n in names if n}
    previous=ROOT/'deliverables/nisar-umap-exploration-2026-11-05.zip'
    with zipfile.ZipFile(previous) as archive:
        old=json.loads(archive.read('BUNDLE_MANIFEST.json'))
    # Keep the previous bundle intact. Reuse only its measured/provenance and
    # UMAP products, never credentials, raw chips, virtualenvs or mockups.
    for item in old['files']:
        if not item['path'].startswith('products/'):continue
        path=ROOT/item['path']
        if not path.resolve().is_relative_to(ROOT.resolve()) or digest(path)!=item['sha256']:
            raise ValueError('Previously verified input/output snapshot differs: '+item['path'])
        paths.add(path)
    new=ROOT/'products/surface_features';worker=ROOT/'products/surface_features_worker/wheatbelt'
    validation=json.loads((new/'validation.json').read_text())
    if validation['status']!='share_with_caveats':raise ValueError('Run the report verifier before packaging')
    for item in json.loads((new/'artifacts.json').read_text()):
        p=new/item['href'];assert p.resolve().parent==new.resolve() and digest(p)==item['sha256'];paths.add(p)
    paths.update([new/'artifacts.json',new/'validation.json',worker/'request.json',worker/'result.json'])
    for item in json.loads((worker/'result.json').read_text())['artifacts']:
        p=worker/item['href'];assert p.resolve().parent==worker.resolve() and digest(p)==item['sha256'];paths.add(p)
    if any(not p.resolve().is_relative_to(ROOT.resolve()) or p.relative_to(ROOT).parts[0] in {'mockup','.venv','.secrets','scratch'} for p in paths):
        raise ValueError('Unsafe package path')
    ordered=sorted(paths)
    records=[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)} for p in ordered]
    readme=f'''# Observed surface features

Source commit: {source}

Open `products/surface_features/feature_explorer.html` for the clickable measured feature plot, `baseline_comparison.png` for the paired baseline example, and `reference_feature_distributions.png` for the independent water-product comparison. Read `docs/SURFACE_FEATURES.md` for the full methods, data contract and limitations.

The ZIP includes measured inputs, the unchanged encoder, the previous UMAP results, the new cross-region analog report and the physical fingerprint worker example. No archive credentials, server, frontend or mockup are needed for offline use. A departure is relative to the observed baseline, not a confirmed event or calibrated normality probability.

Install the source in a Python 3.13 CPU environment:

```
python -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[exploration]"
python -m pip install -r requirements-exploration-lock.txt
python scripts/verify_surface_features.py
python -m surface_dynamics fingerprint contracts/examples/wheatbelt-fingerprint.json --output products/wheatbelt_fingerprint_reproduced
```

Install `requirements-lock.txt` after CPU Torch for the recorded base runtime. `python scripts/characterize_surface_features.py` regenerates the discovery report from the bundled measured cubes and UMAP point IDs. `BUNDLE_MANIFEST.json` lists per-file SHA256 checks; the separate `.zip.sha256` verifies the complete download.
'''
    destination=ROOT/'deliverables'/NAME;destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in ordered:z.write(p,p.relative_to(ROOT).as_posix())
        z.writestr('START_HERE.md',readme);z.writestr('BUNDLE_MANIFEST.json',json.dumps({'source_commit':source,'files':records},indent=2))
    sha=digest(destination);destination.with_suffix('.zip.sha256').write_text(sha+'  '+NAME+'\n')
    print(json.dumps({'file':NAME,'bytes':destination.stat().st_size,'sha256':sha,'files':len(records),'source_commit':source},indent=2))
    return destination


if __name__=='__main__':run()
