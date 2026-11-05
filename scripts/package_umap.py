"""Build a compact offline exploration bundle, preserving all measured inputs."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile
from surface_dynamics.embedding_atlas import case_inventory

ROOT = Path(__file__).resolve().parents[1]


def run():
    output = ROOT/'products/representation_umap'
    paths = set(output.iterdir())
    for folder in ['src/surface_dynamics', 'configs', 'models/global_explorer_v2']:
        paths.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for name in ['README.md', 'pyproject.toml', 'requirements-lock.txt', 'requirements-exploration-lock.txt',
                 'docs/REPRESENTATION_UMAP.md', 'scripts/explore_representations.py',
                 'scripts/verify_representation_umap.py', 'scripts/package_umap.py']:
        paths.add(ROOT/name)
    for name in ['trajectory_umap.png', 'change_umap.png', 'change_neighborhood_examples.png']:
        paths.add(ROOT/'docs/images'/name)
    # All inputs for the atlas and for the previous experiment-freeze checks.
    for case in case_inventory(ROOT):
        cube = ROOT/case['cube_path']; paths.add(cube)
        paths.update(p for p in [cube.with_name('reference_pair.npz'), cube.with_name('reference_pair.json')] if p.exists())
    old = ROOT/'products/characterization_v05/probe_freeze.json'; paths.add(old)
    paths.update(ROOT/p for p in json.loads(old.read_text())['files'])
    paths = sorted(p for p in paths if p.is_file())
    if any(not p.resolve().is_relative_to(ROOT.resolve()) for p in paths):
        raise ValueError('Bundle paths must stay inside the science workspace')
    records = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    readme = f'''# Frozen Transformer UMAP exploration

Scientific source snapshot: {source}

Open products/representation_umap/trajectory_interactive.html or change_interactive.html directly in your browser. Click points to inspect actual HH/HV histories. No server, archive credentials or CDN are needed. The measured cubes, references, frozen encoder, source and full scientific outputs are included.

Read docs/REPRESENTATION_UMAP.md for findings, masks, dimensions and limits. All existing scenes are exploratory data; groups are not phenomenon classes or a held-out evaluation. The frontend and mockup are excluded.

To verify or reproduce, create a Python 3.13 environment, install CPU Torch and the package/exploration dependencies, then run:

python -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[exploration]"
python -m pip install -r requirements-exploration-lock.txt
python scripts/verify_representation_umap.py
python scripts/explore_representations.py --reuse-features

For exact version reproduction, install requirements-lock.txt after CPU Torch, then requirements-exploration-lock.txt. The original frozen experiment files are preserved. BUNDLE_MANIFEST.json lists every included file's SHA256; the ZIP's companion .sha256 file checks the complete download.
'''
    destination = ROOT/'deliverables/nisar-umap-exploration-2026-11-05.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in paths: archive.write(p, p.relative_to(ROOT).as_posix())
        archive.writestr('START_HERE.md', readme)
        archive.writestr('BUNDLE_MANIFEST.json', json.dumps({'source_commit': source, 'files': records}, indent=2))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest+'  '+destination.name+'\n')
    print(json.dumps({'path': str(destination.relative_to(ROOT)), 'bytes': destination.stat().st_size,
                      'sha256': digest, 'files': len(records), 'source_commit': source}, indent=2))
    return destination


if __name__ == '__main__': run()
