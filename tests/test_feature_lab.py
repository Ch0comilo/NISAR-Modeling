"""Feature Lab support, physical units, historical gates and shared observations."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import numpy as np
from rasterio.io import MemoryFile

from surface_dynamics.explore import measure, parse_request
from surface_dynamics.explore_server import ExploreJobs
from surface_dynamics.feature_lab import build_feature_lab, directional_texture, patches


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/feature-lab-result.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
DATES = np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-18", "2026-09-11", "2026-09-23"])


def measured_cube(count=6):
    offsets = np.arange(-320, 320, 20)
    db = np.zeros((count, 2, 32, 32), dtype=float)
    for i in range(count):
        db[i, 0] = i + np.arange(32)[None, :] / 20
        db[i, 1] = i / 2 + np.arange(32)[:, None] / 30
    power = (10 ** (db / 10)).astype("float32")
    return {
        "power": power, "valid": np.ones_like(power, bool), "dates": DATES[:count].copy(),
        "polarizations": np.array(["HH", "HV"]), "epsg": 32631,
        "x": 500000 + offsets, "y": offsets[::-1],
    }


def build(data, target=None, point=(3, 0)):
    report, arrays, shared = build_feature_lab(data, target or str(data["dates"][-1]),
                                              baseline_end="2026-07-25", point=point)
    json.dumps(report, allow_nan=False)
    VALIDATOR.validate(report)
    return report, arrays, shared


def feature(report, name):
    return next(item for item in report["features"] if item["id"] == name)


def write_measured_chip(path, data, day):
    i = data["dates"].tolist().index(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, HHHH=data["power"][i, 0], HVHV=data["power"][i, 1],
             mask=data["valid"][i].all(0).astype("uint8"), x=data["x"], y=data["y"], epsg=data["epsg"])
    path.with_suffix(".json").write_text(json.dumps({
        "date": day, "sha256": sha256(path.read_bytes()).hexdigest(), "flags": {},
    }))


class FeatureLabTests(unittest.TestCase):
    def test_ratio_uses_each_cell_and_date_and_preserves_named_channels(self):
        report, arrays, _ = build(measured_cube())
        ratio = feature(report, "HV_over_HH")
        series = ratio["series"]
        hh, hv = feature(report, "HH"), feature(report, "HV")
        for i, sample in enumerate(series):
            expected = hv["series"][i]["point_db"] - hh["series"][i]["point_db"]
            self.assertAlmostEqual(sample["point_db"], expected)
            self.assertAlmostEqual(sample["point_linear_ratio"], 10 ** (expected / 10))
        expected_change = series[-1]["point_db"] - np.median([s["point_db"] for s in series[:3]])
        self.assertAlmostEqual(ratio["target_change_db"], expected_change)
        self.assertEqual(arrays["change_db"].shape, (3, 32, 32))
        vv = measured_cube()
        vv["polarizations"] = np.array(["VV", "VH"])
        v_report, _, _ = build(vv)
        self.assertEqual([f["id"] for f in v_report["features"]], ["VV", "VH", "VH_over_VV"])
        single = measured_cube()
        single.update(polarizations=np.array(["HH"]), power=single["power"][:, :1], valid=single["valid"][:, :1])
        one, _, _ = build(single)
        self.assertEqual([f["id"] for f in one["features"]], ["HH"])

    def test_masked_date_keeps_raw_point_dot_and_withholds_common_point_metrics(self):
        data = measured_cube()
        data["valid"][3, :, 15, 16] = False
        report, arrays, shared = build(data)
        self.assertEqual(report["support"]["common_cells"], 1023)
        self.assertFalse(report["point"]["fixed_common_support"])
        hh = feature(report, "HH")
        self.assertIsNone(hh["series"][3]["point_db"])
        self.assertIsNotNone(hh["series"][-1]["point_db"])
        self.assertIsNone(hh["target_change_db"])
        self.assertEqual(hh["point_timing"]["status"], "insufficient_data")
        self.assertTrue(np.isnan(arrays["change_db"][:, 15, 16]).all())
        self.assertFalse(shared["valid"][:, :, 15, 16].any())
        # ROI summaries keep the same 1,023 cells at every date.
        db = 10 * np.log10(data["power"].astype(float))
        common = shared["valid"].all(axis=(0, 1))
        np.testing.assert_allclose([s["roi_mean_db"] for s in hh["series"]], db[:, 0, common].mean(1), atol=2e-7)

    def test_future_values_do_not_change_a_pinned_historical_target(self):
        data = measured_cube()
        first, arrays, _ = build(data, target=str(DATES[4]))
        data["power"][5] = 1000
        data["valid"][5] = False
        second, later_arrays, _ = build(data, target=str(DATES[4]))
        self.assertEqual(first, second)
        self.assertEqual(first["dates"], DATES[:5].tolist())
        for key in arrays:
            np.testing.assert_equal(arrays[key], later_arrays[key])
        self.assertEqual(feature(first, "HH")["gp"]["status"], "insufficient_data")

    def test_short_and_missing_target_records_keep_measurements_without_invented_steps(self):
        for count in (1, 2, 3):
            report, _, _ = build(measured_cube(count))
            hh = feature(report, "HH")
            self.assertEqual(len(hh["series"]), count)
            self.assertEqual(hh["point_timing"]["status"], "insufficient_data")
            self.assertEqual(hh["gp"]["status"], "insufficient_data")
        report, arrays, _ = build(measured_cube(4), target=str(DATES[-1]))
        self.assertIsNone(feature(report, "HH")["target_change_db"])
        self.assertNotIn("change_db", arrays)
        self.assertNotIn("target_db", arrays)
        self.assertEqual(feature(report, "HH")["spatial"]["status"], "insufficient_data")

    def test_empty_and_disjoint_support_remain_null_in_json(self):
        data = measured_cube()
        data["valid"][:] = False
        data["valid"][0, :, :16] = True
        data["valid"][1:, :, 16:] = True
        report, arrays, _ = build(data)
        self.assertEqual(report["reason"], "no_fixed_common_support")
        self.assertEqual(report["support"]["common_cells"], 0)
        self.assertTrue((arrays["common_support"] == 0).all())
        self.assertTrue(all(s["roi_mean_db"] is None for s in feature(report, "HH")["series"]))
        outside, _, _ = build(measured_cube(), point=(3.1, 0))
        self.assertFalse(outside["point"]["inside_grid"])

    def test_directional_pairs_preserve_holes_and_metric_spacing(self):
        common = np.ones((16, 16), bool)
        image = np.broadcast_to(np.arange(16, dtype=float), (16, 16)).copy()
        common[8, 8] = False
        image[8, 8] = np.nan
        report = directional_texture(image, common, 20, 10)
        east = next(item for item in report["bins"] if item["direction"] == "east" and item["lag_m"] == 20)
        north = next(item for item in report["bins"] if item["direction"] == "north" and item["lag_m"] == 10)
        self.assertEqual(east["pairs"], 16 * 15 - 2)
        self.assertAlmostEqual(east["semivariance_db2"], 0.5)
        self.assertAlmostEqual(east["median_absolute_gradient_db_per_m"], 0.05)
        self.assertEqual(north["semivariance_db2"], 0)

    def test_patch_geometry_uses_eight_neighbors_and_flags_missing_boundaries(self):
        common = np.ones((16, 16), bool)
        common[7, 7] = False
        change = np.zeros((16, 16))
        for i in range(3, 7):
            change[i, i] = -4
        report, ids = patches(change, common, 20, 10)
        self.assertEqual(report["patch_count"], 1)
        obj = report["objects"][0]
        self.assertEqual(obj["cells"], 4)
        self.assertEqual(obj["area_m2"], 800)
        self.assertAlmostEqual(obj["boundary_contrast_db"], -4)
        self.assertTrue(obj["touches_chip_edge_or_nodata"])
        self.assertTrue(np.isnan(ids[7, 7]))
        self.assertTrue((ids[np.arange(3, 7), np.arange(3, 7)] == obj["id"]).all())
        self.assertEqual(patches(np.zeros((16, 16)), common, 20, 10)[0]["patch_count"], 0)

    def test_gp_draws_use_actual_dates_and_a_failed_fit_preserves_other_cards(self):
        report, _, _ = build(measured_cube())
        model = feature(report, "HH")["gp"]
        self.assertEqual(model["status"], "available")
        self.assertEqual(model["dates"], DATES.tolist())
        self.assertEqual(np.asarray(model["draws_db"]).shape, (128, 6))
        self.assertEqual(np.asarray(model["quantiles_025_50_975_db"]).shape, (3, 6))
        with patch("surface_dynamics.feature_lab.smooth_observations", side_effect=np.linalg.LinAlgError):
            failed, _, _ = build(measured_cube())
        self.assertEqual(failed["status"], "available")
        self.assertEqual(feature(failed, "HH")["gp"]["reason"], "gp_numerical_failure")
        flat = measured_cube()
        flat["power"][:] = 1
        constant, _, _ = build(flat)
        self.assertEqual(feature(constant, "HH")["gp"]["reason"], "constant_regional_history")
        self.assertEqual(feature(constant, "HH")["point_timing"]["departure_acquisitions"], 0)

    def test_opt_in_and_baseline_cutoff_work_without_a_lens(self):
        body = {"longitude": 3, "latitude": 0, "start_date": str(DATES[0]), "end_date": str(DATES[-1]),
                "baseline_end_date": str(DATES[2]), "feature_lab": True}
        self.assertTrue(parse_request(body).feature_lab)
        self.assertIsNone(parse_request(body).lens)
        for value in (False, "true", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_request({**body, "feature_lab": value})
        data = measured_cube()
        entries = [{"title": str(day), "time_start": str(day) + "T00:00:00Z"} for day in DATES]
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            def fetch(entry, longitude, latitude, path, pixels, cache_only):
                write_measured_chip(path, data, entry["time_start"][:10])
                return {"flags": {}}
            query = replace(parse_request(body), baseline_end_date=None, feature_lab=False)
            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch), patch("surface_dynamics.explore.build_feature_lab") as build_lab:
                measure(query, entries, folder, folder, lambda **state: None)
            build_lab.assert_not_called()

    def test_worker_reuses_six_reads_and_shares_mask_with_flood_lens_and_assets(self):
        body = {"longitude": 3, "latitude": 0, "start_date": str(DATES[0]), "end_date": str(DATES[-1]),
                "baseline_end_date": str(DATES[2]), "feature_lab": True}
        data = measured_cube()
        data["valid"][3, :, 15, 16] = False
        entries = [{"title": str(day), "time_start": str(day) + "T00:00:00Z"} for day in DATES]
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            def fetch(entry, longitude, latitude, path, pixels, cache_only):
                write_measured_chip(path, data, entry["time_start"][:10])
                return {"flags": {}}
            jobs = ExploreJobs(folder / "cache", folder)
            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch) as fetched:
                submitted = jobs.submit(parse_request({**body, "lens": "flood_wetland"}), {}, "test_DHDH", entries, None)
                for pool in (jobs.worker, jobs.cached_worker, jobs.optical_worker, jobs.pair_worker):
                    pool.shutdown()
            result = jobs.result(submitted["job_id"])
            self.assertEqual(fetched.call_count, 6)
            lab = result["feature_lab"]
            VALIDATOR.validate(lab)
            self.assertEqual(lab["support"]["common_cells"], 1023)
            self.assertEqual(result["lens"]["radar"]["common_cells"], 1023)
            self.assertEqual(result["lens"]["shared_observation_support"]["common_mask_sha256"], lab["support"]["common_mask_sha256"])
            self.assertEqual(result["lens"]["shared_observation_support"]["dates"], lab["dates"])
            self.assertEqual(result["lens"]["point"]["status"], "insufficient_data")
            layer = next(item for item in lab["layers"] if item["role"] == "change_db")
            payload = jobs.asset(submitted["job_id"], Path(layer["href"]).name)
            self.assertEqual(sha256(payload).hexdigest(), layer["sha256"])
            with MemoryFile(payload) as memory, memory.open() as raster:
                self.assertEqual(raster.descriptions, ("HH", "HV", "HV_over_HH"))
                self.assertTrue(np.isnan(raster.read()[:, 15, 16]).all())

    def test_schema_packaging_and_null_policy(self):
        packaged = ROOT / "src/surface_dynamics/schemas/feature-lab-result.schema.json"
        self.assertEqual(json.loads(packaged.read_text()), SCHEMA)
        example = json.loads((ROOT / "contracts/examples/colombia-feature-lab-result.json").read_text())
        VALIDATOR.validate(example)
        report, _, _ = build(measured_cube())
        invalid = deepcopy(report)
        invalid["phenomenon_probability"] = 0.5
        with self.assertRaises(ValidationError):
            VALIDATOR.validate(invalid)

    def test_empty_catalog_job_returns_an_explicit_lab_outcome(self):
        query = parse_request({
            "longitude": 3, "latitude": 0, "start_date": str(DATES[0]), "end_date": str(DATES[-1]),
            "feature_lab": True,
        })
        with TemporaryDirectory() as folder:
            jobs = ExploreJobs(Path(folder) / "cache", Path(folder))
            try:
                result = jobs.submit(query, {"catalog_hits": 0}, None, [], "no_catalog_match")
                self.assertEqual(result["status"], "no_data")
                self.assertEqual(result["feature_lab"]["reason"], "no_catalog_match")
                VALIDATOR.validate(result["feature_lab"])
            finally:
                for pool in (jobs.worker, jobs.cached_worker, jobs.optical_worker, jobs.pair_worker):
                    pool.shutdown()


if __name__ == "__main__":
    unittest.main()
