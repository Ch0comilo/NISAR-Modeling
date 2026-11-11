"""Measure the deployed API and verify curated output without new archive reads by default."""
import argparse
import json
from time import perf_counter, sleep

import requests


def smoke(url, live):
    session = requests.Session()
    session.headers["Origin"] = "https://nisar-hazard-watch.vercel.app"
    timings = []

    def fetch(method, path, expected=200, **kwargs):
        start = perf_counter()
        response = session.request(method, url.rstrip("/") + path, timeout=25, **kwargs)
        timings.append({"method": method, "path": path, "seconds": round(perf_counter()-start, 3),
                        "status": response.status_code})
        if response.status_code != expected:
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
    if live:
        request = {**query, "operation": "insar-network"}
        first = session.post(url.rstrip("/") + "/v2/jobs", json=request, timeout=25)
        first.raise_for_status()
        job = first.json()
        duplicate = session.post(url.rstrip("/") + "/v2/jobs", json=request, timeout=25)
        duplicate.raise_for_status()
        assert duplicate.json()["job_id"] == job["job_id"]
        for _ in range(40):
            job = fetch("GET", job["status_url"]).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            sleep(3)
        assert job["status"] == "succeeded", job
        assert fetch("POST", "/v2/jobs", 200, json=request).json()["cache"] == "hit"
    print(json.dumps(timings, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--live", action="store_true", help="Use one quota slot to test a catalog-only job.")
    args = parser.parse_args()
    smoke(args.url, args.live)
