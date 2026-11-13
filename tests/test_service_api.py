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

    def test_five_new_jobs_per_ip_and_cache_hit_after_quota(self):
        caps = self.client.get("/v2/capabilities").json()
        self.assertEqual((caps["daily_new_jobs_per_ip"], caps["daily_new_jobs_total"]), (5, 12))
        for longitude in range(5):
            response = self.client.post("/v2/jobs", json={**BODY, "longitude": longitude})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job_id"]
            self.state["job:" + job_id].update(status="succeeded", result={"artifacts": []})
        blocked = self.client.post("/v2/jobs", json={**BODY, "longitude": 5})
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (429, "rate_limited"))
        cached = self.client.post("/v2/jobs", json={**BODY, "longitude": 0})
        self.assertEqual((cached.status_code, cached.json()["cache"]), (200, "hit"))

    def test_hosted_observation_page_and_api_share_a_gateway(self):
        page = Path(__file__).resolve().parents[1] / "scripts" / "observation-mock" / "index.html"
        client = TestClient(create_app(self.root, self.jobs, observation_page=page.read_text(encoding="utf-8")))
        response = client.get("/mock")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Search catalog", response.text)
        self.assertEqual(client.get("/v2/capabilities").json()["daily_new_jobs_per_ip"], 5)
        self.assertEqual(client.post("/v2/jobs", json=BODY).status_code, 202)

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

    def test_coherence_case_is_not_exposed_as_legacy_flood(self):
        folder = self.root / "cases-v8-20261113" / "choco"
        folder.mkdir(parents=True)
        case = {"id": "choco", "title": "Chocó, Colombia", "point": [-76.256, 4.86],
                "grid": {"crs": "EPSG:32618", "bounds": [358240, 534880, 363200, 539840],
                         "shape": [62, 62], "posting_m": [80, 80]},
                "series_key": "148_A_004_2000_SH", "baseline_dates": ["2026-07-18"],
                "assessment_dates": ["2026-08-11", "2026-08-23"], "status": "not_displacement_ready",
                "evidence": {"los_displacement_cm": None}, "layers": [], "series_url": "/v2/cases/choco/series"}
        (folder.parent / "index.json").write_text(json.dumps([case]), encoding="utf8")
        (folder / "manifest.json").write_text(json.dumps(case), encoding="utf8")
        client = TestClient(create_app(self.root, self.jobs, cases_path=folder.parent.name))

        result = client.get("/v2/cases/choco")
        legacy = client.get("/frames/148-4/analysis?hazard=flood&before=2026-07-18&after=2026-08-11&contract=2")

        self.assertEqual(result.json()["status"], "not_displacement_ready")
        self.assertIsNone(result.json()["evidence"]["los_displacement_cm"])
        self.assertEqual((legacy.status_code, legacy.json()["code"]), (404, "case_not_available"))

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

    def test_analysis_dashboard_publishes_numeric_rows_and_supported_map_bands(self):
        import numpy as np
        import rasterio
        from surface_dynamics.engine import write_cog
        from surface_dynamics.service_workers import execute_job

        dates = ["2026-07-01", "2026-07-13", "2026-07-25", "2026-08-18", "2026-08-30", "2026-09-23"]
        out = self.root / "result"

        def science(_body, _job, folder, _cache):
            (folder / "spectral_series.csv").write_text(
                "frequency_cycles_day,period_days,sampling_window,HH_normalized_power,HV_normalized_power\n"
                "0.02,50,0.3,0.8,\n0.025,40,0.6,0.4,\n", encoding="utf8")
            (folder / "gp_historical_series.csv").write_text(
                "date,polarization,status,observed_mean_db,conditional_95_lower_db\n"
                "2026-07-01,HH,constant_history,-16,\n", encoding="utf8")
            maps = np.stack([np.full((3, 3), 47), np.full((3, 3), np.nan)])
            write_cog(folder / "candidate_period_days.tif", maps, [500000, 500040, 500080],
                      [5300080, 5300040, 5300000], 32614, "days", ["HH", "HV"])
            return {"status": "complete", "observations": {"dates": dates}}

        with patch("surface_dynamics.service_workers.process", side_effect=science):
            result = execute_job({**BODY, "operation": "analysis"}, "test", out, self.root / "cache", "rev")
        dashboard = result["science"]["dashboard"]
        self.assertEqual(dashboard["acquisition_gaps_days"], [12, 12, 24, 12, 24])
        self.assertEqual(dashboard["series"]["spectral_series"][0]["HH_normalized_power"], .8)
        self.assertIsNone(dashboard["series"]["spectral_series"][0]["HV_normalized_power"])
        self.assertEqual(dashboard["series"]["gp_historical_series"][0]["status"], "constant_history")
        self.assertIsNone(dashboard["series"]["gp_historical_series"][0]["conditional_95_lower_db"])
        layer, = result["layers"]
        self.assertEqual((layer["id"], layer["units"], layer["range"]),
                         ("display_candidate_period_days_HH", "days", [40, 50]))
        self.assertEqual(layer["crs"], "EPSG:4326")
        with rasterio.open(out / layer["cog_url"].rsplit("/", 1)[-1]) as source:
            self.assertEqual(source.crs.to_epsg(), 32614)
            np.testing.assert_array_equal(source.read(1), 47)
