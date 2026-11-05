"""Observed-pattern semantics, fixed populations and evidence boundaries."""
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch,MagicMock
import numpy as np
from rasterio.warp import transform
from jsonschema.exceptions import ValidationError
from test_flood import cube
from surface_dynamics.behavior import classify_trajectories,summarize,POLICY
from surface_dynamics.characterization import process_characterization,selection_mask
from surface_dynamics.engine import validate_request,validate_output
from surface_dynamics.io import save_cube
from surface_dynamics.analysis import region_series
from surface_dynamics.disturbance import status_masks,EPOCH
from surface_dynamics.ranges import RangeReader
from surface_dynamics.representation_probe import coarsen,fit_knn,predict_knn,score

B={'start_date':'2026-01-01','end_date':'2026-01-25'}
A={'start_date':'2026-02-01','end_date':'2026-03-02'}


class CharacterizationTests(unittest.TestCase):
    def test_repeated_changes_and_gradual_patterns_are_distinct(self):
        values=np.array([[0,0,0,-5,-5,-5,-5,-5],
                         [0,0,0,5,5,5,5,5],
                         [0,-1,-2,-3,-4,-5,-6,-7],
                         [0,0,0,-5,0,-5,0,0],
                         [0,0,0,0,0,0,0,0]],dtype=float).T
        labels=classify_trajectories(values,np.arange(8)*12,[0,1,2],[3,4,5,6,7])
        np.testing.assert_array_equal(labels,[2,3,6,4,1])

    def test_sparse_baseline_and_large_gaps_do_not_invent_gradual_onset(self):
        values=-np.arange(8.)[:,None]
        labels=classify_trajectories(values,np.arange(8)*12,[0],[1,2,3,4,5,6,7]);self.assertEqual(labels[0],0)
        days=np.array([0,12,24,100,112,124,136,148])
        labels=classify_trajectories(values,days,[0,1,2],[3,4,5,6,7]);self.assertNotEqual(labels[0],6)

    def test_common_population_excludes_missing_cells_without_zero_fill(self):
        c=cube();c['valid'][0,0,0,0]=False
        f=summarize(c,B,A)
        self.assertEqual(f['quality']['fixed_common_support_cells'],15)
        self.assertFalse(f['comparison_support'][0,0])
        self.assertEqual(f['pattern_code'][0,0],255)
        self.assertTrue(all(row['fixed_cells']==15 for row in f['series']))
        expected=c['power'][3,0] # physical values below refer to 10log10 input.
        self.assertAlmostEqual(f['channel_summary'][0]['first_assessment_change_db']['mean'],(-4*7)/15)

    def test_polarization_ratio_change_has_correct_db_sign(self):
        f=summarize(cube(),B,A)
        self.assertAlmostEqual(f['polarization_ratio_change']['mean_db'],1.)
        self.assertFalse(f['polarization_ratio_change']['independent_measurement'])

    def test_circle_and_outside_selection_preserve_geography(self):
        c=cube();lon,lat=transform('EPSG:32614','EPSG:4326',[c['x'][1]],[c['y'][1]])
        mask=selection_mask(c,{'longitude':lon[0],'latitude':lat[0],'radius_m':0})
        self.assertEqual(mask.sum(),1);self.assertTrue(mask[1,1])
        self.assertFalse(selection_mask(c,{'longitude':0,'latitude':0}).any())

    def test_worker_contract_and_evidence_links_from_actual_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cube.npz';save_cube(cube(),path)
            request={'contract_version':'1.0','request_id':'observed','theme':'explore',
                     'source':{'mode':'local_cube','cube_path':str(path)},'baseline':B,'assessment':A,'include_methods':False}
            result=process_characterization(request,Path(tmp)/'job');validate_output(result,'characterization')
            self.assertEqual(result['status'],'complete');self.assertEqual(result['evidence']['status'],'not_supplied')
            self.assertIsNone(result['phenomenon_probability']);self.assertIsNone(result['displacement_cm'])
            ids={a['id'] for a in result['artifacts']}
            for insight in result['insights']:self.assertTrue(set(insight['evidence_artifacts'])<=ids)
            for field in ['token','forecast_days','flood_probability']:
                with self.assertRaises(ValidationError):validate_request({**request,field:'unsupported'},'characterization')

    def test_spatial_bootstrap_is_cell_weighted_on_unequal_support(self):
        values=np.zeros((3,1,4,4));mask=np.ones((4,4),bool)
        mask[0,0]=False;values[:,:,0:2,0:2]=10
        # 2x2 blocks require >=80% support; use 5x5 blocks instead, one omitted
        # cell leaves 24/25 support and four accepted blocks.
        values=np.zeros((3,1,10,10));values[:,:,:5,:5]=10
        mask=np.ones((10,10),bool);mask[0,0]=False
        r=region_series(values,mask,block=5)
        self.assertEqual(r['supported_pixels'],99)
        np.testing.assert_allclose(r['mean_db'],240/99)

    def test_optical_disturbance_uses_correct_day_epoch_and_excludes_old_or_stale(self):
        day=int((np.datetime64('2026-08-01')-EPOCH)/np.timedelta64(1,'D'))
        start=int((np.datetime64('2026-07-15')-EPOCH)/np.timedelta64(1,'D'))
        status=np.array([[3,6,7,8,1,0,255]],dtype='uint8')
        onset=np.array([[start,start-1,start,day,start,0,-1]])
        last=np.array([[day,day,day-20,day,day,day,-1]])
        m=status_masks(status,onset,last,'2026-07-15','2026-08-31','2026-08-01')
        np.testing.assert_array_equal(m['confirmed_loss'],[[True,False,False,True,False,False,False]])
        self.assertTrue(m['confirmed_outside_window'][0,1]);self.assertFalse(m['valid_coverage'][0,2])
        self.assertTrue(m['provisional_loss'][0,4]);self.assertTrue(m['non_disturbed'][0,5])

    def test_range_retries_keep_full_response_rejection_and_https_host_gate(self):
        with self.assertRaisesRegex(ValueError,'host'):RangeReader('http://nisar.asf.earthdatacloud.nasa.gov/x','test')
        response=MagicMock();response.__enter__.return_value=response;response.status_code=200
        session=MagicMock();session.get.return_value=response
        with patch('surface_dynamics.ranges.requests.Session',return_value=session):
            with self.assertRaisesRegex(RuntimeError,'200'):RangeReader('https://nisar.asf.earthdatacloud.nasa.gov/x','test')
        response.raw.read.assert_not_called()

    def test_common_posting_averages_linear_power_and_weights_optical_coverage(self):
        c=cube();c['power'][...,0,0]=100;c['valid'][0,0,1,1]=False
        r={}
        for epoch in ['pre','post']:
            r[epoch+'_valid_coverage']=np.ones((4,4));r[epoch+'_valid_coverage'][0,0]=0
            r[epoch+'_open_class_fraction']=np.zeros((4,4));r[epoch+'_open_class_fraction'][0,0]=np.nan
            r[epoch+'_partial_class_fraction']=np.zeros((4,4))
        q,ref=coarsen(c,r,80)
        self.assertFalse(q['valid'][0,0,0,0]);self.assertAlmostEqual(q['power'][0,0,0,0],25.75)
        self.assertAlmostEqual(ref['pre_valid_coverage'][0,0],.75)
        self.assertEqual(ref['pre_open_class_fraction'][0,0],0)
        np.testing.assert_allclose(q['x'],[500040,500120])
        np.testing.assert_allclose(q['y'],[3999980,3999900])

    def test_probe_scaling_uses_development_only_and_absent_classes_stay_undefined(self):
        train=np.array([[0.,0.],[1,1],[10,10],[11,11]])
        model=fit_knn(train,np.array([0,0,1,1]),k=1);center=model['center'].copy()
        prediction=predict_knn(model,np.array([[.2,.1],[10.2,10.1]]))
        np.testing.assert_array_equal(prediction,[0,1]);np.testing.assert_array_equal(model['center'],center)
        r=score(np.array([0,0]),np.array([0,1]));self.assertIsNone(r['recall_by_class'][1])
        self.assertEqual(r['present_reference_classes'],1)


if __name__=='__main__':unittest.main()
