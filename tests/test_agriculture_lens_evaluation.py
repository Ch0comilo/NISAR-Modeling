"""Computed agriculture observations and negative/control behavior."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import numpy as np
from rasterio.io import MemoryFile

from surface_dynamics.exploration_lenses import evaluate_lens
from surface_dynamics.explore import parse_request
from surface_dynamics.explore_server import ExploreJobs


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/agriculture-lens-evaluation.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
DATES = np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-18", "2026-09-11", "2026-09-23"])


def cube():
    offsets = np.arange(-320, 320, 10)
    power = np.ones((6, 2, 64, 64), dtype="float32")
    data = {
        "power": power, "valid": np.ones_like(power, bool), "dates": DATES.copy(),
        "polarizations": np.array(["HH", "HV"]), "x": 500000 + offsets,
        "y": offsets[::-1], "epsg": 32631,
    }
    distance = np.hypot(offsets[None, :], offsets[::-1, None])
    return data, distance <= 60, (distance >= 120) & (distance <= 300)


def evaluate(data, target=None, point=(3, 0)):
    report, arrays = evaluate_lens(data, target or str(DATES[-1]), "agriculture",
                                   baseline_end=str(DATES[2]), point=point)
    json.dumps(report, allow_nan=False)
    VALIDATOR.validate(report["evaluation"])
    return report["evaluation"], arrays


def local_step():
    data, focal, ring = cube()
    data["power"][3:, 0, focal] = 10 ** (4 / 10)
    data["power"][3:, 1, focal] = 10 ** (3 / 10)
    return data, focal, ring


class AgricultureEvaluationTests(unittest.TestCase):
    def test_local_step_has_observed_bracket_contrast_and_synchrony(self):
        data, focal, _ = local_step()
        result, arrays = evaluate(data)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["matches"]["matched_fraction"], 1)
        for name, expected in (("HH", 4), ("HV", 3)):
            contrast = result["target_contrasts"][name]
            self.assertAlmostEqual(contrast["mean_contrast_db"], expected, places=5)
            self.assertEqual(contrast["state"], "supports")
            steps = result["steps"][name]
            self.assertEqual(steps["largest_assessment_interval_code"], 3)
            self.assertEqual(steps["intervals"][2]["dates"], DATES[2:4].tolist())
            self.assertEqual(steps["intervals"][2]["interval_days"], 24)
            self.assertAlmostEqual(steps["intervals"][2]["bootstrap_largest_assessment_fraction"], 1)
            self.assertEqual(steps["synchrony"]["fraction"], 1)
            self.assertEqual(steps["synchrony"]["state"], "supports")
        self.assertTrue((arrays["agriculture_step_interval"][0, focal] == 3).all())
        self.assertTrue((arrays["agriculture_step_interval"][0, ~focal] == 0).all())
        self.assertEqual(result["independent_evidence"]["status"], "insufficient_data")
        self.assertIsNone(result["causal_attribution"])

    def test_stable_region_has_no_material_steps_or_synchrony(self):
        result, arrays = evaluate(cube()[0])
        for name in result["channels"]:
            self.assertEqual(result["target_contrasts"][name]["state"], "absent_on_sampled_support")
            steps = result["steps"][name]
            self.assertIsNone(steps["largest_assessment_interval_code"])
            self.assertIsNone(steps["largest_observed_interval_code"])
            self.assertEqual(steps["bootstrap_material_step_draws"], 0)
            self.assertIsNone(steps["synchrony_interval_code"])
            self.assertEqual(steps["synchrony"]["state"], "absent_on_sampled_support")
        self.assertTrue((arrays["agriculture_step_interval"] == 0).all())

    def test_regional_pulse_is_synchronous_without_a_focal_contrast(self):
        data, _, _ = cube()
        data["power"][3:] = 10 ** (4 / 10)
        result, _ = evaluate(data)
        self.assertEqual(result["target_contrasts"]["HH"]["state"], "absent_on_sampled_support")
        self.assertIsNone(result["steps"]["HH"]["largest_assessment_interval_code"])
        self.assertEqual(result["steps"]["HH"]["synchrony_interval_code"], 3)
        self.assertEqual(result["steps"]["HH"]["synchrony"]["state"], "supports")
        self.assertIsNone(result["phenomenon_probability"])

    def test_control_change_does_not_create_focal_synchrony(self):
        data, _, ring = cube()
        data["power"][3:, :, ring] = 10 ** (-4 / 10)
        result, _ = evaluate(data)
        self.assertEqual(result["target_contrasts"]["HH"]["state"], "supports")
        self.assertEqual(result["steps"]["HH"]["synchrony"]["state"], "absent_on_sampled_support")
        self.assertEqual(result["steps"]["HH"]["synchrony"]["fraction"], 0)

    def test_timing_resampling_retains_ambiguous_observed_intervals(self):
        data, focal, _ = cube()
        left = focal & (data["x"][None, :] < 500000)
        right = focal & ~left
        data["power"][3:, :, left] = 10 ** (4 / 10)
        data["power"][4:, :, right] = 10 ** (4 / 10)
        result, _ = evaluate(data)
        steps = result["steps"]["HH"]
        early, late = [steps["intervals"][index]["bootstrap_largest_assessment_fraction"] for index in (2, 3)]
        self.assertTrue(0 < early < 1)
        self.assertTrue(0 < late < 1)
        self.assertAlmostEqual(early + late, 1)
        self.assertFalse(steps["exact_event_date_estimated"])

    def test_future_values_do_not_change_results_and_ratio_is_measured(self):
        data, _, _ = local_step()
        first, arrays = evaluate(data, target=str(DATES[4]))
        data["power"][5] = 1000
        data["valid"][5] = False
        second, other = evaluate(data, target=str(DATES[4]))
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["target_contrasts"]["HV_over_HH"]["mean_contrast_db"], -1, places=5)
        self.assertEqual(first["support"]["dates"], DATES[:5].tolist())
        for name in arrays:
            np.testing.assert_equal(arrays[name], other[name])
        data["polarizations"] = np.array(["VV", "VH"])
        vv, _ = evaluate(data, target=str(DATES[4]))
        self.assertEqual(vv["channels"], ["VV", "VH", "VH_over_VV"])

    def test_weak_matches_and_too_few_control_blocks_withhold_states(self):
        data, focal, ring = local_step()
        data["power"][:3, :, ring] = 100
        weak, _ = evaluate(data)
        self.assertEqual(weak["reason"], "insufficient_baseline_matches")
        self.assertFalse(weak["target_contrasts"])
        # Sixty-four eligible controls share one block. The point estimate is
        # retained, while its spatial interval and evidence state are withheld.
        data["power"][:3, :, ring] = 100
        data["power"][:3, :, 16:24, 8:16] = 1
        thin, _ = evaluate(data)
        self.assertEqual(thin["status"], "evaluated")
        self.assertEqual(thin["uncertainty"]["spatial"]["control_blocks"], 1)
        self.assertEqual(thin["target_contrasts"]["HH"]["state"], "insufficient_data")
        self.assertIsNone(thin["target_contrasts"]["HH"]["spatial_resampling_95_interval_db"])

    def test_masked_pixel_sparse_dates_and_outside_neighborhood_remain_explicit(self):
        data, _, _ = local_step()
        data["valid"][3, :, 31, 32] = False
        result, arrays = evaluate(data)
        self.assertFalse(result["point"]["fixed_common_support"])
        self.assertTrue(np.isnan(arrays["agriculture_step_interval"][:, 31, 32]).all())
        outside, _ = evaluate(data, point=(3.001, 0))
        self.assertEqual(outside["reason"], "neighborhood_outside_grid")
        sparse = {**data, **{key: data[key][1:] for key in ("dates", "power", "valid")}}
        sparse_result, _ = evaluate(sparse)
        self.assertEqual(sparse_result["reason"], "fewer_than_three_prior_dates")

    def test_one_assessment_interval_keeps_contrast_and_withholds_synchrony(self):
        data, _, _ = local_step()
        result, _ = evaluate(data, target=str(DATES[3]))
        self.assertEqual(result["target_contrasts"]["HH"]["state"], "supports")
        synchrony = result["steps"]["HH"]["synchrony"]
        self.assertEqual(synchrony["state"], "insufficient_data")
        self.assertEqual(synchrony["reason"], "fewer_than_two_assessment_intervals")
        self.assertIsNone(synchrony["spatial_95_interval"])

    def test_worker_shares_temporal_support_and_serves_diagnostic_assets(self):
        data, _, _ = local_step()
        entries = [{"title": str(day), "time_start": str(day) + "T00:00:00Z"} for day in DATES]
        query = parse_request({
            "longitude": 3, "latitude": 0, "start_date": str(DATES[0]), "end_date": str(DATES[-1]),
            "baseline_end_date": str(DATES[2]), "lens": "agriculture",
        })
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            jobs = ExploreJobs(folder / "cache", folder)
            def fetch(entry, longitude, latitude, path, pixels, cache_only):
                i = data["dates"].tolist().index(entry["time_start"][:10])
                path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(path, HHHH=data["power"][i, 0], HVHV=data["power"][i, 1],
                         mask=data["valid"][i].all(0).astype("uint8"),
                         x=data["x"], y=data["y"], epsg=data["epsg"])
                path.with_suffix(".json").write_text(json.dumps({
                    "date": entry["time_start"][:10], "sha256": sha256(path.read_bytes()).hexdigest(), "flags": {},
                }))
                return {"flags": {}}
            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch) as fetcher:
                submitted = jobs.submit(query, {}, "test_DHDH", entries, None)
                for pool in (jobs.worker, jobs.cached_worker, jobs.optical_worker, jobs.pair_worker):
                    pool.shutdown()
            result = jobs.result(submitted["job_id"])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(fetcher.call_count, 6)
            evaluation = result["lens"]["evaluation"]
            VALIDATOR.validate(evaluation)
            temporal = result["lens"]["temporal_analysis"]
            self.assertEqual(evaluation["support"]["common_cells"], temporal["common_cells"])
            self.assertEqual(evaluation["support"]["dates"], temporal["dates"])
            self.assertEqual(len(result["lens"]["radar"]["layers"]), 3)
            layer = next(item for item in result["lens"]["radar"]["layers"] if item["role"] == "agriculture_step_interval")
            payload = jobs.asset(submitted["job_id"], Path(layer["href"]).name)
            self.assertEqual(sha256(payload).hexdigest(), layer["sha256"])
            with MemoryFile(payload) as memory, memory.open() as raster:
                self.assertEqual(raster.descriptions, ("HH", "HV", "HV_over_HH"))
                self.assertTrue(set(np.unique(raster.read(1))) <= {0, 3})
            self.assertIn("series", result["lens"]["agriculture"])

    def test_schema_packaging_pins_null_claims_and_policy(self):
        self.assertEqual(json.loads((ROOT / "src/surface_dynamics/schemas/agriculture-lens-evaluation.schema.json").read_text()), SCHEMA)
        example = json.loads((ROOT / "contracts/examples/skollersta-agriculture-lens-evaluation.json").read_text())
        VALIDATOR.validate(example)
        result, _ = evaluate(cube()[0])
        invalid = deepcopy(result)
        invalid["phenomenon_class"] = "harvest"
        with self.assertRaises(ValidationError):
            VALIDATOR.validate(invalid)


if __name__ == "__main__":
    unittest.main()
