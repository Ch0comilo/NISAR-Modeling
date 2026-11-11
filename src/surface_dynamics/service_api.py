"""FastAPI entry point for the Modal scientific backend."""
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Annotated, Literal

from fastapi import FastAPI, Path as ApiPath, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException
import requests

from . import __version__
from .catalog import group_entries, search
from .service_artifacts import ASSET_LIMIT
from .service_contracts import ApiError, Case, CoverageQuery, Hazard, Job, JobRequest, LegacyEvidence, Latitude, Longitude
from .service_state import Rejected


CaseID = Annotated[str, ApiPath(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
JobID = Annotated[str, ApiPath(pattern=r"^[a-f0-9]{32}$")]
AssetName = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,180}\.(png|tif|nc|csv|geojson)$")]
ORIGINS = ["https://nisar-hazard-watch.vercel.app", "http://localhost:5173"]
PUBLIC_CACHE = "public, max-age=300"
MAX_BODY = 8192


class Results:
    def __init__(self, root, reload):
        self.root = root
        self.reload = reload
        self.lock = RLock()
        self.loaded = float("-inf")

    def access(self, path, limit):
        with self.lock:
            now = monotonic()
            if now - self.loaded >= 1:
                self.reload()
                self.loaded = now
            target = self.root / path
            if not target.is_file() or target.is_symlink():
                raise Rejected(404, "asset_not_found", "The requested resource is unavailable.")
            if target.stat().st_size > limit:
                raise Rejected(413, "asset_too_large", "The requested resource exceeds the delivery limit.")
            return target.read_bytes()

    def json(self, path):
        return json.loads(self.access(path, 4 * 1024 * 1024))


@lru_cache(maxsize=512)
def catalog(query_json, _minute):
    from .network import acquisition_dates

    q = CoverageQuery.model_validate_json(query_json)
    data = search(q.longitude, q.latitude, q.product, f"{q.start_date}T00:00:00Z",
                  f"{q.end_date}T23:59:59.999Z", max_records=200, timeout=(3, 8))
    series = []
    for key, entries in sorted(group_entries(data["entries"], q.product).items()):
        observations = []
        for row in entries:
            dates = acquisition_dates(row["title"]) if q.product == "GUNW" else (row["time_start"][:10],)
            if all(q.start_date.isoformat() <= d <= q.end_date.isoformat() for d in dates):
                observations.append({"id": row["title"], "dates": dates})
        if observations:
            series.append({"key": key, "observations": observations})
    return {"api_version": "2.0", "query": q.model_dump(mode="json"), "catalog_hits": data["hits"],
            "catalog_queried_at_utc": data["queried_at_utc"], "pixel_support": "unchecked",
            "coverage_status": "catalog_match" if series else "no_catalog_match", "series": series}


def artifact_response(body, asset, request):
    etag = '"' + asset["sha256"] + '"'
    headers = {"ETag": etag, "Cache-Control": PUBLIC_CACHE, "Accept-Ranges": "bytes"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    selected = request.headers.get("range")
    if selected and request.headers.get("if-range", etag) == etag:
        length = len(body)
        try:
            start, end = selected.removeprefix("bytes=").split("-")
            if not selected.startswith("bytes=") or not (start or end):
                raise ValueError
            if start:
                first, last = int(start), min(int(end), length - 1) if end else length - 1
            else:
                first, last = max(0, length - int(end)), length - 1
            if not 0 <= first <= last < length:
                raise ValueError
        except ValueError:
            return JSONResponse({"message": "Use one valid byte range.", "code": "invalid_range"},
                                status_code=416, headers={"Content-Range": f"bytes */{length}"})
        headers["Content-Range"] = f"bytes {first}-{last}/{length}"
        return Response(body[first:last + 1], 206, headers, asset["media_type"])
    return Response(body, headers=headers, media_type=asset["media_type"])


def create_app(root: Path, jobs, *, reload=lambda: None, observation_page: str | None = None):
    results = Results(root, reload)
    rate = OrderedDict()
    rate_lock = RLock()
    errors = {code: {"model": ApiError} for code in (400, 404, 409, 411, 413, 422, 429, 502, 503, 504)}
    app = FastAPI(title="NISAR Measured Surface Dynamics", version="2.0",
                  responses=errors, openapi_url="/v2/openapi.json")

    def problem(status, code, message):
        return JSONResponse({"message": message, "code": code}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def guard(request, call_next):
        if request.method == "POST":
            try:
                size = int(request.headers.get("content-length", ""))
            except ValueError:
                return problem(411, "length_required", "Set Content-Length for a JSON job request.")
            if not 0 <= size <= MAX_BODY:
                return problem(413, "body_too_large", "Job JSON is limited to 8 KiB.")
        key = request.client.host if request.client else "unknown"
        now = int(monotonic() // 60)
        with rate_lock:
            minute, count = rate.get(key, (now, 0))
            count = count if minute == now else 0
            if count >= 120:
                return problem(429, "rate_limited", "This client reached its request rate limit.")
            rate[key] = (now, count + 1)
            rate.move_to_end(key)
            if len(rate) > 8192:
                rate.popitem(last=False)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", "Range", "If-None-Match", "If-Range"],
                       expose_headers=["ETag", "Retry-After", "Location", "Content-Range", "Accept-Ranges"])

    @app.exception_handler(Rejected)
    async def rejected(_request, error):
        response = problem(error.status, error.code, error.message)
        if error.status in {429, 503}:
            response.headers["Retry-After"] = "86400" if error.code == "daily_budget" else "10"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        return problem(error.status_code, "http_error", str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def bad_input(_request, _error):
        return problem(422, "invalid_request", "The request failed API schema validation.")

    @app.exception_handler(Exception)
    async def unexpected(_request, _error):
        return problem(500, "service_error", "The request could not be processed.")

    @app.get("/health")
    def health():
        return {"status": "ok", "api_version": "2.0", "science_version": __version__}

    if observation_page is not None:
        @app.get("/mock", response_class=HTMLResponse, include_in_schema=False)
        def mock():
            return HTMLResponse(observation_page, headers={"Cache-Control": PUBLIC_CACHE})

    @app.get("/v2/capabilities")
    def capabilities():
        return {"api_version": "2.0", "science_version": __version__, "revision": jobs.revision,
                "radar_band": "L", "window_days_limit": 366, "live_date_limit": 5,
                "gunw_pair_limit": 3, "public_job_timeout_seconds": 240,
                "daily_new_jobs_per_ip": jobs.jobs_per_ip_day, "daily_new_jobs_total": jobs.jobs_per_day,
                "measurement": ["GCOV gamma0", "signed dB change", "GUNW coherence", "wrapped phase"],
                "forecast": None, "classification": None, "los_displacement_cm": None,
                "origins": ORIGINS, "case_mode": "precomputed", "point_shape": "native square window"}

    @app.get("/v2/coverage")
    def coverage(query: Annotated[CoverageQuery, Query()], response: Response):
        try:
            result = catalog(query.model_dump_json(), int(monotonic() // 60))
        except requests.Timeout as exc:
            raise Rejected(504, "catalog_timeout", "The public catalog timed out.") from exc
        except requests.RequestException as exc:
            raise Rejected(502, "catalog_unavailable", "The public catalog is unavailable.") from exc
        except ValueError as exc:
            raise Rejected(422, "catalog_limit", "Narrow the catalog search window.") from exc
        response.headers["Cache-Control"] = "public, max-age=60"
        return result

    @app.get("/v2/cases", response_model=list[Case])
    def cases(response: Response):
        response.headers["Cache-Control"] = PUBLIC_CACHE
        return results.json("cases/index.json")

    @app.get("/v2/cases/{case_id}", response_model=Case)
    def case(case_id: CaseID, response: Response):
        response.headers["Cache-Control"] = PUBLIC_CACHE
        return results.json(f"cases/{case_id}/manifest.json")

    @app.get("/v2/cases/{case_id}/series")
    def series(case_id: CaseID, response: Response):
        response.headers["Cache-Control"] = PUBLIC_CACHE
        return results.json(f"cases/{case_id}/series.json")

    @app.post("/v2/jobs", response_model=Job, status_code=202, responses={200: {"model": Job}})
    def submit(body: JobRequest, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        view = jobs.submit(body, sha256(client.encode()).hexdigest()[:24])
        response.headers.update({"Location": view.status_url, "Cache-Control": "no-store", "Retry-After": "3"})
        response.status_code = 200 if view.status == "succeeded" else 202
        return view

    @app.get("/v2/jobs/{job_id}", response_model=Job)
    def poll(job_id: JobID, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return jobs.get(job_id)

    def serve_artifact(folder, assets, name, request):
        asset = next((record for record in assets if record["name"] == name), None)
        if asset is None:
            raise Rejected(404, "asset_not_found", "The asset is outside the published manifest.")
        response = artifact_response(results.access(f"{folder}/{name}", ASSET_LIMIT), asset, request)
        jobs.reserve_delivery(len(response.body))
        return response

    @app.get("/v2/cases/{case_id}/layers/{name}")
    def case_layer(case_id: CaseID, name: AssetName, request: Request):
        folder = f"cases/{case_id}"
        return serve_artifact(folder, results.json(f"{folder}/assets.json"), name, request)

    @app.get("/v2/jobs/{job_id}/layers/{name}")
    def job_layer(job_id: JobID, name: AssetName, request: Request):
        view = jobs.get(job_id)
        if view.status != "succeeded":
            raise Rejected(409, "result_pending", "The job result is unavailable.")
        return serve_artifact(f"jobs/{job_id}", view.result["artifacts"], name, request)

    @app.get("/frames/{track}-{frame}/analysis", response_model=LegacyEvidence)
    def legacy(track: int, frame: int, hazard: Hazard, before: str, after: str,
               contract: Literal["2"] | None = None, lon: Longitude | None = None,
               lat: Latitude | None = None):
        if contract is None:
            raise Rejected(409, "contract_upgrade_required", "Opt into contract=2 and render measured evidence.")
        if (lon is None) != (lat is None):
            raise Rejected(422, "invalid_request", "Supply both lon and lat, or omit both.")
        for row in results.json("cases/index.json"):
            t, _, f, *_ = row["series_key"].split("_")
            if (track, frame) != (int(t), int(f)):
                continue
            deformation = row["id"] == "kumamoto"
            if (hazard == "earthquake") != deformation:
                continue
            if not deformation and hazard != "flood":
                continue
            if before != row["baseline_dates"][-1] or after != row["assessment_dates"][0]:
                continue
            if lon is not None and (lon, lat) != tuple(row["point"]):
                continue
            return LegacyEvidence(kind="deformation" if deformation else "change", hazard=hazard,
                                  track=track, frame=frame, before=before, after=after,
                                  evidence=row["evidence"], layers=row["layers"],
                                  case_url=f"/v2/cases/{row['id']}")
        raise Rejected(404, "case_not_available", "No curated match. Use coverage and submit a v2 job.")

    return app
