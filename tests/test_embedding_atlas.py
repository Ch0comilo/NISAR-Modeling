"""Physical-unit and sampling safeguards for the exploratory atlas."""
import unittest
import numpy as np
from surface_dynamics.embedding_atlas import aggregate_hh_hv, sample_blocks, standardize


class EmbeddingAtlasTests(unittest.TestCase):
    def cube(self):
        # Reversed polarization order checks that slots follow names.
        power = np.ones((3, 3, 8, 8), dtype='float32')
        power[:, 0] = 4  # HV
        power[:, 1, ::2] = 9  # HH alternating power, not alternating dB
        power[:, 2] = 100  # VV must not enter HH/HV features
        return {'power': power, 'valid': np.ones_like(power, bool),
                'polarizations': np.array(['HV', 'HH', 'VV']),
                'dates': np.array(['2026-01-01', '2026-01-13', '2026-02-06']),
                'x': 500020+np.arange(8)*40., 'y': 5300020-np.arange(8)*40.,
                'epsg': np.array(32614), 'radar_band': np.array('L')}

    def test_power_before_logarithm_and_center_coordinates(self):
        cube = self.cube(); coarse = aggregate_hh_hv(cube)
        self.assertEqual(coarse['polarizations'].tolist(), ['HH', 'HV'])
        np.testing.assert_allclose(coarse['power'][:, 0], 5.)
        np.testing.assert_allclose(coarse['power'][:, 1], 4.)
        np.testing.assert_allclose(coarse['x'], [500080, 500240])
        np.testing.assert_allclose(coarse['y'], [5299960, 5299800])
        self.assertNotAlmostEqual(float(10*np.log10(coarse['power'][0,0,0,0])), float(5*np.log10(9)))
        self.assertEqual(cube['power'].shape[-1], 8)

    def test_one_invalid_constituent_excludes_the_whole_block(self):
        cube = self.cube(); cube['valid'][1, 1, 0, 0] = False
        coarse = aggregate_hh_hv(cube)
        self.assertFalse(coarse['valid'][1,0,0,0])
        self.assertTrue(np.isnan(coarse['power'][1,0,0,0]))
        self.assertTrue(coarse['valid'][1,1,0,0])

    def test_unsupported_domain_and_noninteger_grids_are_rejected(self):
        cube = self.cube(); cube['radar_band'] = np.array('S')
        with self.assertRaises(ValueError): aggregate_hh_hv(cube)
        cube = self.cube(); cube['x'] = 500000+np.arange(8)*30.
        cube['y'] = 5300000-np.arange(8)*30.
        with self.assertRaises(ValueError): aggregate_hh_hv(cube)

    def test_sampling_is_reproducible_complete_and_at_most_one_per_block(self):
        valid = np.ones((17, 15), bool); valid[3:9, 4:11] = False
        ids = sample_blocks(valid, 40, 2, 29)
        np.testing.assert_array_equal(ids, sample_blocks(valid, 40, 2, 29))
        self.assertEqual(len(ids), 40); self.assertTrue(valid.ravel()[ids].all())
        row, col = np.unravel_index(ids, valid.shape)
        self.assertEqual(len(set(zip(row//2, col//2))), len(ids))
        self.assertEqual(len(sample_blocks(np.zeros((4,4),bool),40,2,29)),0)

    def test_finite_distance_features_and_zero_variance_guard(self):
        z, center, scale = standardize(np.array([[1.,3.],[1.,7.]]))
        self.assertTrue(np.isfinite(z).all()); self.assertEqual(scale[0], .0001)
        np.testing.assert_allclose(z[:,1], [-1,1])
        with self.assertRaises(ValueError): standardize([[1,np.nan]])


if __name__ == '__main__': unittest.main()
