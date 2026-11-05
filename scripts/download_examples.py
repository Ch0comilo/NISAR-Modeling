"""Restore only verified measured fixtures from the published release bundle."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,tempfile,zipfile
import requests

ROOT=Path(__file__).resolve().parents[1]


def restore(archive,root=ROOT):
    root=Path(root).resolve()
    with zipfile.ZipFile(archive) as z:
        records={r['path']:r for r in json.loads(z.read('BUNDLE_INVENTORY.json'))}
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive members')
        selected=[n for n in names if (n.startswith('products/cubes/') and n.endswith('.npz')) or n in ['products/insar/kumamoto_pair.npz','products/insar/kumamoto_pair.json']]
        if len(selected)!=9:raise ValueError('Expected seven cubes and one measured pair with provenance')
        verified=[]
        for name in selected:
            relative=PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name:raise ValueError('Unsafe archive member')
            destination=(root/Path(*relative.parts)).resolve()
            if not destination.is_relative_to(root):raise ValueError('Archive escapes destination')
            record=records[name]
            if record['bytes']>25*1024*1024 or z.getinfo(name).file_size!=record['bytes']:raise ValueError('Unexpected fixture size')
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
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'examples.zip';digest=hashlib.sha256();size=0
        with requests.get(manifest['url'],stream=True,timeout=(10,60)) as response:
            response.raise_for_status()
            with path.open('wb') as stream:
                for chunk in response.iter_content(1024*1024):
                    size+=len(chunk)
                    if size>50*1024*1024:raise ValueError('Release bundle exceeds download budget')
                    digest.update(chunk);stream.write(chunk)
        if size!=manifest['bytes'] or digest.hexdigest()!=manifest['sha256']:raise ValueError('Release bundle size/checksum mismatch')
        print(json.dumps(restore(path)))


if __name__=='__main__':main()
