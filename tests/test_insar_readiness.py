"""Correction support is not stable ground or a deformation estimate."""
import unittest
import numpy as np
from test_insar_stack import measured_pair,metadata
from surface_dynamics.insar_readiness import audit_corrections


class ReadinessTests(unittest.TestCase):
    def data(self):
        pairs=[measured_pair(),measured_pair(label=5)]
        for p in pairs:
            p['ionospherePhaseScreen']=np.ones((4,4),dtype='float32')*4
            p['ionospherePhaseScreenUncertainty']=np.ones((4,4),dtype='float32')*.8
        return pairs,[metadata('20260101','20260113'),metadata('20260113','20260125')]

    def test_fill_and_nonpositive_uncertainty_excluded_on_each_pair(self):
        pairs,meta=self.data();pairs[0]['unwrapped_mask'][0,0]|=1<<24
        pairs[1]['ionospherePhaseScreenUncertainty'][1,1]=0
        ds,report,q=audit_corrections(pairs,meta)
        self.assertEqual(q['common_phase_support_pixels'],16)
        self.assertEqual(q['common_correction_input_pixels'],14)
        self.assertEqual(report[0]['eligible_fraction_of_coherent_support'],15/16)
        self.assertFalse(q['candidate_reference']['independently_stable'])
        self.assertFalse(q['eligibility_is_correction_validation'])
        np.testing.assert_array_equal(pairs[0]['unwrappedPhase'],measured_pair()['unwrappedPhase'])
        self.assertEqual(ds.attrs['corrections_applied'],'none')

    def test_missing_screen_never_becomes_zero_correction(self):
        pairs,meta=self.data();del pairs[0]['ionospherePhaseScreen']
        ds,report,q=audit_corrections(pairs,meta)
        self.assertEqual(q['common_correction_input_pixels'],0)
        self.assertTrue(np.isnan(ds.reported_ionosphere_screen_rad[0]).all())
        self.assertIsNone(q['candidate_reference'])

    def test_no_common_phase_has_undefined_conditional_fraction(self):
        pairs,meta=self.data();pairs[0]['connectedComponents'][:]=0
        _,_,q=audit_corrections(pairs,meta)
        self.assertIsNone(q['common_correction_input_fraction_of_common_phase_support'])


if __name__=='__main__':unittest.main()
