"""The vectorized neighborhood statistics must equal the per-pixel reference they replace."""

import time
from unittest import TestCase

import numpy as np
from scipy.ndimage import generic_filter

from surface_dynamics.surface_features import _neighbor


def reference(values, minimum, statistic):
    def calculate(a):
        a = a.copy()
        a[len(a) // 2] = np.nan
        a = a[np.isfinite(a)]
        if len(a) < minimum:
            return np.nan
        median = np.median(a)
        return median if statistic == "median" else 1.4826 * np.median(abs(a - median))
    return generic_filter(values.astype(float), calculate, size=3, mode="constant", cval=np.nan)


class NeighborFilterTests(TestCase):
    def test_matches_the_per_pixel_reference_with_gaps_edges_and_all_thresholds(self):
        rng = np.random.default_rng(7)
        for shape in ((1, 5), (2, 2), (7, 9), (24, 31)):
            values = rng.normal(-12, 4, shape)
            values[rng.random(shape) < .25] = np.nan
            for minimum in (1, 4, 8):
                for statistic in ("median", "mad"):
                    with self.subTest(shape=shape, minimum=minimum, statistic=statistic):
                        np.testing.assert_allclose(
                            _neighbor(values, minimum, statistic), reference(values, minimum, statistic),
                            rtol=1e-12, atol=1e-12, equal_nan=True)

    def test_an_all_missing_window_stays_missing(self):
        result = _neighbor(np.full((4, 4), np.nan), 4, "median")
        self.assertTrue(np.isnan(result).all())

    def test_a_full_native_window_is_fast_enough_for_a_live_request(self):
        values = np.random.default_rng(1).normal(-12, 4, (1024, 1024))
        values[np.random.default_rng(2).random(values.shape) < .1] = np.nan
        started = time.perf_counter()
        _neighbor(values, 4, "median")
        _neighbor(values, 4, "mad")
        self.assertLess(time.perf_counter() - started, 20)

    def test_infinite_values_are_treated_like_gaps(self):
        values = np.full((3, 3), 5.0)
        values[0, 0] = np.inf
        values[2, 2] = -np.inf
        result = _neighbor(values, 4, "median")
        self.assertEqual(result[1, 1], 5.0)
