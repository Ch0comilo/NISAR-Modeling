from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from surface_dynamics.service_contracts import JobRequest
from surface_dynamics.service_workers import process
from surface_dynamics.spectral import spectral_analysis
from surface_dynamics.stochastic import historical_series, smooth_observations


class TemporalDiagnosticsTests(TestCase):
    def test_irregular_spectrum_controls_for_trend_and_abstains_on_other_channel(self):
        days = np.cumsum([0, 12, 11, 25, 10, 12, 13, 12, 12, 22, 12, 12, 12, 12, 12, 24, 12, 12])
        trend = 0.12 * days - 10
        oscillation = 2.5 * np.sin(2 * np.pi * days / 35)
        values = np.broadcast_to(np.column_stack([trend + oscillation, trend])[:, :, None, None],
                                 (len(days), 2, 4, 4))
        result = spectral_analysis(values, days, permutations=39)
        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["channels"][0]["candidate_period_days"], 35, delta=0.5)
        self.assertGreater(result["channels"][0]["normalized_power"], .98)
        self.assertTrue(result["channels"][0]["supported_cells"] == 16)
        self.assertIsNone(result["channels"][1]["candidate_period_days"])
        self.assertTrue(np.isnan(result["candidate_period_days"][1]).all())
        self.assertEqual(spectral_analysis(values[:5], days[:5])["status"], "insufficient_temporal_support")

    def test_historical_gp_withholds_constant_band_and_preserves_measured_mean(self):
        times = np.array([0, 12, 24, 48, 60, 72.])
        values = np.full((6, 2, 4, 4), -16.)
        values[:, 1] += np.array([0, 1.5, 2, -1, -2.5, 1])[:, None, None]
        result = smooth_observations(values, times, draws=64)
        days = ["2026-01-01", "2026-01-13", "2026-01-25", "2026-02-18", "2026-03-02", "2026-03-14"]
        records = historical_series(result, days, ["HH", "HV"])
        self.assertEqual(records["HH"]["status"], "constant_history")
        self.assertEqual(records["HH"]["series"][0]["observed_mean_db"], -16)
        self.assertIsNone(records["HH"]["series"][0]["conditional_95_db"])
        self.assertEqual(records["HV"]["status"], "fit_returned")
        self.assertEqual([item["date"] for item in records["HV"]["series"]], days)
        self.assertEqual(records["HV"]["supported_cells"], 16)

    def test_existing_analysis_request_gets_six_dates_only_for_spectral(self):
        request = {"operation": "analysis", "longitude": -97.7, "latitude": 48.2,
                   "start_date": "2026-06-01", "end_date": "2026-10-19"}
        with TemporaryDirectory() as temp, patch("surface_dynamics.engine.analyze") as analyze:
            for analyses, expected in [(["stochastic"], 5), (["spectral", "stochastic"], 6)]:
                body = JobRequest.model_validate({**request, "analyses": analyses}).model_dump(mode="json", exclude_none=True)
                process(body, "job", Path(temp), Path(temp))
                self.assertEqual(analyze.call_args.args[0]["source"]["max_dates"], expected)
