"""Scientific safeguards for local baselines and matched cross-region analogs."""
import unittest
import numpy as np
from surface_dynamics.surface_features import from_db,pixel_summary
from surface_dynamics.behavior_analogs import matched_window,sampling,eligible,fit_scaling


class SurfaceFeatureTests(unittest.TestCase):
    dates=np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-06','2026-03-02','2026-03-14'])

    def data(self):
        a=np.full((6,2,5,5),-12.);a[:,1]=-20.
        return a

    def test_assessment_values_never_fit_the_baseline_or_bounds(self):
        a=self.data();a[3:]-=4
        one=from_db(a,self.dates,[0,1,2],[3,4,5]);a[3:]=100
        other=from_db(a,self.dates,[0,1,2],[3,4,5])
        for name in ['baseline_db','baseline_mad_db','diagnostic_half_width_db']:
            np.testing.assert_array_equal(one['maps'][name],other['maps'][name])
        self.assertEqual(one['maps']['departure_frequency'][2,2],1)
        self.assertEqual(pixel_summary(one,2,2)['normal_probability'],None)

    def test_equal_change_can_have_different_baseline_scaled_departure(self):
        a=self.data();a[3:]-=4
        a[0,:,0,0]-=5;a[2,:,0,0]+=5
        r=from_db(a,self.dates,[0,1,2],[3,4,5])['maps']
        self.assertEqual(r['state_code'][2,2],2)
        self.assertEqual(r['state_code'][0,0],1)
        self.assertEqual(r['first_change_db'][0,0,0],r['first_change_db'][0,2,2])

    def test_insufficient_baseline_does_not_become_zero_or_normal(self):
        r=from_db(self.data(),self.dates,[0],[1,2,3,4,5])
        p=pixel_summary(r,2,2)
        self.assertEqual(p['state'],'insufficient_baseline')
        self.assertIsNone(p['maximum_scaled_departure']);self.assertIsNone(p['observed_departure_fraction'])
        self.assertEqual(p['first_change_db'],[0.,0.])

    def test_missing_selected_measurement_excludes_cell_at_every_date(self):
        a=self.data();a[4,1,2,2]=np.nan
        r=from_db(a,self.dates,[0,1,2],[3,4,5]);m=r['maps']
        self.assertFalse(m['common_support'][2,2]);self.assertEqual(m['state_code'][2,2],255)
        self.assertTrue(np.isnan(m['baseline_db'][:,2,2]).all())
        self.assertEqual(pixel_summary(r,2,2)['state'],'unsupported')

    def test_persistence_counts_acquisitions_not_intervening_days(self):
        a=self.data();a[3]-=4;a[5]-=4
        p=pixel_summary(from_db(a,self.dates,[0,1,2],[3,4,5]),2,2)
        self.assertEqual(p['outside_baseline_observations'],2)
        self.assertAlmostEqual(p['observed_departure_fraction'],2/3)
        self.assertFalse(p['continuous_duration_inferred'])
        self.assertEqual(p['first_departure_interval']['after'],'2026-01-25')
        self.assertEqual(p['first_departure_interval']['by'],'2026-02-06')

    def test_local_contrast_excludes_center_and_unsupported_neighbors(self):
        a=self.data();a[3:,0,2,2]-=6
        m=from_db(a,self.dates,[0,1,2],[3,4,5])['maps']
        self.assertEqual(m['neighbor_co_change_db'][2,2],0)
        self.assertEqual(m['local_co_contrast_db'][2,2],-6)
        self.assertTrue(np.isnan(m['local_co_contrast_db'][0,0]))

    def test_ratio_is_computed_per_observation_before_temporal_median(self):
        a=self.data();a[:3,0]=np.array([-10,-5,-15])[:,None,None]
        a[:3,1]=np.array([-20,-25,-15])[:,None,None]
        a[3:,0]=-10;a[3:,1]=-18
        m=from_db(a,self.dates,[0,1,2],[3,4,5])['maps']
        self.assertEqual(m['ratio_change_db'][2,2],2)
        self.assertEqual(m['first_ratio_change_db'][2,2],2)

    def test_overlapping_or_nonincreasing_windows_are_rejected(self):
        with self.assertRaises(ValueError):from_db(self.data(),self.dates,[0,1,2],[2,3,4])
        with self.assertRaises(ValueError):from_db(self.data(),self.dates,[0,2,1],[3,4,5])

    def test_analog_windows_use_actual_dates_and_keep_partial_cases_out(self):
        self.assertIsNone(matched_window(self.dates,[0],[1,2,3,4,5]))
        np.testing.assert_array_equal(matched_window(self.dates,[0,1,2],[3,4,5]),np.arange(6))
        self.assertEqual(sampling(self.dates)['boundary_gap_days'],12)
        self.assertEqual(sampling(self.dates)['span_days'],72)

    def test_sampling_and_geographic_gates_are_independent_of_labels(self):
        p={'minimum_region_distance_km':200,'maximum_span_ratio':1.3,'maximum_phase_span_ratio':1.5,
           'maximum_boundary_gap_ratio':2,'maximum_normalized_gap_l1':.4}
        a={'case_id':'one','longitude':0,'latitude':0,'sampling':sampling(self.dates)}
        b={**a,'case_id':'other','longitude':10,'event_label':'ignored'}
        self.assertTrue(eligible(a,b,p))
        self.assertFalse(eligible(a,{**b,'longitude':.1},p))
        altered=np.array(self.dates,dtype='datetime64[D]');altered=altered[0]+(altered-altered[0])*3
        self.assertFalse(eligible(a,{**b,'sampling':sampling(altered)},p))

    def test_scaling_is_reference_only_and_balanced_by_scene(self):
        x=np.array([[0.],[0.],[0.],[10.],[10000.]])
        meta=[{'case_id':s} for s in ['a','a','a','b','query']]
        scale=fit_scaling(x,meta,['a','b'],.1)
        self.assertEqual(scale['center'][0],5)
        self.assertEqual(scale['scale'][0],5)
        x[-1]=-10000
        np.testing.assert_array_equal(scale['center'],fit_scaling(x,meta,['a','b'],.1)['center'])


if __name__=='__main__':unittest.main()
