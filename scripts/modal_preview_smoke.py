"""Smoke test of a deployed preview gateway: health, limits and one real InSAR pair job.

Environment: GATEWAY_URL, EXPECTED_GIT_SHA and optionally EXPECTED_JOBS_PER_DAY.
Standard library only, so the workflow needs nothing beyond the Modal client.
"""
from datetime import date, timedelta
import json
import os
import sys
import time
import urllib.error
import urllib.request

KUMAMOTO = (130.65, 32.65)
CATALOG_WINDOW_DAYS = 150
HEALTH_ATTEMPTS = 12
HEALTH_WAIT_SECONDS = 10
POLL_SECONDS = 6
JOB_BUDGET_SECONDS = 420
REQUEST_TIMEOUT_SECONDS = 60
COHERENCE_THRESHOLD = 0.35
DONE = ("succeeded", "failed")


def request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data else {}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def wait_for_health(base, expected_sha):
    """A redeploy can keep answering from the previous version for a while, so wait for the new one."""
    last = None
    for _ in range(HEALTH_ATTEMPTS):
        try:
            last = request(f"{base}/health")
            if last.get("status") == "ok" and last.get("git_sha") == expected_sha:
                return last
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(HEALTH_WAIT_SECONDS)
    if not last or last.get("status") != "ok":
        raise SystemExit(f"/health did not answer ok: {last}")
    raise SystemExit(f"/health reports {last.get('git_sha')}, expected {expected_sha}")


def first_pair(coverage):
    """The first GUNW pair the catalog lists, with its two acquisition days."""
    for series in coverage.get("series", []):
        for observation in series.get("observations", []):
            days = sorted(observation.get("dates", []))
            if len(days) == 2:
                return observation["id"], days[0], days[1]
    return None


def pair_job_body(pair):
    pair_id, start, end = pair
    return {"operation": "insar-pair", "longitude": KUMAMOTO[0], "latitude": KUMAMOTO[1],
            "start_date": start, "end_date": end, "pair_id": pair_id,
            "coherence_threshold": COHERENCE_THRESHOLD}


def run_pair_job(base):
    today = date.today()
    query = (f"longitude={KUMAMOTO[0]}&latitude={KUMAMOTO[1]}&product=GUNW"
             f"&start_date={today - timedelta(days=CATALOG_WINDOW_DAYS)}&end_date={today}")
    pair = first_pair(request(f"{base}/v2/coverage?{query}"))
    if pair is None:
        raise SystemExit("the GUNW catalog listed no pair over Kumamoto")
    started = time.monotonic()
    job = request(f"{base}/v2/jobs", pair_job_body(pair))
    while job.get("status") not in DONE:
        if time.monotonic() - started > JOB_BUDGET_SECONDS:
            raise SystemExit(f"job {job.get('job_id')} did not finish in {JOB_BUDGET_SECONDS} s")
        time.sleep(POLL_SECONDS)
        job = request(f"{base}{job['status_url']}")
    result = job.get("result") or {}
    if job["status"] != "succeeded" or not result.get("layers"):
        raise SystemExit(f"job ended {job['status']} with {len(result.get('layers') or [])} layers: {job.get('error')}")
    return round(time.monotonic() - started), result["science_status"], len(result["layers"])


def main():
    base = os.environ["GATEWAY_URL"].rstrip("/")
    health = wait_for_health(base, os.environ["EXPECTED_GIT_SHA"])
    print(f"health ok at {health['git_sha'][:7]}")
    expected = os.environ.get("EXPECTED_JOBS_PER_DAY")
    if expected:
        limit = request(f"{base}/v2/capabilities")["daily_new_jobs_total"]
        if limit != int(expected):
            raise SystemExit(f"daily job limit is {limit}, expected {expected}")
    seconds, science, layers = run_pair_job(base)
    print(f"insar-pair job {science} in {seconds} s with {layers} layers")


if __name__ == "__main__":
    sys.exit(main())
