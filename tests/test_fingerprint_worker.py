"""Contract, physical-domain and NoData checks for the reusable worker."""
from pathlib import Path
import copy
import hashlib
import tempfile
import unittest
import numpy as np
import rasterio
from jsonschema import ValidationError
from surface_dynamics.fingerprint_worker import process_fingerprint
from surface_dynamics.engine import validate_output


class FingerprintWorkerTests(unittest.TestCase):
    def inputs(self,root,pair=('HH','HV'),band='L'):
        power=np.full((6,2,6,6),10**(-12/10),dtype='float32');power[:,1]=10**(-20/10)
        power[3:]*=10**(-4/10)
        path=root/'cube.npz'
        np.savez(path,power=power,valid=np.ones_like(power,bool),
            dates=np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-06','2026-02-18','2026-03-02']),
            polarizations=np.array(pair),x=500000+np.arange(6)*160.,y=5300000-np.arange(6)*160.,epsg=32614,radar_band=band)
        return {'contract_version':'1.0','request_id':'fingerprint-test','source':{'mode':'local_cube','cube_path':str(path)},
            'baseline':{'start_date':'2026-01-01','end_date':'2026-01-25'},
            'assessment':{'start_date':'2026-02-06','end_date':'2026-03-02'},'maximum_points':256}

    def test_artifacts_are_registered_with_correct_coordinates_and_no_class_probability(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=self.inputs(root);out=root/'job';r=process_fingerprint(request,out)
            self.assertEqual(r['status'],'complete');self.assertEqual(r['quality']['selected_common_cells'],36)
            self.assertEqual(r['features']['baseline_state_counts']['outside_observed_baseline_bounds'],36)
            self.assertTrue(all(p['normal_probability'] is p['phenomenon_class'] is None for p in r['points']))
            for a in r['artifacts']:
                path=out/a['href'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),a['sha256'])
            with rasterio.open(out/'first_change_db.tif') as ds:
                self.assertEqual(ds.crs.to_epsg(),32614);self.assertEqual(ds.transform.a,160)
                self.assertEqual(ds.transform.c,499920);np.testing.assert_allclose(ds.read(),-4,atol=2e-6)
            bad=copy.deepcopy(r);bad['normal_probability']=.8
            with self.assertRaises(ValidationError):validate_output(bad,'fingerprint')

    def test_one_reference_date_remains_partial_with_null_departure_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=self.inputs(root);request['baseline']['end_date']='2026-01-01'
            r=process_fingerprint(request,root/'job')
            self.assertEqual(r['status'],'partial')
            self.assertEqual(r['features']['baseline_state_counts']['insufficient_baseline'],36)
            self.assertTrue(all(p['maximum_scaled_departure'] is None for p in r['points']))

    def test_missing_window_is_nodata_and_does_not_emit_zero_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=self.inputs(root)
            request['baseline']={'start_date':'2025-01-01','end_date':'2025-02-01'}
            r=process_fingerprint(request,root/'job')
            self.assertEqual(r['status'],'no_data');self.assertEqual(r['points'],[]);self.assertEqual(r['artifacts'],[])

    def test_outside_selection_is_nodata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=self.inputs(root)
            request['selection']={'longitude':0,'latitude':0,'radius_m':0}
            r=process_fingerprint(request,root/'job');self.assertEqual(r['status'],'no_data')

    def test_physical_worker_handles_vv_vh_without_silently_using_hh_hv_encoder(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=self.inputs(root,('VV','VH'),'S')
            r=process_fingerprint(request,root/'job')
            self.assertEqual(r['status'],'complete');self.assertEqual(r['observations']['polarizations'],['VV','VH'])


if __name__=='__main__':unittest.main()
