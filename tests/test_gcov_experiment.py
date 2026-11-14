import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np

from surface_dynamics.io import cube_from_chips
from surface_dynamics.ranges import RangeReader
from surface_dynamics.service_api import create_app
from surface_dynamics.service_state import JobStore, LocalDict


class GcovExperimentTests(TestCase):
    def test_strict_power_aggregation_and_equal_pixel_edges(self):
        with TemporaryDirectory() as work:
            chip = Path(work) / "chip.npz"
            x, y = np.arange(8) * 10 + 5, np.arange(8)[::-1] * 10 + 5
            power = np.arange(1, 65, dtype="float32").reshape(8, 8)
            mask = np.ones((8, 8), dtype="uint8")
            mask[7, 7] = 255
            np.savez(chip, HHHH=power, HVHV=power * 2, x=x, y=y, mask=mask, epsg=32618)
            chip.with_suffix(".json").write_text(json.dumps({
                "sha256": hashlib.sha256(chip.read_bytes()).hexdigest(),
                "date": "2026-10-15", "flags": {"radarBand": "L"},
            }))
            native = cube_from_chips([chip], aggregate=1)
            coarse = cube_from_chips([chip], aggregate=4, require_posting=10)
            self.assertEqual(coarse["x"].tolist(), [20, 60])
            self.assertEqual(coarse["y"].tolist(), [60, 20])
            np.testing.assert_array_equal(coarse["valid"][0, 0], [[True, True], [True, False]])
            np.testing.assert_allclose(coarse["power"][0, :, 0, 0], native["power"][0, :, :4, :4].mean((1, 2)))
            self.assertTrue(np.isnan(coarse["power"][0, :, 1, 1]).all())
            with self.assertRaisesRegex(ValueError, "verified 10 m"):
                cube_from_chips([chip], aggregate=4, require_posting=20)

    def test_bulk_exact_ranges_run_concurrently_with_a_shared_budget(self):
        gate = Barrier(2, timeout=5)
        url = "https://nisar.asf.earthdatacloud.nasa.gov/test.h5"

        def download(_, start, end, timeout, bulk):
            if bulk:
                gate.wait()
                return bytes([start]) * (end - start + 1), 1000
            return b"\x89HDF\r\n\x1a\n" + bytes(8), 1000

        stats = {}
        with patch.object(RangeReader, "_download", download), RangeReader(
            url, "test", budget=24, parallel=True, stats=stats
        ) as reader:
            self.assertEqual(reader.cat_ranges([url, url], [20, 30], [24, 34]), [bytes([20])*4, bytes([30])*4])
            with self.assertRaisesRegex(RuntimeError, "transfer budget"):
                reader.cat_ranges([url], [40], [41])
            with self.assertRaisesRegex(ValueError, "Invalid archive"):
                reader.cat_ranges(["another-file"], [40], [41])
        self.assertEqual((stats["http_requests"], stats["asf_bytes"], stats["peak_requests"]), (3, 24, 2))

    def test_preview_variant_partitions_jobs_and_preserves_exact_point(self):
        payload = json.loads((Path(__file__).parents[1] / "scripts/gcov_ab/choco.json").read_text())
        with TemporaryDirectory() as work:
            calls = []
            jobs = JobStore(LocalDict(), lambda body, _: calls.append(body), "test", max_active=4)
            with TestClient(create_app(Path(work), jobs, gcov_experiment=True)) as client:
                def submit(header):
                    return client.post("/v2/jobs", json=payload, headers={"X-NISAR-GCOV-Experiment": header})

                first = submit("h5py:10:a:cold")
                self.assertEqual(first.status_code, 202)
                self.assertEqual(submit("h5py:10:a:cold").json()["cache"], "in_flight")
                self.assertNotEqual(submit("pyfive:40:b:cold").json()["job_id"], first.json()["job_id"])
                self.assertEqual(submit("pyfive:20:b:cold").status_code, 422)
                self.assertEqual(calls[0]["longitude"], payload["longitude"])
                self.assertEqual(calls[1]["gcov_experiment"]["posting"], 40)
