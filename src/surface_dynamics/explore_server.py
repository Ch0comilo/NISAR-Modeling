"""Loopback HTTP entry point for the exploration worker."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import requests

from .explore import chips_cached, discover, enrich_lens, measure, parse_request, select, select_alternate
from .exploration_lenses import LENSES, empty_lens
from .flood_lens import with_evidence
from .feature_lab import empty_feature_lab
from .exploration_insar import discover_pairs, measure_pair, pair_cache_path, parse_pair_request


ACTIVE = {"queued", "running"}
OPTICAL_RETRY = {"optical_catalog_unavailable", "optical_assets_unavailable",
                 "optical_deadline_elapsed", "optical_queue_full", "optical_worker_error"}


class ExploreJobs:
    def __init__(self, cache, output):
        self.cache = cache
        self.output = output
        self.jobs = {}
        self.keys = {}
        self.lock = Lock()
        # One writer owns the on-disk chip cache during this server process.
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.cached_worker = ThreadPoolExecutor(max_workers=1)
        self.optical_worker = ThreadPoolExecutor(max_workers=1)
        self.pair_worker = ThreadPoolExecutor(max_workers=1)

    def _make_room(self):
        if len(self.jobs) >= 32:
            oldest = next(job_id for job_id, state in self.jobs.items() if state["status"] not in ACTIVE)
            del self.jobs[oldest]
            self.keys = {k: v for k, v in self.keys.items() if v != oldest}
            rmtree(self.output / oldest, ignore_errors=True)

    def submit(self, query, coverage, key, entries, reason, alternate=None):
        choices = [(key, entries)] + ([alternate] if alternate is not None else [])
        signature = (query, tuple((series, tuple(entry["title"] for entry in rows)) for series, rows in choices))
        with self.lock:
            existing = self.keys.get(signature)
            if existing:
                state = self.jobs[existing]
                retryable = state["status"] == "failed" or any(
                    row["status"] == "unavailable" for row in state["observations"]
                ) or (state["lens"] or {}).get("evidence", {}).get("reason") in OPTICAL_RETRY
                if not retryable or state["status"] in ACTIVE:
                    state = {**state, "coverage": coverage}
                    self.jobs[existing] = state
                    return state
            cache_only = all(chips_cached(query, rows, self.cache) for _, rows in choices)
            active_count = sum(
                state["status"] in ACTIVE and state["phase"] != "optical_evidence"
                and state.get("product") != "GUNW"
                and state["cache_only"] == cache_only
                for state in self.jobs.values()
            )
            if entries and active_count >= (2 if cache_only else 4):
                return None
            self._make_room()
            job_id = uuid4().hex
            status = "queued" if entries else "no_data"
            state = {
                "job_id": job_id, "status_url": f"/v1/jobs/{job_id}", "status": status,
                "coverage": coverage, "series_key": key,
                "selected_dates": [entry["time_start"][:10] for entry in entries],
                "series_attempts": [],
                "optical_evidence_requested": query.optical_evidence,
                "observations": [], "comparison": None, "reason": reason, "cache_only": cache_only,
                "lens": empty_lens(None, [], reason, query.lens) if query.lens and not entries else None,
                "feature_lab": empty_feature_lab(reason or "acquisitions_pending", "pending" if entries else "insufficient_data")
                if query.feature_lab else None,
                "phase": "radar" if entries else None,
            }
            self.jobs[job_id] = state
            self.keys[signature] = job_id
            if entries:
                worker = self.cached_worker if cache_only else self.worker
                worker.submit(self._run, job_id, query, choices, cache_only)
            return state

    def submit_pair(self, query, coverage, entry):
        path = pair_cache_path(query, entry, self.cache)
        signature = ("GUNW", path.name, query.coherence_threshold)
        with self.lock:
            existing = self.keys.get(signature)
            if existing and self.jobs[existing]["status"] not in {"failed", "unavailable"}:
                state = {**self.jobs[existing], "coverage": coverage}
                self.jobs[existing] = state
                return state
            if sum(state.get("product") == "GUNW" and state["status"] in ACTIVE
                   for state in self.jobs.values()) >= 2:
                return None
            self._make_room()
            job_id = uuid4().hex
            state = {
                "job_id": job_id, "status_url": f"/v1/jobs/{job_id}", "status": "queued",
                "phase": "insar", "product": "GUNW", "coverage": coverage,
                "pair": next(pair for pair in coverage["pairs"] if pair["id"] == query.pair_id),
                "insar": None, "cache_available": path.is_file() and path.with_suffix(".json").is_file(),
            }
            self.jobs[job_id] = state
            self.keys[signature] = job_id
            self.pair_worker.submit(self._run_pair, job_id, query, entry, path)
            return state

    def _run_pair(self, job_id, query, entry, path):
        with self.lock:
            self.jobs[job_id] = {**self.jobs[job_id], "status": "running"}
        try:
            out = self.output / job_id
            out.mkdir()
            measured = measure_pair(query, entry, path, out)
        except Exception as exc:
            print(f"Explore InSAR job {job_id} failed ({type(exc).__name__})", flush=True)
            measured = {"status": "failed", "reason": "worker_error"}
        with self.lock:
            self.jobs[job_id] = {**self.jobs[job_id], "status": measured["status"], "phase": None, "insar": measured}

    def _run(self, job_id, query, choices, cache_only):
        def publish(**fields):
            with self.lock:
                self.jobs[job_id] = {**self.jobs[job_id], **fields}

        attempts = []
        try:
            out = self.output / job_id
            out.mkdir()
            for index, (key, entries) in enumerate(choices):
                dates = [entry["time_start"][:10] for entry in entries]
                publish(status="running", series_key=key, selected_dates=dates,
                        observations=[], comparison=None, lens=None, series_attempts=list(attempts),
                        feature_lab=empty_feature_lab("acquisitions_pending", "pending") if query.feature_lab else None)
                final = {}

                def progress(**fields):
                    if "status" in fields:
                        final.update(fields)
                    else:
                        publish(**fields)

                follow_up = measure(query, entries, self.cache, out, progress, cache_only=cache_only)
                radar_status = follow_up[0] if follow_up is not None else final["status"]
                attempts.append({"series_key": key, "selected_dates": dates, "status": radar_status,
                                 "usable_window_date_count": final["usable_window_date_count"]})
                if radar_status != "no_data" or index == len(choices) - 1:
                    publish(**final, series_attempts=list(attempts))
                    break
        except Exception as exc:
            # Archive/library exception text can contain internal URLs or filesystem paths.
            print(f"Explore job {job_id} failed ({type(exc).__name__})", flush=True)
            publish(status="failed", phase=None, reason="worker_error")
            return
        if follow_up is not None:
            status, lens, paths = follow_up
            with self.lock:
                optical_count = sum(state["phase"] == "optical_evidence" for state in self.jobs.values())
                if optical_count <= 3:
                    self.optical_worker.submit(self._run_optical, query, status, lens, paths, out, publish)
                    return
            evidence = {"status": "unavailable", "reason": "optical_queue_full"}
            publish(status=status, phase=None, lens=with_evidence(lens, evidence))

    def _run_optical(self, query, status, lens, paths, out, publish):
        try:
            evidence = enrich_lens(query, lens, paths, self.cache / "optical", out)
        except Exception as exc:
            print(f"Explore optical job {out.name} failed ({type(exc).__name__})", flush=True)
            evidence = {"status": "unavailable", "reason": "optical_worker_error"}
        publish(status=status, phase=None, lens=with_evidence(lens, evidence))

    def result(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def asset(self, job_id, name):
        with self.lock:
            state = self.jobs.get(job_id)
            if state is None:
                return None
            expected = f"/v1/jobs/{job_id}/assets/{name}"
            lens = state.get("lens") or {}
            sources = [*state.get("observations", []), state.get("comparison") or {},
                       lens.get("radar") or {}, lens.get("evidence") or {}]
            layers = [item.get("layer") or {} for item in sources]
            layers.extend((lens.get("radar") or {}).get("layers", []))
            layers.extend((state.get("insar") or {}).get("layers", []))
            layers.extend((state.get("feature_lab") or {}).get("layers", []))
            if not any(layer.get("href") == expected for layer in layers):
                return None
            return (self.output / job_id / name).read_bytes()


class ExploreHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_request(self, code="-", size="-"):
        # Avoid writing clicked locations to the server log.
        self.log_message("%s %s", self.command, code)

    def _respond(self, status, data, content_type="application/json"):
        if content_type == "application/json":
            data = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _discover(self, query, lookup=discover):
        try:
            return lookup(query)
        except requests.Timeout:
            self._respond(504, {"error": "catalog_timeout"})
        except (requests.RequestException, KeyError, ValueError):
            self._respond(502, {"error": "catalog_unavailable"})
        return None

    def do_GET(self):
        url = urlsplit(self.path)
        parts = url.path.split("/")
        if url.path == "/health":
            self._respond(200, {"status": "ok"})
        elif url.path == "/v1/lenses":
            self._respond(200, {"contract_version": "1.0", "lenses": list(LENSES.values())})
        elif url.path in {"/v1/coverage", "/v1/insar/pairs"}:
            try:
                params = parse_qs(url.query, keep_blank_values=True, max_num_fields=4)
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError("Coverage parameters cannot be repeated.")
                query = parse_request({
                    "longitude": float(params["longitude"][0]),
                    "latitude": float(params["latitude"][0]),
                    "start_date": params["start_date"][0],
                    "end_date": params["end_date"][0],
                })
            except (ValueError, KeyError):
                self._respond(400, {"error": "invalid_coverage_parameters"})
                return
            found = self._discover(query, discover_pairs if url.path == "/v1/insar/pairs" else discover)
            if found is not None:
                self._respond(200, found[0])
        elif parts[1:3] == ["v1", "jobs"]:
            if len(parts) == 4:
                state = self.server.jobs.result(parts[3])
                if state is not None:
                    self._respond(200, state)
                    return
            elif len(parts) == 6 and parts[4] == "assets":
                data = self.server.jobs.asset(parts[3], parts[5])
                if data is not None:
                    self._respond(200, data, "image/tiff")
                    return
            self._respond(404, {"error": "unknown_job_or_asset"})
        else:
            self._respond(404, {"error": "unknown_route"})

    def do_POST(self):
        is_pair = self.path == "/v1/insar"
        if self.path != "/v1/explore" and not is_pair:
            self._respond(404, {"error": "unknown_route"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Expected a JSON body up to 4096 bytes.")
            body = self.rfile.read(length)
            try:
                query = (parse_pair_request if is_pair else parse_request)(json.loads(body))
            except (UnicodeError, json.JSONDecodeError, RecursionError):
                raise ValueError("Expected a valid JSON object.") from None
        except ValueError as exc:
            self._respond(400, {"error": "invalid_request", "detail": str(exc)})
            return
        found = self._discover(query.point, discover_pairs) if is_pair else self._discover(query)
        if found is None:
            return
        coverage, groups = found
        if is_pair:
            entry = groups.get(query.pair_id)
            if entry is None:
                self._respond(404, {"error": "pair_unavailable", "coverage": coverage})
                return
            state = self.server.jobs.submit_pair(query, coverage, entry)
        else:
            key, entries, reason = select(query, groups)
            alternate = select_alternate(query, groups, key)
            state = self.server.jobs.submit(query, coverage, key, entries, reason, alternate)
        if state is None:
            self._respond(503, {"error": "queue_full", "coverage": coverage})
        else:
            self._respond(202 if state["status"] in ACTIVE else 200, state)


def serve(cache, port=8787, *, host="127.0.0.1", handler=ExploreHandler):
    cache = Path(cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="nisar-explore-") as temp:
        with ThreadingHTTPServer((host, port), handler) as server:
            server.jobs = ExploreJobs(cache, Path(temp))
            print(f"NISAR Explore API: http://{host}:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.jobs.cached_worker.shutdown(cancel_futures=True)
                server.jobs.worker.shutdown(cancel_futures=True)
                server.jobs.optical_worker.shutdown(cancel_futures=True)
                server.jobs.pair_worker.shutdown(cancel_futures=True)
