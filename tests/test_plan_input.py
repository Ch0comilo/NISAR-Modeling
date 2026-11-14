"""Check that a backend scene plan drives an exploration without any catalog search."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from unittest import TestCase
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError
from rasterio.warp import transform

from surface_dynamics.catalog import science_url
from surface_dynamics.plan_input import (
    entries_from_scenes, prefetch_chips, series_key, summarize_aoi,
)
from surface_dynamics.service_contracts import JobRequest, canonical_job
from surface_dynamics.service_workers import run_explore

NAME = "NISAR_L2_PR_GCOV_{cycle}_084_A_018_4005_DHDH_A_{day}T002206_{day}T002240_P05023_N_F_J_001"
URGENT = "NISAR_L2_UR_GCOV_035_084_A_018_4005_DHDH_A_20261111T002203_20261111T002238_P05036_F_F_J_001"
DAYS = ["20260620", "20260725", "20260830", "20261005", "20261110"]
SCENES = [
    {"granule_id": NAME.format(cycle=f"{20 + i:03d}", day=day), "collection": "PROVISIONAL"}
    for i, day in enumerate(DAYS)
]
BODY = {
    "operation": "explore", "longitude": 73.713, "latitude": 32.329,
    "start_date": "2026-06-12", "end_date": "2026-11-12", "scenes": SCENES, "lens": "flood_wetland",
}


POINT_X, POINT_Y = (value[0] for value in transform("EPSG:4326", "EPSG:32643", [BODY["longitude"]], [BODY["latitude"]]))


def chip(path, date, size=4):
    """A small synthetic window of the plan's grid centered on the query point."""
    np.savez(path, HHHH=np.full((size, size), 2.0), HVHV=np.full((size, size), 1.0),
             mask=np.ones((size, size)), epsg=32643,
             x=np.round(POINT_X) - 10 * (size // 2) + 10 * np.arange(size),
             y=np.round(POINT_Y) + 10 * (size // 2) - 10 * np.arange(size))
    path.with_suffix(".json").write_text(json.dumps({
        "date": date, "sha256": sha256(path.read_bytes()).hexdigest(), "flags": {"productVersion": "t"},
    }))


class PlanEntryTests(TestCase):
    def test_granule_names_become_catalog_entries_with_server_built_urls(self):
        scenes = [{"granule_id": NAME.format(cycle="020", day="20260620"), "collection": "VALIDATED"},
                  {"granule_id": NAME.format(cycle="021", day="20260725"), "collection": "VALIDATED"},
                  {"granule_id": NAME.format(cycle="022", day="20260830"), "collection": "PROVISIONAL"},
                  {"granule_id": NAME.format(cycle="023", day="20261005"), "collection": "PROVISIONAL"},
                  {"granule_id": URGENT, "collection": "UR"}]
        key, entries = entries_from_scenes(scenes)
        self.assertEqual(key, "084_A_018_4005_DHDH")
        self.assertEqual([entry["time_start"][:10] for entry in entries],
                         ["2026-06-20", "2026-07-25", "2026-08-30", "2026-10-05", "2026-11-11"])
        urls = [science_url(entry) for entry in entries]
        self.assertIn("/NISAR/NISAR_L2_GCOV_V1/", urls[0])
        self.assertIn("/NISAR/NISAR_L2_GCOV_PROVISIONAL_V1/", urls[2])
        self.assertIn("/NISAR-UR/UR_L2_L_GCOV/" + URGENT + "/" + URGENT + ".h5", urls[4])

    def test_entries_are_sorted_by_time_and_series_key_comes_from_the_name(self):
        _, entries = entries_from_scenes(list(reversed(SCENES)))
        self.assertEqual([entry["id"] for entry in entries], [scene["granule_id"] for scene in SCENES])
        self.assertEqual(series_key(SCENES[0]["granule_id"]), "084_A_018_4005_DHDH")

    def test_invalid_plans_are_rejected(self):
        other_series = NAME.replace("4005", "2005").format(cycle="030", day="20261020")
        bad = {
            "not a granule name": [{"granule_id": "../../etc/passwd", "collection": "PROVISIONAL"}],
            "collection does not match the configuration": [{"granule_id": URGENT, "collection": "PROVISIONAL"}],
            "two series": [*SCENES[:2], {"granule_id": other_series, "collection": "PROVISIONAL"}],
            "two scenes on one day": [SCENES[0], {**SCENES[0], "granule_id": SCENES[0]["granule_id"].replace("020", "021")}],
            "empty": [],
        }
        for label, scenes in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                entries_from_scenes(scenes)

    def test_science_url_still_refuses_other_locations(self):
        for href in ("https://example.com/NISAR/x.h5", "https://nisar.asf.earthdatacloud.nasa.gov/OTHER/x.h5",
                     "https://nisar.asf.earthdatacloud.nasa.gov/NISAR-UR/x_QA_STATS.h5"):
            with self.subTest(href), self.assertRaises(ValueError):
                science_url({"links": [{"href": href}]})


class ContractTests(TestCase):
    def test_a_plan_is_accepted_only_for_explore_and_changes_the_job_identity(self):
        request = JobRequest(**BODY)
        self.assertEqual(len(request.scenes), 5)
        other = JobRequest(**{**BODY, "scenes": SCENES[:4]})
        self.assertNotEqual(canonical_job(request, "r")[0], canonical_job(other, "r")[0])
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "operation": "analysis", "lens": None, "compare_dates": None})
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "scenes": [{"granule_id": "x", "collection": "PROVISIONAL"}]})
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "scenes": SCENES + [SCENES[0]]})

    def test_the_plan_must_agree_with_the_request_dates(self):
        days = ["2026-06-20", "2026-07-25", "2026-08-30", "2026-10-05", "2026-11-10"]
        bad = {
            "scenes before the window": {"start_date": "2026-07-01"},
            "scenes after the window": {"end_date": "2026-11-01"},
            "comparison dates that are not planned days": {"compare_dates": ["2026-07-01", "2026-11-10"]},
            "a baseline cutoff after every scene": {"baseline_end_date": "2026-11-10"},
        }
        for label, change in bad.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                JobRequest(**{**BODY, **change})
        good = JobRequest(**{**BODY, "compare_dates": [days[0], days[-1]], "baseline_end_date": days[2]})
        self.assertEqual(good.baseline_end_date.isoformat(), days[2])

    def test_an_area_needs_a_lens_and_a_closed_ring_and_the_window_is_a_multiple_of_eight(self):
        ring = [[73.7, 32.3], [73.75, 32.3], [73.75, 32.34], [73.7, 32.3]]
        polygon = {"type": "Polygon", "coordinates": [ring]}
        no_lens = {key: value for key, value in BODY.items() if key != "lens"}
        with self.assertRaises(ValidationError):
            JobRequest(**{**no_lens, "aoi": polygon})
        for bad_ring in (ring[:-1], ring[:3] + [[73.7, 32.31]], [[73.7, 32.3], [73.7, 32.3]]):
            with self.subTest(ring=bad_ring), self.assertRaises(ValidationError):
                JobRequest(**{**BODY, "aoi": {"type": "Polygon", "coordinates": [bad_ring]}})
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "window_pixels": 65})
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "aoi": {"type": "Polygon", "coordinates": [[[0, 89], [1, 89], [1, 90], [0, 89]]]}})

    def test_the_scene_order_does_not_change_the_job_identity(self):
        forward = JobRequest(**BODY)
        backward = JobRequest(**{**BODY, "scenes": list(reversed(SCENES))})
        self.assertEqual(canonical_job(forward, "r")[0], canonical_job(backward, "r")[0])

    def test_window_pixels_and_aoi_are_bounded_and_explore_only(self):
        polygon = {"type": "Polygon", "coordinates": [[[73.7, 32.3], [73.75, 32.3], [73.75, 32.34], [73.7, 32.3]]]}
        request = JobRequest(**{**BODY, "window_pixels": 512, "aoi": polygon})
        self.assertEqual(request.window_pixels, 512)
        for field, value in (("window_pixels", 32), ("window_pixels", 4096),
                             ("aoi", {"type": "Point", "coordinates": [0, 0]}),
                             ("aoi", {"type": "Polygon", "coordinates": [[[200, 0], [1, 0], [1, 1], [200, 0]]]})):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                JobRequest(**{**BODY, field: value})
        with self.assertRaises(ValidationError):
            JobRequest(**{**BODY, "operation": "flood", "baseline_end_date": "2026-09-01", "lens": None,
                          "window_pixels": 128})


class RunExploreTests(TestCase):
    def test_scattering_lens_surroundings_are_published_on_native_radar(self):
        for lens in ("vegetation_disturbance", "wildfire"):
            with self.subTest(lens=lens), TemporaryDirectory() as temp:
                out, cache = Path(temp) / "out", Path(temp) / "cache"
                out.mkdir()
                cache.mkdir()

                def fetch(entry, longitude, latitude, path, pixels, cache_only, deadline=None):
                    chip(path, entry["time_start"][:10], size=64)
                    return {"flags": {}}

                with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch), \
                     patch("surface_dynamics.explore.enrich_lens", return_value={"status": "unavailable"}):
                    result = run_explore({**BODY, "lens": lens, "optical_evidence": True}, out, cache)

                self.assertEqual(result["lens"]["evidence"]["status"], "unavailable")
                surroundings = result["lens"]["evaluation"]["surrounding_comparison"]
                self.assertEqual(surroundings["status"], "evaluated")
                self.assertEqual(set(surroundings["channels"]), {"HH", "HV"})
                self.assertEqual(surroundings["channels"]["HV"]["mean_contrast_db"], 0)

    def test_a_plan_runs_without_searching_the_catalog_and_reads_the_planned_window(self):
        calls, lock = [], Lock()
        with TemporaryDirectory() as temp:
            out, cache = Path(temp) / "out", Path(temp) / "cache"
            out.mkdir()
            cache.mkdir()

            def fetch(entry, longitude, latitude, path, pixels, cache_only, deadline=None):
                with lock:
                    calls.append((entry["title"], pixels))
                chip(path, entry["time_start"][:10])
                return {"flags": {"productVersion": "t"}}

            def forbidden(*args, **kwargs):
                raise AssertionError("the catalog must not be searched when a plan is given")

            body = {**BODY, "window_pixels": 128}
            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch), \
                 patch("surface_dynamics.explore.discover", side_effect=forbidden), \
                 patch("surface_dynamics.explore.search", side_effect=forbidden):
                result = run_explore(body, out, cache)
        self.assertEqual(result["series_key"], "084_A_018_4005_DHDH")
        self.assertEqual(result["coverage"]["coverage_status"], "planned_scenes")
        self.assertEqual(result["status"], "complete")
        self.assertEqual({pixels for _, pixels in calls}, {128})
        self.assertEqual({title for title, _ in calls}, {scene["granule_id"] for scene in SCENES})
        self.assertEqual(set(result["timings"]), {"prefetch_s", "measure_s", "total_s"})

    def test_failed_prefetches_are_reported_without_restarting_archive_reads(self):
        attempts = {}
        with TemporaryDirectory() as temp:
            out, cache = Path(temp) / "out", Path(temp) / "cache"
            out.mkdir()
            cache.mkdir()

            def fetch(entry, longitude, latitude, path, pixels, cache_only, deadline=None):
                title = entry["title"]
                attempts[title] = attempts.get(title, 0) + 1
                if title == SCENES[0]["granule_id"] and attempts[title] == 1:
                    raise OSError("temporary failure")
                chip(path, entry["time_start"][:10])
                return {"flags": {}}

            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch):
                result = run_explore(BODY, out, cache)
        self.assertEqual(attempts[SCENES[0]["granule_id"]], 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["observations"][0]["reason"], "archive_read_failed")


class PrefetchTests(TestCase):
    def test_every_entry_is_fetched_once_and_failures_do_not_stop_the_others(self):
        _, entries = entries_from_scenes(SCENES)
        seen = []

        def fetch(entry, path):
            seen.append(entry["id"])
            if entry["id"] == SCENES[1]["granule_id"]:
                raise OSError("boom")

        failures = prefetch_chips(entries, lambda entry: Path(entry["id"]), fetch)
        self.assertEqual(sorted(seen), sorted(scene["granule_id"] for scene in SCENES))
        self.assertEqual(list(failures), [SCENES[1]["granule_id"]])


class AoiSummaryTests(TestCase):
    def setUp(self):
        size = 20
        self.cube = {"x": 500000 + 10.0 * np.arange(size), "y": 3000000 - 10.0 * np.arange(size), "epsg": 32643}
        delta = np.zeros((2, size, size))
        delta[:, :, :10] = -6.0
        delta[:, 0, :] = np.nan
        self.arrays = {"target_change_db": delta, "diagnostic_half_width_db": np.full((2, size, size), 3.0)}

    def polygon(self, west, south, east, north):
        corners = [(west, south), (east, south), (east, north), (west, north), (west, south)]
        lon, lat = transform("EPSG:32643", "EPSG:4326", [c[0] for c in corners], [c[1] for c in corners])
        return {"type": "Polygon", "coordinates": [list(zip(lon, lat))]}

    def test_only_cells_inside_the_polygon_are_counted(self):
        whole = self.polygon(499995, 2999800, 500200, 3000005)
        half = self.polygon(499995, 2999800, 500095, 3000005)
        everywhere = summarize_aoi(self.cube, self.arrays, whole, ["HH", "HV"])
        left = summarize_aoi(self.cube, self.arrays, half, ["HH", "HV"])
        self.assertEqual(everywhere["cells_in_area"], 400)
        self.assertGreater(everywhere["area_covered_fraction"], .9)
        self.assertEqual(everywhere["cells_with_measurement"], 380)
        self.assertEqual(everywhere["cells_departing_from_baseline"], 190)
        self.assertAlmostEqual(everywhere["departing_fraction"], .5)
        self.assertEqual(left["cells_in_area"], 200)
        self.assertEqual(left["cells_departing_from_baseline"], 190)
        self.assertEqual(left["median_target_minus_baseline_db"], {"HH": -6.0, "HV": -6.0})

    def test_an_area_larger_than_the_window_says_how_little_of_it_was_analysed(self):
        huge = self.polygon(499000, 2999000, 501000, 3001000)
        summary = summarize_aoi(self.cube, self.arrays, huge, ["HH", "HV"])
        self.assertEqual(summary["cells_in_area"], 400)
        self.assertAlmostEqual(summary["area_covered_fraction"], .01, places=3)

    def test_an_area_outside_the_window_reports_no_cells(self):
        outside = self.polygon(600000, 2900000, 600100, 2900100)
        summary = summarize_aoi(self.cube, self.arrays, outside, ["HH", "HV"])
        self.assertEqual(summary["cells_in_area"], 0)
        self.assertEqual(summary["reason"], "the area lies outside the analysed window")
        self.assertIsNone(summary["departing_fraction"])
        self.assertEqual(summary["median_target_minus_baseline_db"], {"HH": None, "HV": None})


class AoiPathTests(AoiSummaryTests):
    def test_run_explore_attaches_the_area_summary_when_an_area_is_given(self):
        polygon = self.polygon(499995, 2999800, 500200, 3000005)

        def measure(query, entries, cache, out, record, sink=None):
            record(status="complete", usable_window_date_count=5)
            sink.update(cube=self.cube, arrays=self.arrays,
                        report={"status": "evaluated", "radar": {"polarizations": ["HH", "HV"]}})
            return None

        with TemporaryDirectory() as temp:
            out, cache = Path(temp) / "out", Path(temp) / "cache"
            out.mkdir()
            cache.mkdir()
            with patch("surface_dynamics.explore.fetch_chip", return_value={}), \
                 patch("surface_dynamics.explore.measure", side_effect=measure):
                result = run_explore({**BODY, "aoi": polygon}, out, cache)
        self.assertEqual(result["aoi_summary"]["cells_in_area"], 400)
        self.assertEqual(result["aoi_summary"]["cells_departing_from_baseline"], 190)

    def test_no_summary_is_attached_when_the_lens_was_not_evaluated(self):
        def measure(query, entries, cache, out, record, sink=None):
            record(status="partial", usable_window_date_count=2)
            sink.update(report={"status": "not_evaluated"})
            return None

        with TemporaryDirectory() as temp:
            out, cache = Path(temp) / "out", Path(temp) / "cache"
            out.mkdir()
            cache.mkdir()
            with patch("surface_dynamics.explore.fetch_chip", return_value={}), \
                 patch("surface_dynamics.explore.measure", side_effect=measure):
                result = run_explore({**BODY, "aoi": self.polygon(499995, 2999800, 500200, 3000005)}, out, cache)
        self.assertNotIn("aoi_summary", result)


class GatewayImageTests(TestCase):
    def test_the_contracts_import_without_numpy_or_rasterio(self):
        import subprocess
        import sys
        code = ("import sys, surface_dynamics.service_contracts; "
                "heavy = {'numpy', 'rasterio', 'scipy'} & set(sys.modules); "
                "sys.exit(1 if heavy else 0)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
