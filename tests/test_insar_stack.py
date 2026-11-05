"""Shared physical datum, component locality, interval and grid gates."""
import unittest
import numpy as np
from surface_dynamics.insar_stack import analyze_stack


def measured_pair(offset=0,label=1,shift=0):
    phase=np.arange(16,dtype='float32').reshape(4,4)+offset
    return {'unwrappedPhase':phase,'unwrapped_coherence':np.full((4,4),.8,dtype='float32'),
            'connectedComponents':np.full((4,4),label,dtype='uint16'),
            'unwrapped_mask':np.full((4,4),11,dtype='uint32'),
            'unwrapped_x':500000+shift+np.arange(4)*80.,'unwrapped_y':5300000-np.arange(4)*80.,
            'unwrapped_epsg':32614,'center_frequency_hz':1239000000.}


def metadata(a,b,track='024'):
    return {'title':f'NISAR_L2_PR_GUNW_026_{track}_A_018_028_4000_SH_{a}T202900_{a}T202934_{b}T202858_{b}T202933_P05023_N_F_J_001',
            'polarization':'HH','sha256':'0'*64}


class StackTests(unittest.TestCase):
    def test_shared_pixel_reference_removes_pair_offsets_without_matching_component_ids(self):
        pairs=[measured_pair(10,label=2),measured_pair(40,label=5)]
        metas=[metadata('20260101','20260113'),metadata('20260113','20260125')]
        ds,observations,quality=analyze_stack(pairs,metas)
        np.testing.assert_allclose(ds.phase_relative_to_shared_pixel_rad[0],ds.phase_relative_to_shared_pixel_rad[1])
        self.assertEqual(quality['common_phase_support_fraction'],1.)
        self.assertFalse(quality['shared_reference']['independently_stable'])
        self.assertEqual(observations[0]['interval_days'],12)
        self.assertEqual(observations[1]['selected_component'],5)

    def test_missing_common_phase_support_is_not_zero_motion(self):
        pairs=[measured_pair(),measured_pair(label=0)]
        ds,observations,quality=analyze_stack(pairs,[metadata('20260101','20260113'),metadata('20260113','20260125')])
        self.assertIsNone(quality['shared_reference']);self.assertTrue(np.isnan(ds.phase_relative_to_shared_pixel_rad).all())
        self.assertEqual(quality['common_coherence_support_fraction'],1.)
        self.assertTrue(np.isfinite(ds.coherence).all())

    def test_pair_intervals_are_not_combined_across_tracks(self):
        with self.assertRaisesRegex(ValueError,'single track'):
            analyze_stack([measured_pair(),measured_pair()],[metadata('20260101','20260113'),metadata('20260113','20260125','125')])

    def test_exact_native_pixel_intersection_preserves_measurements(self):
        ds,_,_=analyze_stack([measured_pair(),measured_pair(shift=80)],[metadata('20260101','20260113'),metadata('20260113','20260125')])
        self.assertEqual(ds.sizes['x'],3);self.assertEqual(float(ds.x[0]),500080)
        np.testing.assert_allclose(ds.coherence,.8)
        with self.assertRaisesRegex(ValueError,'No overlapping native pixel centers'):
            analyze_stack([measured_pair(),measured_pair(shift=40)],[metadata('20260101','20260113'),metadata('20260113','20260125')])

    def test_duplicate_interval_revisions_are_not_time_replication(self):
        meta=metadata('20260101','20260113');other={**meta,'title':meta['title'][:-3]+'002'}
        with self.assertRaisesRegex(ValueError,'Duplicate day-pair'):
            analyze_stack([measured_pair(),measured_pair()],[meta,other])


if __name__=='__main__':unittest.main()
