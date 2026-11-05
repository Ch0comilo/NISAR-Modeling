"""Physical units, independent evidence, timing, masks and contract safeguards."""
from pathlib import Path
import copy,hashlib,json,tempfile,unittest
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from jsonschema.exceptions import ValidationError
from surface_dynamics.engine import validate_request,validate_output
from surface_dynamics.flood import process_flood,load_reference
from surface_dynamics.flood_signatures import (characterize,reference_groups,
    match_controls,proxy_agreement,signal_intervals,spatial_contrast,POLICY)
from surface_dynamics.optical_water import align_classes,select_reference
from surface_dynamics.io import save_cube


def cube():
    db=np.zeros((6,2,4,4));db[3,0,:,:2]=-4;db[3,1,:,:2]=-2
    return {'power':10**(db/10),'valid':np.ones(db.shape,bool),
            'dates':np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-06','2026-02-18','2026-03-02']),
            'polarizations':np.array(['HH','HV']),'x':500020+np.arange(4)*40.,
            'y':4000000-np.arange(4)*40.,'epsg':32614}


def reference():
    r={}
    for epoch in ['pre','post']:
        r[epoch+'_valid_coverage']=np.ones((4,4))
        r[epoch+'_open_class_fraction']=np.zeros((4,4))
        r[epoch+'_partial_class_fraction']=np.zeros((4,4))
    r['post_open_class_fraction'][:,:2]=1
    return r


def features(c=None,r=None):return characterize(c or cube(),'2026-01-01','2026-01-25','2026-02-01','2026-03-02',r)


class FloodTests(unittest.TestCase):
    def test_db_change_and_first_observed_target_have_physical_units(self):
        f=features(r=reference());np.testing.assert_allclose(f['target_change_db'][0,:,:2],-4)
        self.assertEqual(f['candidate_darkening'].sum(),8);self.assertEqual(f['target_index'],3)
        np.testing.assert_allclose(f['darkening_observation_frequency'][:,:2],1/3)
        c=cube();c['power'][3]=1;c['power'][4]=.001
        self.assertEqual(features(c)['candidate_darkening'].sum(),0)

    def test_missing_or_partial_reference_is_unknown_not_nonwater(self):
        r=reference();r['post_valid_coverage'][0,0]=.79
        r['post_partial_class_fraction'][0,1]=1;r['post_open_class_fraction'][0,1]=0
        g=reference_groups(r);self.assertEqual(g[0,0],255);self.assertEqual(g[0,1],4)
        f=features();self.assertEqual(f['interpretation_status'],'insufficient_independent_reference')
        self.assertEqual(f['proxy_agreement']['scored_pixels'],0)
        self.assertIsNone(f['proxy_agreement']['recall_against_proxy'])

    def test_missing_baseline_or_cross_channel_is_not_zero_change(self):
        c=cube();c['valid'][0,0,0,0]=False;c['valid'][3,1,0,1]=False
        f=features(c);self.assertFalse(f['comparison_support'][0,0]);self.assertFalse(f['comparison_support'][0,1])
        self.assertTrue(np.isnan(f['target_change_db'][0,0,0]))

    def test_no_cross_channel_is_explicit_and_not_synthesized(self):
        c=cube();c.update(power=c['power'][:,:1],valid=c['valid'][:,:1],polarizations=np.array(['HH']))
        f=features(c);self.assertIsNone(f['cross_polarization']);self.assertEqual(f['candidate_darkening'].sum(),8)

    def test_matching_uses_only_baseline_and_contrast_only_matched_positives(self):
        levels=np.zeros((2,4,4));positive=np.zeros((4,4),bool);positive[0,:2]=True
        dry=np.zeros_like(positive);dry[1,0]=True
        controls,matched,report=match_controls(levels,positive,dry)
        self.assertEqual(controls.sum(),1);self.assertEqual(matched.sum(),1)
        self.assertEqual(report['matched_fraction'],.5)
        levels[:,0,0]=20
        controls,matched,_=match_controls(levels,positive,dry)
        self.assertFalse(matched[0,0]);self.assertTrue(matched[0,1])
        c=cube();f=features(c,reference());c['power'][3:]*=100
        other=features(c,reference())
        np.testing.assert_array_equal(f['matched_control_mask'],other['matched_control_mask'])
        self.assertEqual(f['matched_contrast']['positive_pixels'],f['matching']['matched_positive_pixels'])

    def test_stable_water_and_water_loss_are_scored_negatives(self):
        groups=np.array([[0,1,2,3,4,255]],dtype='uint8')
        p=proxy_agreement(np.ones(groups.shape,bool),groups,np.ones(groups.shape,bool))
        self.assertEqual(p['scored_pixels'],4);self.assertEqual(p['false_positive_proxy_pixels'],3)
        self.assertEqual(p['precision_against_proxy'],.25);self.assertEqual(p['unscored_reference_or_mixed_pixels'],2)

    def test_change_interval_and_return_need_actual_confirming_acquisitions(self):
        c=cube();s=np.array([0,0,0,-4,0,0.])
        r=signal_intervals(s,c['dates'],[0,1,2],[3,4,5],-1)
        self.assertEqual(r['first_change_interval'],{'after':'2026-01-25','by':'2026-02-06','exact_event_date_estimated':False})
        self.assertEqual(r['signal_return_interval']['confirmation_date'],'2026-03-02')
        self.assertFalse(r['continuous_duration_inferred'])
        s[-1]=np.nan;r=signal_intervals(s,c['dates'],[0,1,2],[3,4,5],-1)
        self.assertIsNone(r['signal_return_interval']);self.assertTrue(r['return_right_censored'])

    def test_small_groups_do_not_receive_spurious_bootstrap_intervals(self):
        d=np.arange(16.).reshape(4,4);a=d<4;b=d>=12
        r=spatial_contrast(d,a,b);self.assertEqual(r['mean_contrast_db'],-12)
        self.assertIsNone(r['spatial_resampling_95_interval_db'])

    def test_spatial_resampling_weights_cells_in_unequal_blocks(self):
        d=np.zeros((4,4));a=np.zeros((4,4),bool);b=np.zeros_like(a)
        a[:2,:2]=True;a[0,2]=True;d[:2,:2]=1;d[0,2]=10;b[2:]=True
        policy={**POLICY,'spatial_block_pixels':2,'minimum_blocks_per_group':1,'bootstrap_draws':400}
        r=spatial_contrast(d,a,b,policy);self.assertAlmostEqual(r['positive_mean_db'],2.8)
        # Four spatial blocks have weights (4,1,0,0) for the positive group.
        blocks=np.array([[4,4,0,0],[10,1,0,0],[0,0,0,4],[0,0,0,4]])
        rng=np.random.default_rng(policy['bootstrap_seed'])
        sums=blocks[rng.integers(0,4,size=(400,4))].sum(1)
        good=(sums[:,1]>0)&(sums[:,3]>0)
        expected=np.quantile(sums[good,0]/sums[good,1]-sums[good,2]/sums[good,3],[.025,.975])
        np.testing.assert_allclose(r['spatial_resampling_95_interval_db'],expected)

    def test_optical_selection_uses_time_and_coverage_without_radar_agreement(self):
        entries=[{'time_start':'2026-02-06T00:00:00Z','title':'near'},
                 {'time_start':'2026-02-08T00:00:00Z','title':'clear'}]
        def fetch(entry,directory):return entry['title'],{'observation_time':entry['time_start']}
        def align(path,c):return {'valid_coverage':np.full((4,4),.1 if path=='near' else 1.)}
        with patch('surface_dynamics.optical_water.fetch_asset',side_effect=fetch),patch('surface_dynamics.optical_water.align_classes',side_effect=align):
            _,selection=select_reference({'entries':entries},cube(),'2026-02-06','unused')
        self.assertEqual(selection['absolute_date_offset_days'],2)
        self.assertEqual(selection['valid_analysis_cell_fraction'],1.)

    def test_optical_clouds_contribute_zero_valid_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'water.tif'
            with rasterio.open(path,'w',driver='GTiff',height=4,width=4,count=1,dtype='uint8',crs='EPSG:32614',transform=from_origin(500000,4000020,30,30),nodata=255) as ds:
                ds.write(np.array([[1,253,0,0],[1,253,0,0],[2,2,0,0],[2,2,0,0]],dtype='uint8'),1)
            target={'x':np.array([500030.,500090.]),'y':np.array([3999990.,3999930.]),'epsg':32614}
            a=align_classes(path,target)
            self.assertAlmostEqual(a['valid_coverage'][0,0],.5)
            self.assertAlmostEqual(a['open_class_fraction'][0,0],1.)
            self.assertAlmostEqual(a['partial_class_fraction'][1,0],1.)

    def test_reference_checksum_grid_and_date_support_are_required(self):
        c=cube();r=reference();r.update(x=c['x'],y=c['y'],epsg=c['epsg'],source_family=np.array('OPERA_DSWX_HLS'),pre_date=np.array('2026-01-25'),post_date=np.array('2026-02-06'))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'r.npz';np.savez_compressed(path,**r)
            path.with_suffix('.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
            ref,info=load_reference(path,c,'2026-01-25','2026-02-06','2026-02-01');self.assertIsNotNone(ref)
            ref,info=load_reference(path,c,'2026-01-25','2026-03-02','2026-02-01');self.assertIsNone(ref)
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum'):load_reference(path,c,'2026-01-25','2026-02-06','2026-02-01')

    def test_contract_rejects_credentials_and_forecast_options(self):
        r={'contract_version':'1.0','request_id':'test','source':{'mode':'local_cube','cube_path':'x.npz'},'baseline':{'start_date':'2026-01-01','end_date':'2026-01-25'},'assessment':{'start_date':'2026-02-01','end_date':'2026-03-02'}}
        validate_request(r,'flood')
        for field in ['token','forecast_days','flood_probability']:
            with self.assertRaises(ValidationError):validate_request({**r,field:'unsupported'},'flood')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cube.npz';save_cube(cube(),path);r['source']['cube_path']=str(path)
            result=process_flood(r,Path(tmp)/'job');validate_output(result,'flood')
            self.assertEqual(result['status'],'partial');self.assertIsNone(result['flood_depth_m'])
            self.assertTrue(all(a['sha256']==hashlib.sha256((Path(tmp)/'job'/a['href']).read_bytes()).hexdigest() for a in result['artifacts']))
            r['baseline']['end_date']='2026-02-02'
            with self.assertRaisesRegex(ValueError,'overlap'):process_flood(r,Path(tmp)/'invalid')


if __name__=='__main__':unittest.main()
