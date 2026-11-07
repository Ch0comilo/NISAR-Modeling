"""Flood observations, independent comparisons and progressive job contracts."""

from copy import deepcopy
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import numpy as np
from rasterio.io import MemoryFile

from surface_dynamics.exploration_lenses import evaluate_lens
from surface_dynamics.explore_server import ExploreHandler, ExploreJobs
from surface_dynamics.flood_lens import with_evidence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/flood-lens-evaluation.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
DATES = np.array(["2026-07-01", "2026-07-13", "2026-07-25", "2026-09-11", "2026-09-23"])


def cube():
    offsets = np.arange(-320, 320, 10)
    power = np.ones((5, 2, 64, 64), dtype="float32")
    return {
        "power": power, "valid": np.ones_like(power, dtype=bool), "dates": DATES.copy(),
        "polarizations": np.array(["HH", "HV"]),
        "x": 500000 + offsets, "y": offsets[::-1], "epsg": 32631,
    }


def reference(shape, pre=0, post=1):
    return {
        "pre_valid_coverage": np.ones(shape), "post_valid_coverage": np.ones(shape),
        "pre_partial_class_fraction": np.zeros(shape), "post_partial_class_fraction": np.zeros(shape),
        "pre_open_class_fraction": np.full(shape, pre, dtype=float),
        "post_open_class_fraction": np.full(shape, post, dtype=float),
    }


def evaluate(data, optical=None, point=(3, 0)):
    report, arrays = evaluate_lens(data, DATES[3], "flood_wetland", baseline_end=DATES[2],
                                   reference=optical, point=point,
                                   reference_epochs={"pre": {"date": DATES[2]}, "post": {"date": DATES[3]}})
    json.dumps(report, allow_nan=False)
    VALIDATOR.validate(report["evaluation"])
    return report, arrays


def write_chip(path, data, day):
    index = data["dates"].tolist().index(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, HHHH=data["power"][index, 0], HVHV=data["power"][index, 1],
             mask=data["valid"][index].all(0).astype("uint8"), x=data["x"], y=data["y"], epsg=data["epsg"])
    path.with_suffix(".json").write_text(json.dumps({
        "date": day, "sha256": sha256(path.read_bytes()).hexdigest(),
        "flags": {"noiseCorrectionApplied": False},
    }))


class FloodEvaluationTests(unittest.TestCase):
    def test_existing_water_darkening_and_future_values_do_not_become_new_water(self):
        data = cube()
        data["power"][3] = 0.1
        optical = reference((64, 64), pre=1, post=1)
        report, arrays = evaluate(data, optical)
        evaluation = report["evaluation"]
        self.assertEqual(evaluation["point"]["signature_states"]["dual_darkening"], "supports")
        self.assertEqual(evaluation["water_comparison"]["point_new_open_water_state"], "contradicts")
        self.assertEqual(evaluation["water_comparison"]["point_group_code"], 2)
        self.assertEqual(report["evidence"]["groups"]["stable_open_water"]["dual_darkening_cells"], 4096)
        self.assertEqual(evaluation["grid_signatures"]["co_pol_brightening"]["signature_cells"], 0)
        data["power"][4] = 100
        changed, _ = evaluate(data, optical)
        self.assertEqual(changed["evaluation"], evaluation)
        self.assertEqual(arrays["flood_signature_flags"].shape, (2, 64, 64))

    def test_directional_absence_ratio_and_named_v_channels(self):
        data = cube()
        data["polarizations"] = np.array(["VV", "VH"])
        data["power"][3, 0] = 10
        report, _ = evaluate(data)
        point = report["evaluation"]["point"]
        self.assertEqual(point["signature_states"], {
            "dual_darkening": "absent_on_sampled_support", "co_pol_brightening": "supports",
        })
        self.assertEqual(set(point["change_db"]), {"VV", "VH"})
        self.assertAlmostEqual(point["cross_minus_co_change_db"], -10)
        self.assertEqual(point["observation_bracket"], ["2026-07-25", "2026-09-11"])
        self.assertEqual([row["date"] for row in point["series"]], DATES[:4].tolist())
        self.assertEqual(report["evaluation"]["uncertainty"]["observation"]["status"], "unavailable")

    def test_new_optical_water_is_supported_independently_of_a_radar_signature(self):
        data = cube()
        optical = reference((64, 64), pre=0, post=0)
        optical["post_open_class_fraction"][:32] = 1
        report, _ = evaluate(data, optical)
        evaluation = report["evaluation"]
        self.assertEqual(evaluation["point"]["signature_states"]["dual_darkening"], "absent_on_sampled_support")
        self.assertEqual(evaluation["water_comparison"]["point_new_open_water_state"], "supports")
        compared = evaluation["water_comparison"]["matched_new_water_comparison"]
        self.assertEqual(compared["status"], "evaluated")
        self.assertEqual(compared["channels"]["HH"]["mean_contrast_db"], 0)
        self.assertEqual(compared["matches"]["matched_positive_pixels"], 16)
        optical["post_open_class_fraction"][:32] = 0.5
        mixed, _ = evaluate(data, optical)
        self.assertEqual(mixed["evaluation"]["water_comparison"]["point_new_open_water_state"], "insufficient_data")
        existing_water = next(item for item in mixed["evaluation"]["confounders"] if item["id"] == "existing_water")
        self.assertEqual(existing_water["status"], "unavailable")

    def test_masked_pixel_remains_unknown_beside_valid_darkening(self):
        data = cube()
        data["power"][3] = 0.1
        data["valid"][0, :, 31, 32] = False
        report, arrays = evaluate(data)
        self.assertEqual(report["evaluation"]["point"]["reason"], "point_lacks_fixed_common_support")
        self.assertIsNone(report["evaluation"]["point"]["change_db"])
        self.assertTrue(np.isnan(arrays["flood_signature_flags"][:, 31, 32]).all())
        self.assertEqual(report["evaluation"]["grid_signatures"]["dual_darkening"]["supported_cells"], 4095)
        outside, _ = evaluate(data, point=(3.1, 0))
        self.assertEqual(outside["evaluation"]["point"]["reason"], "point_outside_grid")

    def test_matched_surroundings_use_baseline_and_report_spatial_uncertainty(self):
        data = cube()
        distance = np.hypot(data["x"][None, :] - 500000, data["y"][:, None])
        focal = distance <= 60
        data["power"][3, 0, focal] = 10 ** (-6 / 10)
        data["power"][3, 1, focal] = 10 ** (-4 / 10)
        report, _ = evaluate(data)
        comparison = report["evaluation"]["surrounding_comparison"]
        self.assertEqual(comparison["status"], "evaluated")
        self.assertEqual(comparison["matches"]["matched_fraction"], 1)
        self.assertEqual(comparison["differential_darkening"]["state"], "supports")
        for pol, expected in (("HH", -6), ("HV", -4)):
            channel = comparison["channels"][pol]
            self.assertAlmostEqual(channel["mean_contrast_db"], expected, places=4)
            self.assertIsNotNone(channel["spatial_resampling_95_interval_db"])
        self.assertEqual(len(comparison["series"]), 4)
        # A simultaneous regional shift preserves the selected baseline matches.
        data["power"][3] *= 10
        shifted, _ = evaluate(data)
        updated = shifted["evaluation"]["surrounding_comparison"]
        self.assertEqual(updated["matches"], comparison["matches"])
        self.assertAlmostEqual(updated["channels"]["HH"]["mean_contrast_db"], -6, places=4)
        self.assertEqual(shifted["evaluation"]["point"]["signature_states"]["dual_darkening"],
                         "absent_on_sampled_support")

    def test_weak_controls_and_sparse_optical_support_are_withheld(self):
        data = cube()
        focal = np.hypot(data["x"][None, :] - 500000, data["y"][:, None]) <= 60
        data["power"][:3, :, focal] = 100
        report, _ = evaluate(data)
        self.assertEqual(report["evaluation"]["surrounding_comparison"]["reason"], "insufficient_baseline_matches")
        optical = reference((64, 64))
        optical["pre_valid_coverage"][:] = 0
        optical["pre_valid_coverage"][31, 32] = 1
        report, _ = evaluate(data, optical)
        self.assertEqual(report["evaluation"]["water_comparison"]["status"], "insufficient_data")
        self.assertNotIn("point_new_open_water_state", report["evaluation"]["water_comparison"])

    def test_sparse_missing_channel_and_empty_support_carry_reasons_and_nulls(self):
        for case in ("sparse", "channel", "masked", "target"):
            with self.subTest(case=case):
                data = cube()
                if case == "sparse":
                    data.update(dates=DATES[2:], power=data["power"][2:], valid=data["valid"][2:])
                elif case == "channel":
                    data.update(polarizations=np.array(["HH"]), power=data["power"][:, :1], valid=data["valid"][:, :1])
                elif case == "masked":
                    data["valid"][:] = False
                else:
                    data.update(dates=DATES[:3], power=data["power"][:3], valid=data["valid"][:3])
                report, arrays = evaluate(data)
                self.assertEqual(report["evaluation"]["status"], "insufficient_data")
                self.assertEqual(report["evaluation"]["reason"], report["reason"])
                self.assertIsNone(report["evaluation"]["point"])
                self.assertIsNone(report["evaluation"]["phenomenon_probability"])
                self.assertEqual(arrays, {})

    def test_optical_claim_requires_sensing_dates_and_aligned_grid(self):
        data = cube()
        optical = reference((64, 64))
        for epochs, reason in (
            (None, "reference_sensing_dates_unavailable"),
            ({"pre": {"date": "2026-07-25"}, "post": {"date": "2026-09-01"}}, "reference_outside_temporal_support"),
            ({"pre": {"date": "2026-07-01"}, "post": {"date": "2026-09-11"}}, "reference_outside_temporal_support"),
        ):
            report, _ = evaluate_lens(data, DATES[3], "flood_wetland", baseline_end=DATES[2],
                                      reference=optical, reference_epochs=epochs, point=(3, 0))
            VALIDATOR.validate(report["evaluation"])
            self.assertEqual(report["evaluation"]["water_comparison"]["reason"], reason)
        optical["x"] = data["x"] + 10
        with self.assertRaisesRegex(ValueError, "aligned"):
            evaluate(data, optical)

    def test_schema_pins_policy_null_claims_and_packaged_copy(self):
        packaged = ROOT / "src/surface_dynamics/schemas/flood-lens-evaluation.schema.json"
        self.assertEqual(json.loads(packaged.read_text()), SCHEMA)
        example = json.loads((ROOT / "contracts/examples/barh-flood-lens-evaluation.json").read_text())
        VALIDATOR.validate(example)
        report, _ = evaluate(cube())
        invalid = deepcopy(report["evaluation"])
        invalid["phenomenon_probability"] = 0.9
        with self.assertRaises(ValidationError):
            VALIDATOR.validate(invalid)
        native = report["evaluation"]
        snapshot = deepcopy(native)
        pending = with_evidence(report, {"status": "pending"})
        failed = with_evidence(pending, {"status": "unavailable", "reason": "optical_worker_error"})
        VALIDATOR.validate(pending["evaluation"])
        VALIDATOR.validate(failed["evaluation"])
        self.assertEqual(native, snapshot)
        self.assertEqual(pending["evaluation"]["water_comparison"]["status"], "pending")
        self.assertEqual(failed["evaluation"]["water_comparison"]["reason"], "optical_worker_error")

    def test_point_and_signed_maps_publish_before_optical_then_complete_over_http(self):
        data = cube()
        data["power"][3] = 0.1
        entries = [{"title": "synthetic_" + day, "time_start": day + "T00:00:00Z"} for day in DATES[:4]]
        optical_started, optical_release = Event(), Event()

        def fetch(entry, longitude, latitude, path, pixels, cache_only):
            write_chip(path, data, entry["time_start"][:10])
            return {"flags": {"noiseCorrectionApplied": False}}

        def optical(cube, longitude, latitude, radar_dates, common, cache):
            optical_started.set()
            if not optical_release.wait(10):
                raise RuntimeError("Test optical stage did not release")
            return reference(common.shape, pre=1, post=1), {
                "status": "selected", "source": "OPERA DSWx-HLS optical surface water",
                "epochs": {"pre": {"date": DATES[2]}, "post": {"date": DATES[3]}},
            }

        def request(server, method, route, body=None):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(method, route, body=json.dumps(body) if body else None)
            response = conn.getresponse()
            payload = response.read()
            status = response.status
            conn.close()
            return status, payload

        with (
            TemporaryDirectory() as folder,
            patch.object(ExploreHandler, "_discover", return_value=({"catalog_hits": 4}, {"001_A_001_4005_DHDH": entries})),
            patch("surface_dynamics.explore.fetch_chip", side_effect=fetch),
            patch("surface_dynamics.explore.find_optical_reference", side_effect=optical),
        ):
            folder = Path(folder)
            jobs = ExploreJobs(folder / "cache", folder)
            server = ThreadingHTTPServer(("127.0.0.1", 0), ExploreHandler)
            server.jobs = jobs
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                body = {"longitude": 3, "latitude": 0, "start_date": DATES[0], "end_date": DATES[3],
                        "lens": "flood_wetland", "baseline_end_date": DATES[2], "optical_evidence": True, "feature_lab": True}
                status, payload = request(server, "POST", "/v1/explore", body)
                self.assertEqual(status, 202)
                job = json.loads(payload)
                self.assertTrue(optical_started.wait(10))
                status, payload = request(server, "GET", job["status_url"])
                pending = json.loads(payload)
                self.assertEqual(pending["phase"], "optical_evidence")
                self.assertEqual(pending["lens"]["evaluation"]["water_comparison"]["status"], "pending")
                self.assertEqual(pending["lens"]["evaluation"]["point"]["signature_states"]["dual_darkening"], "supports")
                self.assertEqual(len(pending["observations"]), 4)
                self.assertEqual(pending["comparison"]["status"], "available")
                self.assertEqual(pending["feature_lab"]["status"], "available")
                feature_layer = pending["feature_lab"]["layers"][0]
                feature_status, feature_tif = request(server, "GET", feature_layer["href"])
                self.assertEqual(feature_status, 200)
                self.assertEqual(sha256(feature_tif).hexdigest(), feature_layer["sha256"])
                layer = pending["lens"]["radar"]["layers"][0]
                status, tif = request(server, "GET", layer["href"])
                self.assertEqual(status, 200)
                self.assertEqual(sha256(tif).hexdigest(), layer["sha256"])
                with MemoryFile(tif) as memory, memory.open() as raster:
                    self.assertEqual(raster.descriptions, ("dual_darkening", "co_pol_brightening"))
                    self.assertEqual(raster.count, 2)
                    np.testing.assert_equal(raster.read(1), 1)
                    np.testing.assert_equal(raster.read(2), 0)
                optical_release.set()
                deadline = monotonic() + 10
                completed = pending
                while completed["phase"] and monotonic() < deadline:
                    sleep(0.01)
                    _, payload = request(server, "GET", job["status_url"])
                    completed = json.loads(payload)
                self.assertEqual(completed["status"], "complete")
                evaluation = completed["lens"]["evaluation"]
                VALIDATOR.validate(evaluation)
                self.assertEqual(evaluation["point"], pending["lens"]["evaluation"]["point"])
                self.assertEqual(evaluation["grid"]["width"], 64)
                self.assertEqual(evaluation["water_comparison"]["grid"]["width"], 16)
                self.assertEqual(evaluation["water_comparison"]["point_new_open_water_state"], "contradicts")
                self.assertEqual(request(server, "GET", job["status_url"] + "/assets/unpublished.tif")[0], 404)
                self.assertEqual(pending["lens"]["evaluation"]["water_comparison"]["status"], "pending")
            finally:
                optical_release.set()
                server.shutdown()
                worker.join()
                server.server_close()
                for pool in (jobs.worker, jobs.cached_worker, jobs.optical_worker, jobs.pair_worker):
                    pool.shutdown()


if __name__ == "__main__":
    unittest.main()
