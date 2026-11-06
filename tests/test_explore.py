"""Check support and partial results in the measured exploration worker."""

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import requests

from surface_dynamics.explore import _compare, measure, parse_request, select, select_alternate
from surface_dynamics.explore_server import ExploreJobs


def chip(path, mask, offset=0, date=None):
    np.savez(path, HHHH=np.full((2, 2), 2.0), mask=np.array(mask), epsg=32631,
             x=np.array([500000, 500010]) + offset, y=np.array([10, 0]))
    path.with_suffix(".json").write_text(json.dumps({
        "date": date or path.stem, "sha256": sha256(path.read_bytes()).hexdigest(),
    }))


class ExploreTests(unittest.TestCase):
    def setUp(self):
        self.query = parse_request({
            "longitude": 3, "latitude": 0,
            "start_date": "2026-10-07", "end_date": "2026-10-19",
        })

    def test_declines_frequency_b_only_and_never_combines_series(self):
        a = [{"time_start": "2026-10-07T00:00:00Z", "title": "a"}]
        b = [{"time_start": "2026-10-19T00:00:00Z", "title": "b"}]
        self.assertEqual(select(self.query, {"1_A_1_0005_NASV": a})[2], "unsupported_frequency")
        key, entries, reason = select(self.query, {"1_A_1_4005_DHDH": a, "2_D_1_4005_DHDH": b})
        self.assertEqual(len(entries), 1)
        self.assertIsNone(reason)
        self.assertIn(key, {"1_A_1_4005_DHDH", "2_D_1_4005_DHDH"})

    def test_comparison_rejects_disjoint_support_and_misaligned_grids(self):
        pair = ("2026-10-07", "2026-10-19")
        with TemporaryDirectory() as temp:
            out = Path(temp)
            paths = {day: out / (day + ".npz") for day in pair}
            chip(paths[pair[0]], [[1, 0], [0, 0]])
            chip(paths[pair[1]], [[0, 0], [0, 1]])
            self.assertEqual(_compare(self.query, pair, paths, out)["reason"], "no_common_valid_pixels")
            chip(paths[pair[1]], [[1, 0], [0, 0]], offset=10)
            self.assertEqual(_compare(self.query, pair, paths, out)["reason"], "incompatible_measured_support")
            self.assertFalse((out / "change.tif").exists())

    def test_archive_timeout_preserves_available_observation(self):
        entries = [
            {"time_start": day + "T00:00:00Z", "title": title}
            for day, title in (("2026-10-07", "older"), ("2026-10-19", "newer"))
        ]
        updates = []
        with TemporaryDirectory() as temp:
            out = Path(temp)

            def fetch(entry, longitude, latitude, path, pixels, cache_only):
                if entry["title"] == "older":
                    raise requests.Timeout()
                chip(path, [[1, 1], [1, 1]], date="2026-10-19")
                return {"flags": {"productVersion": "test"}}

            with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch):
                measure(self.query, entries, out, out, lambda **state: updates.append(state))
            self.assertEqual(updates[0]["observations"][0]["date"], "2026-10-19")
            self.assertEqual(updates[0]["observations"][0]["status"], "observed")
            self.assertEqual(updates[1]["observations"][0]["reason"], "archive_timeout")
            self.assertEqual(updates[-1]["status"], "partial")
            self.assertEqual(updates[-1]["comparison"]["reason"], "selected_date_unusable")
            self.assertTrue((out / "2026-10-19.tif").exists())

    def test_alternate_respects_the_requested_dates_and_series(self):
        query = replace(self.query, compare_dates=("2026-10-07", "2026-10-19"))
        entries = [{"time_start": day + "T00:00:00Z"} for day in query.compare_dates]
        groups = {"primary_DHDH": entries, "z_DHDH": entries[:1], "a_DHDH": entries}
        self.assertEqual(select_alternate(query, groups, "primary_DHDH"), ("a_DHDH", entries))
        self.assertIsNone(select_alternate(replace(query, series_key="primary_DHDH"), groups, "primary_DHDH"))
        self.assertIsNone(select_alternate(replace(query, lens="wildfire"), groups, "primary_DHDH"))

    def test_coverage_retry_only_runs_when_the_whole_window_is_unusable(self):
        dates = ("2026-10-07", "2026-10-19")
        primary = [{"id": day, "title": "primary", "time_start": day + "T00:00:00Z"} for day in dates]
        alternate = [{"id": day, "title": "alternate", "time_start": day + "T00:00:00Z"} for day in dates]

        for primary_outcome in ("masked", "one_valid", "timeout", "both_masked"):
            with self.subTest(primary_outcome=primary_outcome), TemporaryDirectory() as temp:
                out = Path(temp)
                jobs = ExploreJobs(out / "cache", out)
                calls = []

                def fetch(entry, longitude, latitude, path, pixels, cache_only):
                    calls.append(entry["title"])
                    day = entry["time_start"][:10]
                    if entry["title"] == "primary" and primary_outcome == "timeout":
                        raise requests.Timeout()
                    valid = ((entry["title"] == "alternate" and primary_outcome != "both_masked")
                             or (primary_outcome == "one_valid" and day == dates[0]))
                    path.parent.mkdir(exist_ok=True)
                    chip(path, np.full((2, 2), int(valid)), date=day)
                    return {"flags": {}}

                with patch("surface_dynamics.explore.fetch_chip", side_effect=fetch):
                    submitted = jobs.submit(self.query, {}, "primary", primary, None, ("alternate", alternate))
                    jobs.worker.shutdown()
                    jobs.cached_worker.shutdown()
                    jobs.optical_worker.shutdown()
                    jobs.pair_worker.shutdown()
                result = jobs.result(submitted["job_id"])
                retries = primary_outcome in {"masked", "both_masked"}
                self.assertEqual(calls, ["primary", "primary"] + (["alternate", "alternate"] if retries else []))
                self.assertEqual(len(result["series_attempts"]), 2 if retries else 1)
                self.assertEqual(result["series_key"], "alternate" if retries else "primary")
                self.assertTrue(all(row["granule_title"] == result["series_key"] for row in result["observations"]))
                expected = {"masked": "complete", "one_valid": "partial", "timeout": "unavailable", "both_masked": "no_data"}
                self.assertEqual(result["status"], expected[primary_outcome])
                if retries:
                    self.assertEqual(result["series_attempts"][0]["usable_window_date_count"], 0)
