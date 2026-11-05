"""Release input integrity and user-file preservation; no satellite download."""
from pathlib import Path
import hashlib,importlib.util,json,tempfile,unittest,zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fixture_restore',ROOT/'scripts/download_examples.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


def archive(path,files,wrong_hash=False):
    records=[]
    with zipfile.ZipFile(path,'w') as z:
        for name,data in files.items():
            z.writestr(name,data);records.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        if wrong_hash:records[-1]['sha256']='0'*64
        z.writestr('BUNDLE_INVENTORY.json',json.dumps(records));z.writestr('FIXTURE_INVENTORY.json',json.dumps(list(files)))


class FixtureRestoreTests(unittest.TestCase):
    def test_only_curated_measured_input_paths_can_be_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);path=folder/'data.zip';archive(path,{'src/injected.py':b'not executable input'})
            with self.assertRaisesRegex(ValueError,'allowlist'):helper.restore(path,folder/'output')
            self.assertFalse((folder/'output/src/injected.py').exists())

    def test_all_hashes_are_checked_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);path=folder/'data.zip';archive(path,{'products/cubes/a.npz':b'first','products/cubes/b.npz':b'second'},wrong_hash=True)
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):helper.restore(path,folder/'output')
            self.assertFalse((folder/'output/products/cubes/a.npz').exists())

    def test_different_existing_measurements_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);path=folder/'data.zip';archive(path,{'products/cubes/a.npz':b'new input'})
            existing=folder/'output/products/cubes/a.npz';existing.parent.mkdir(parents=True);existing.write_bytes(b'user measurements')
            with self.assertRaisesRegex(ValueError,'Refusing to overwrite'):helper.restore(path,folder/'output')
            self.assertEqual(existing.read_bytes(),b'user measurements')


if __name__=='__main__':unittest.main()
