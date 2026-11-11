import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from surface_dynamics.service_api import create_app
from surface_dynamics.service_contracts import JobRequest, canonical_job
from surface_dynamics.service_state import JobStore, LocalDict


BODY = {"operation": "explore", "longitude": -97.7, "latitude": 48.2,
        "start_date": "2026-10-07", "end_date": "2026-10-19",
        "compare_dates": ["2026-10-07", "2026-10-19"]}


class ApiTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state, self.calls = LocalDict(), []
        self.jobs = JobStore(self.state, lambda *args: self.calls.append(args), "test-revision")
        self.client = TestClient(create_app(self.root, self.jobs))

    def test_idempotent_active_and_completed_jobs(self):
        first = self.client.post("/v2/jobs", json=BODY)
        self.assertEqual(first.status_code, 202)
        first = first.json()
        second = self.client.post("/v2/jobs", json={**BODY, "longitude": -97.7001})
        self.assertEqual(second.json()["cache"], "in_flight")
        self.assertEqual(second.json()["job_id"], first["job_id"])
        self.assertEqual(len(self.calls), 1)
        key = "job:" + first["job_id"]
        self.state[key].update(status="succeeded", result={"artifacts": []})
        self.assertEqual(self.client.get(first["status_url"]).json()["status"], "succeeded")
        response = self.client.post("/v2/jobs", json=BODY)
        self.assertEqual((response.status_code, response.json()["cache"]), (200, "hit"))

    def test_error_paths_and_limits(self):
        for field, value in [("EARTHDATA_TOKEN", "forbidden"), ("longitude", 181),
                             ("end_date", "2028-01-01"), ("pair_id", "NISAR_L2_unknown")]:
            response = self.client.post("/v2/jobs", json={**BODY, field: value})
            self.assertEqual((response.status_code, response.json()["code"]), (422, "invalid_request"))
        self.assertEqual(self.client.post("/v2/jobs", content="x" * 8193).status_code, 413)
        self.assertEqual(self.client.get("/v2/jobs/" + "0" * 32).status_code, 404)
        for longitude in [10, 20]:
            self.assertEqual(self.client.post("/v2/jobs", json={**BODY, "longitude": longitude}).status_code, 202)
        self.assertEqual(self.client.post("/v2/jobs", json={**BODY, "longitude": 30}).status_code, 503)

    def test_failure_can_retry_and_budget_applies(self):
        def fail(*args):
            raise RuntimeError("internal")
        self.jobs.spawn = fail
        self.jobs.jobs_per_day = 1
        first = self.client.post("/v2/jobs", json=BODY)
        second = self.client.post("/v2/jobs", json=BODY)
        self.assertEqual(first.json()["code"], "submit_failed")
        self.assertEqual(second.json()["code"], "daily_budget")

    def test_schema_and_cors(self):
        schema = self.client.get("/v2/openapi.json").json()
        self.assertIn("/v2/jobs", schema["paths"])
        Draft202012Validator.check_schema(JobRequest.model_json_schema())
        Draft202012Validator(JobRequest.model_json_schema()).validate(BODY)
        a = JobRequest.model_validate(BODY)
        self.assertNotEqual(canonical_job(a, "a")[0], canonical_job(a, "b")[0])
        for origin, expected in [("http://localhost:5173", True), ("https://example.org", False)]:
            response = self.client.get("/health", headers={"Origin": origin})
            self.assertEqual(response.headers.get("access-control-allow-origin") == origin, expected)

    def test_curated_adapter_and_byte_range(self):
        folder = self.root / "cases" / "chokwe"
        folder.mkdir(parents=True)
        manifest = {"id": "chokwe", "point": [32.986, -24.534], "series_key": "114_A_165_4005_DHDH",
                    "baseline_dates": ["2026-01-05"], "assessment_dates": ["2026-01-17"],
                    "evidence": {"threshold_cell_area_ha": 460.8}, "layers": []}
        (folder.parent / "index.json").write_text(json.dumps([manifest]))
        route = "/frames/114-165/analysis?hazard=flood&before=2026-01-05&after=2026-01-17"
        self.assertEqual(self.client.get(route).json()["code"], "contract_upgrade_required")
        result = self.client.get(route + "&contract=2").json()
        self.assertIsNone(result["affectedAreaHa"])
        self.assertIsNone(result["severityHa"])
        self.assertEqual(result["evidence"]["threshold_cell_area_ha"], 460.8)
        self.assertEqual(self.client.get(route + "&contract=2&lon=0&lat=0").status_code, 404)
        data = b"abcdefgh"
        (folder / "a.tif").write_bytes(data)
        (folder / "assets.json").write_text(json.dumps([{"name": "a.tif", "media_type": "image/tiff",
                                                         "sha256": sha256(data).hexdigest()}]))
        asset = "/v2/cases/chokwe/layers/a.tif"
        response = self.client.get(asset, headers={"Range": "bytes=2-4"})
        self.assertEqual((response.status_code, response.content), (206, b"cde"))
        self.assertEqual(self.client.get(asset, headers={"If-None-Match": response.headers["etag"]}).status_code, 304)
        self.assertEqual(self.client.get(asset, headers={"Range": "bytes=10-12"}).status_code, 416)

    @patch("surface_dynamics.service_api.search", side_effect=__import__("requests").Timeout)
    def test_catalog_error(self, _search):
        response = self.client.get("/v2/coverage", params={"longitude": 0, "latitude": 0,
                                   "start_date": "2026-01-01", "end_date": "2026-01-02"})
        self.assertEqual((response.status_code, response.json()["code"]), (504, "catalog_timeout"))

    def test_live_dates_share_stretch_and_links_follow_published_assets(self):
        import numpy as np
        from surface_dynamics.engine import write_cog
        from surface_dynamics.service_workers import live_layers, published_science

        for date, value in [("2026-10-07", -15), ("2026-10-19", -18)]:
            write_cog(self.root / (date + ".tif"), np.full((3, 3), value),
                      [500000, 500010, 500020], [5300020, 5300010, 5300000],
                      32614, "dB", ["HH"])
        layers = live_layers(self.root, "/v2/jobs/abcd/layers", "explore")
        self.assertEqual(len(layers), 2)
        self.assertTrue(all(layer["range"] == [-30, 0] for layer in layers))
        href = "/v2/jobs/abcd/layers/2026-10-07.tif"
        science = {"observations": [{"layer": {"href": "/v1/jobs/abcd/assets/2026-10-07.tif"}}]}
        self.assertEqual(published_science(science, {"2026-10-07.tif": href})["observations"][0]["layer"]["href"], href)
