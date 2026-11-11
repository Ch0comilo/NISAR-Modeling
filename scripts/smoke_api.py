"""Measure the deployed API and verify curated output without new archive reads by default."""
import argparse
import json
from time import perf_counter, sleep

import requests


def smoke(url, live, measurement):
    session = requests.Session()
    session.headers["Origin"] = "https://nisar-hazard-watch.vercel.app"
    timings = []

    def fetch(method, path, expected=200, **kwargs):
        start = perf_counter()
        response = session.request(method, url.rstrip("/") + path, timeout=25, **kwargs)
        timings.append({"method": method, "path": path, "seconds": round(perf_counter()-start, 3),
                        "status": response.status_code})
        allowed = (expected,) if isinstance(expected, int) else expected
        if response.status_code not in allowed:
            raise RuntimeError(json.dumps(timings[-1]))
        return response

    health = fetch("GET", "/health")
    assert health.json()["status"] == "ok"
    assert health.headers["access-control-allow-origin"] == session.headers["Origin"]
    fetch("GET", "/health")
    fetch("GET", "/v2/openapi.json")
    fetch("GET", "/v2/capabilities")
    cases = fetch("GET", "/v2/cases").json()
    assert {case["id"] for case in cases} == {"chokwe", "qadirabad", "richmond", "kumamoto"}
    case = fetch("GET", "/v2/cases/chokwe").json()
    assert case["grid"]["shape"] == [128, 128] and case["isDemo"] is False
    fetch("GET", case["series_url"])
    layer = next(item for item in case["layers"] if item["id"] == "change_db_hh")
    assert fetch("GET", layer["png_url"]).content.startswith(b"\x89PNG")
    assert fetch("GET", layer["cog_url"], 206, headers={"Range": "bytes=0-7"}).content[:2] in (b"II", b"MM")
    legacy = "/frames/114-165/analysis?hazard=flood&before=2026-01-05&after=2026-01-17"
    fetch("GET", legacy, 409)
    assert fetch("GET", legacy + "&contract=2").json()["severityHa"] is None
    bad = fetch("POST", "/v2/jobs", 422, json={"EARTHDATA_TOKEN": "example-placeholder"})
    assert bad.json()["code"] == "invalid_request"
    query = {"longitude": -97.7, "latitude": 48.2, "start_date": "2026-10-07", "end_date": "2026-10-19"}
    assert fetch("GET", "/v2/coverage", params=query).json()["pixel_support"] == "unchecked"
    fetch("GET", "/v2/coverage", params=query)
    job_summary = None
    if live or measurement:
        request = {**query, "operation": "insar-network"}
        if measurement:
            request.update(operation="explore", series_key="105_A_026_4005_DHDH",
                           compare_dates=["2026-10-07", "2026-10-19"])
        started = perf_counter()
        first = fetch("POST", "/v2/jobs", (200, 202), json=request)
        job = first.json()
        duplicate = fetch("POST", "/v2/jobs", (200, 202), json=request)
        assert duplicate.json()["job_id"] == job["job_id"]
        while job["status"] in {"queued", "running"} and perf_counter() - started < 280:
            sleep(3)
            job = fetch("GET", job["status_url"]).json()
        assert job["status"] == "succeeded", job.get("error")
        result = job["result"]
        job_summary = {"id": job["job_id"], "initial_cache": first.json()["cache"],
                       "total_seconds": round(perf_counter() - started, 3),
                       "worker_seconds": result["elapsed_seconds"], "science_status": result["science_status"]}
        assert fetch("POST", "/v2/jobs", 200, json=request).json()["cache"] == "hit"
        if measurement:
            assert result["isDemo"] is False and result["science"]["comparison"]
            previews = [layer for layer in result["layers"] if layer["range"] == [-30, 0]]
            assert len(previews) == 4
            assert fetch("GET", previews[0]["png_url"]).content.startswith(b"\x89PNG")
            source = result["science"]["observations"][0]["layer"]["href"]
            assert source.startswith(job["status_url"] + "/layers/")
            fetch("GET", source, 206, headers={"Range": "bytes=0-7"})
    print(json.dumps({"requests": timings, "job": job_summary}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--live", action="store_true", help="Use one quota slot to test a catalog-only job.")
    parser.add_argument("--measurement", action="store_true", help="Use one quota slot to verify the measured Forest River job.")
    args = parser.parse_args()
    smoke(args.url, args.live, args.measurement)
