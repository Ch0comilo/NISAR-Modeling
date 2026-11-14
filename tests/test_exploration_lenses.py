import unittest
from unittest.mock import patch

import numpy as np

from surface_dynamics.exploration_lenses import agriculture_history, evaluate_flood_wetland, evaluate_lens, wildfire_pattern
from surface_dynamics.explore import parse_request, select
from surface_dynamics.io import point_location
from surface_dynamics.optical_water import select_reference


class AgricultureLensTests(unittest.TestCase):
    def test_focal_contrast_tracks_both_assessments_on_fixed_support(self):
        dates = np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-06", "2026-08-18"])
        offsets = np.arange(-320, 320, 10)
        focal = np.hypot(offsets[None, :], offsets[::-1, None]) <= 60
        power = np.ones((5, 2, 64, 64), dtype=np.float32)
        power[3, 1, focal] = 10
        power[4, 1, focal] = .1
        valid = np.ones_like(power, dtype=bool)
        valid[0, 1, 31, 32] = False
        cube = {"power": power, "valid": valid, "dates": dates, "polarizations": np.array(["HH", "HV"]),
                "x": 500000 + offsets, "y": offsets[::-1], "epsg": 32631}

        report, arrays = evaluate_lens(cube, dates[-1], "agriculture", baseline_end=dates[2])
        common = np.isfinite(arrays["target_change_db"]).all(axis=0)
        history = agriculture_history(cube, report, common, 3, 0)
        self.assertEqual(history["status"], "evaluated")
        self.assertEqual(history["focal_cells"], history["focal_grid_cells"] - 1)
        hv = [sample for sample in history["series"] if sample["polarization"] == "HV"]
        self.assertEqual([sample["date"] for sample in hv], dates.tolist())
        np.testing.assert_allclose([sample["contrast_shift_from_baseline_db"] for sample in hv],
                                   [0, 0, 0, 10, -10], atol=1e-5)
        self.assertEqual(history["largest_contrast_steps"][1]["dates"], dates[-2:].tolist())
        self.assertEqual(agriculture_history(cube, report, common, 3.001, 0)["reason"],
                         "neighborhood_outside_grid")


class SurroundingComparisonTests(unittest.TestCase):
    def test_shared_support_and_baseline_matching_for_both_lenses(self):
        dates = np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-06", "2026-08-18"])
        offsets = np.arange(-320, 320, 10)
        power = np.ones((5, 2, 64, 64), dtype=np.float32)
        valid = np.ones_like(power, dtype=bool)
        cube = {"power": power, "valid": valid, "dates": dates, "polarizations": np.array(["VV", "VH"]),
                "x": 500000 + offsets, "y": offsets[::-1], "epsg": 32631}
        location = point_location(cube, (3, 0))
        focal = np.hypot(cube["x"][None, :] - location["x"], cube["y"][:, None] - location["y"]) <= 60
        power[-2, 1, focal] = .1
        power[-1, 0] = 10
        power[-1, 1, focal] = .01
        valid[-2, 1, location["row"], location["column"]] = False

        for lens, measured_dates, missing_cells in (
            ("vegetation_disturbance", dates[[0, 1, 2, 4]].tolist(), 0),
            ("wildfire", dates.tolist(), 1),
        ):
            with self.subTest(lens=lens):
                def compare(point=(3, 0), target=dates[-1]):
                    report, _ = evaluate_lens(cube, target, lens, baseline_end=dates[2], point=point)
                    return report["evaluation"]["surrounding_comparison"]

                comparison = compare()
                self.assertEqual(comparison["status"], "evaluated")
                self.assertEqual(comparison["matches"]["matched_positive_pixels"], int(focal.sum()) - missing_cells)
                self.assertEqual([row["date"] for row in comparison["series"]], measured_dates)
                self.assertAlmostEqual(comparison["channels"]["VV"]["mean_contrast_db"], 0)
                self.assertAlmostEqual(comparison["channels"]["VH"]["mean_contrast_db"], -20, places=4)
                self.assertNotIn("differential_darkening", comparison)
                self.assertEqual(compare(point=(3.001, 0))["reason"], "neighborhood_outside_grid")
                self.assertEqual(compare(target=dates[2]), {
                    "status": "insufficient_data", "reason": "fewer_than_three_prior_dates",
                })

                power[:3, :, focal] = 100
                self.assertEqual(compare()["reason"], "insufficient_baseline_matches")
                power[:3, :, focal] = 1


class FloodLensTests(unittest.TestCase):
    def test_darkening_in_existing_water_is_separate_from_optical_transition(self):
        # Both top cells darken at the target. Only the first has an optical transition.
        power = np.ones((5, 2, 2, 2), dtype=np.float32)
        power[3, :, 0] = .1
        power[4] = 100
        valid = np.ones_like(power, dtype=bool)
        valid[0, :, 1, 1] = False
        cube = {
            "power": power, "valid": valid,
            "dates": np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-06", "2026-08-18"]),
            "polarizations": np.array(["HH", "HV"]), "x": np.array([500000, 500080]),
            "y": np.array([80, 0]), "epsg": 32631,
        }
        fractions = {
            "pre_valid_coverage": np.ones((2, 2)), "post_valid_coverage": np.ones((2, 2)),
            "pre_partial_class_fraction": np.zeros((2, 2)), "post_partial_class_fraction": np.zeros((2, 2)),
            "pre_open_class_fraction": np.array([[0, 1], [0, 0]]),
            "post_open_class_fraction": np.array([[1, 1], [0, 0]]),
        }
        report, arrays = evaluate_flood_wetland(cube, "2026-08-06", fractions)
        self.assertEqual(report["radar"]["common_cells"], 3)
        self.assertEqual(report["baseline_dates"], ["2026-07-01", "2026-07-13", "2026-07-25"])
        self.assertTrue(np.isnan(arrays["target_change_db"][:, 1, 1]).all())
        groups = report["evidence"]["groups"]
        self.assertEqual(groups["new_open_water"]["dual_darkening_cells"], 1)
        self.assertEqual(groups["stable_open_water"]["dual_darkening_cells"], 1)
        self.assertEqual(groups["stable_nonwater"]["mad_departure_cells"], 0)
        report, _ = evaluate_flood_wetland(cube, "2026-08-06")
        self.assertEqual(report["evidence"]["status"], "unavailable")
        report, arrays = evaluate_flood_wetland(cube, "2026-07-25")
        self.assertEqual(report["reason"], "fewer_than_three_prior_dates")
        self.assertEqual(arrays, {})

        vegetation, arrays = evaluate_lens(cube, "2026-08-18", "vegetation_disturbance",
                                           baseline_end="2026-07-25")
        self.assertEqual(vegetation["baseline_dates"], ["2026-07-01", "2026-07-13", "2026-07-25"])
        self.assertNotIn("dual_darkening_cells", vegetation["radar"])
        np.testing.assert_allclose(arrays["target_change_db"][:, 0], 20, atol=1e-5)

    def test_lens_selection_reserves_dates_before_baseline_cutoff(self):
        days = ["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-06", "2026-08-18", "2026-08-30"]
        query = parse_request({
            "longitude": 0, "latitude": 0, "start_date": days[0], "end_date": days[-1],
            "lens": "vegetation_disturbance", "baseline_end_date": "2026-07-25", "max_dates": 4,
        })
        rows = [{"time_start": day + "T00:00:00Z"} for day in days]
        _, selected, _ = select(query, {"001_A_024_4005_DHDH": rows})
        self.assertEqual([row["time_start"][:10] for row in selected], days[:3] + days[-1:])

    def test_wildfire_preserves_two_assessments_with_a_pinned_early_comparison(self):
        days = ["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-06", "2026-08-18", "2026-08-30"]
        query = parse_request({
            "longitude": 0, "latitude": 0, "start_date": days[0], "end_date": days[-1],
            "lens": "wildfire", "baseline_end_date": "2026-08-06", "max_dates": 5,
            "compare_dates": [days[0], days[-1]],
        })
        rows = [{"time_start": day + "T00:00:00Z"} for day in days]
        _, selected, _ = select(query, {"001_A_024_4005_DHDH": rows})
        self.assertEqual([row["time_start"][:10] for row in selected], [days[i] for i in [0, 2, 3, 4, 5]])

    def test_wildfire_pattern_distinguishes_last_two_return_and_unsupported(self):
        changes = np.array([[[-4, -5, 0, 0, np.nan]], [[-5, -4, 0, -5, -5]], [[-6, -1, 4, -6, -5]]])
        bound = np.full((1, 5), 3.)
        dates = ["2026-08-06", "2026-08-18", "2026-08-30"]
        report, arrays = wildfire_pattern(changes, bound, dates, "HV")
        np.testing.assert_equal(arrays["cross_pol_pattern"], [[2, 3, 0, 2, np.nan]])
        np.testing.assert_equal(arrays["cross_pol_decline_count"], [[3, 2, 0, 2, np.nan]])
        self.assertEqual(report["decline_on_last_two_cells"], 2)
        self.assertEqual(report["return_within_band_cells"], 1)
        report, arrays = wildfire_pattern(changes[-1:], bound, dates[-1:], "HV")
        self.assertEqual(report["status"], "insufficient_assessment_dates")
        self.assertNotIn("cross_pol_pattern", arrays)

    def test_optical_selection_checks_actual_sensing_date_and_common_support(self):
        entries = [
            {"title": name, "time_start": day + "T00:00:00Z"}
            for name, day in [("wrong_epoch", "2026-09-12"), ("near_cloud", "2026-09-12"),
                              ("clear_common", "2026-09-13")]
        ]
        def fetch(entry, directory, **options):
            observed = "2026-09-11" if entry["title"] == "wrong_epoch" else entry["time_start"][:10]
            return entry["title"], {"observation_time": observed}

        def align(path, cube):
            return {"valid_coverage": np.array([[0, 1], [1, 1]] if path == "near_cloud" else [[1, 0], [0, 0]])}

        with (
            patch("surface_dynamics.optical_water.fetch_asset", side_effect=fetch),
            patch("surface_dynamics.optical_water.align_classes", side_effect=align) as aligned,
        ):
            _, selection = select_reference(
                {"entries": entries}, {}, "2026-09-12", "unused",
                support=np.array([[True, False], [False, False]]),
                accept_sensing_day=lambda day: day >= "2026-09-12",
            )
        self.assertEqual(aligned.call_count, 2)
        self.assertEqual(selection["source"]["observation_time"], "2026-09-13")
        self.assertEqual(selection["valid_analysis_cell_fraction"], 1)
