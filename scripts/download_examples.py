"""Restore only verified measured fixtures from the published release bundle."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,tempfile,zipfile,re
import requests

ROOT=Path(__file__).resolve().parents[1]


def allowed_fixture(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:return False
    if path.parent.as_posix() in ['products/cubes','products/global_v2/cubes'] and path.suffix=='.npz':return True
    if name in ['products/insar/kumamoto_pair.npz','products/insar/kumamoto_pair.json','products/global_v2/data_inventory.json']:return True
    if path.parent.as_posix() in ['products/flood_pilot/cases/'+case for case in ['chokwe','qadirabad','richmond']]:
        return path.name in ['cube.npz','reference_pair.npz','reference_pair.json','admission.json']
    if path.parent.as_posix() in ['products/characterization_v05/cases/'+case for case in ['rajshahi','gimcheon','benguerir','wheatbelt']]:
        return path.name in ['cube.npz','reference_pair.npz','reference_pair.json','admission.json']
    if path.parent.as_posix()=='products/characterization_v05/cases/rowe_creek':
        return path.name in ['cube.npz','admission.json','disturbance_reference.npz','disturbance_reference.json']
    if path.parent.as_posix()=='products/characterization_v05/insar_candidate':
        return path.name in ['catalog.json','selection_freeze.json']
    if path.parent.as_posix()=='products/characterization_v05/insar_candidate/cache':
        return re.fullmatch(r'pair_[0-2]\.(npz|json)',path.name) is not None
    if path.parent.as_posix()=='products/characterization_v05':
        return path.name in ['protocol_freeze.json','probe_freeze.json']
    return path.parent.as_posix() in ['products/insar_stacks/kumamoto/cache','products/insar_stacks/kumamoto_wide/cache'] and re.fullmatch(r'pair_[0-5]\.(npz|json)',path.name) is not None


def restore(archive,root=ROOT):
    root=Path(root).resolve()
    with zipfile.ZipFile(archive) as z:
        records={r['path']:r for r in json.loads(z.read('BUNDLE_INVENTORY.json'))}
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive members')
        if 'FIXTURE_INVENTORY.json' in names:selected=json.loads(z.read('FIXTURE_INVENTORY.json'))
        else:
            selected=[n for n in names if (n.startswith('products/cubes/') and n.endswith('.npz')) or n in ['products/insar/kumamoto_pair.npz','products/insar/kumamoto_pair.json']]
            if len(selected)!=9:raise ValueError('Expected legacy seven cubes and one measured pair with provenance')
        if not 1<=len(selected)<=128 or len(selected)!=len(set(selected)):raise ValueError('Invalid measured fixture inventory')
        verified=[];total_bytes=0
        for name in selected:
            if not allowed_fixture(name):raise ValueError('Archive member is outside the measured-input allowlist')
            relative=PurePosixPath(name)
            destination=(root/Path(*relative.parts)).resolve()
            if not destination.is_relative_to(root):raise ValueError('Archive escapes destination')
            record=records[name]
            if not 0<=record['bytes']<=25*1024*1024 or z.getinfo(name).file_size!=record['bytes']:raise ValueError('Unexpected fixture size')
            total_bytes+=record['bytes']
            if total_bytes>128*1024*1024:raise ValueError('Total curated fixture payload exceeds bounded restore budget')
            data=z.read(name)
            if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('Fixture checksum mismatch')
            if destination.exists() and destination.read_bytes()!=data:raise ValueError(f'Refusing to overwrite different measured data: {name}')
            verified.append((destination,data))
        # Validate every fixture before writing any of them; source/model/UI files
        # from the bundle are never extracted by this helper.
        for destination,data in verified:
            destination.parent.mkdir(parents=True,exist_ok=True)
            temp=destination.with_name(destination.name+'.partial');temp.write_bytes(data);temp.replace(destination)
    return {'restored_files':len(verified),'credentials_required':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive');args=parser.parse_args()
    if args.archive:print(json.dumps(restore(args.archive)));return
    manifest=json.loads((ROOT/'data/example_bundle.json').read_text())
    if manifest.get('mode')=='bundled':
        for record in manifest['fixtures']:
            if not allowed_fixture(record['path']):raise ValueError('Unsafe bundled input path')
            path=(ROOT/record['path']).resolve()
            if not path.is_relative_to(ROOT) or path.stat().st_size!=record['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Bundled fixture checksum mismatch')
        print(json.dumps({'verified_bundled_files':len(manifest['fixtures']),'credentials_required':False}));return
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'examples.zip';digest=hashlib.sha256();size=0
        with requests.get(manifest['url'],stream=True,timeout=(10,60)) as response:
            response.raise_for_status()
            with path.open('wb') as stream:
                for chunk in response.iter_content(1024*1024):
                    size+=len(chunk)
                    if size>80*1024*1024:raise ValueError('Release bundle exceeds download budget')
                    digest.update(chunk);stream.write(chunk)
        if size!=manifest['bytes'] or digest.hexdigest()!=manifest['sha256']:raise ValueError('Release bundle size/checksum mismatch')
        print(json.dumps(restore(path)))


if __name__=='__main__':main()
