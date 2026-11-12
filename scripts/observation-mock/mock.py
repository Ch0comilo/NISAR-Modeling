"""Local measured-observation bench for the existing science workers."""
import argparse
from datetime import date, timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
import json
import logging
import os
from pathlib import Path
import re
import sys
from threading import BoundedSemaphore
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK_DIR = ROOT / "scratch" / "observation-mock"
RUNS = WORK_DIR / "runs"
BASE = "https://ch0comilo--nisar-evidence-api.us-east.modal.run"
MAX_ASSET = 24 * 1024 * 1024
os.environ.setdefault("MPLCONFIGDIR", str(WORK_DIR / ".mpl"))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = {
    "chokwe": ("Chókwè", "flood_pilot/cases/chokwe/cube.npz", "2026-01-17", "2026-01-05", "flood_wetland"),
    "qadirabad": ("Qadirabad", "flood_pilot/cases/qadirabad/cube.npz", "2026-07-26", "2026-07-14", "flood_wetland"),
    "richmond": ("Richmond", "flood_pilot/cases/richmond/cube.npz", "2026-08-16", "2026-07-23", "flood_wetland"),
    "rowe_creek": ("Rowe Creek", "characterization_v05/cases/rowe_creek/cube.npz", "2026-09-23", "2026-07-13", "wildfire"),
    "wheatbelt": ("Australian Wheatbelt", "characterization_v05/cases/wheatbelt/cube.npz", "2026-10-15", "2026-07-23", "agriculture"),
    "hokkaido": ("Hokkaido", "global_v2/cubes/hokkaido.npz", "2026-10-17", "2026-08-30", "analysis"),
}
WORK = BoundedSemaphore(1)


def fixture_path(key):
    return ROOT / "products" / FIXTURES[key][1]


@lru_cache(maxsize=1)
def inventory():
    results = []
    if find_spec("numpy") is None:
        return {"fixtures": results}
    import numpy as np

    for key, (title, _, target, cutoff, mode) in FIXTURES.items():
        path = fixture_path(key)
        if not path.is_file():
            continue
        with np.load(path, allow_pickle=False) as cube:
            results.append({"id": key, "title": title, "dates": cube["dates"].astype(str).tolist(),
                            "polarizations": cube["polarizations"].tolist(),
                            "posting_m": float(cube["x"][1] - cube["x"][0]),
                            "target": target, "cutoff": cutoff, "mode": mode})
    return {"fixtures": results}


def lens_result(key, cube, dates, target, cutoff, operation, out):
    import numpy as np
    from surface_dynamics.exploration_lenses import agriculture_history, evaluate_lens, prepare_vegetation_lens_case
    from surface_dynamics.flood import load_reference

    if key == "rowe_creek" and (target, cutoff) == FIXTURES[key][2:4] and operation in {
        "wildfire", "vegetation_disturbance"
    }:
        request = json.loads((ROOT / "contracts/examples/rowe_creek-characterization.json").read_text(encoding="utf-8"))
        request["source"]["cube_path"] = str(ROOT / request["source"]["cube_path"])
        request["disturbance_reference"] = str(ROOT / request["disturbance_reference"])
        verified = out / "verified-request.json"
        verified.write_text(json.dumps(request), encoding="utf-8")
        report = prepare_vegetation_lens_case(verified, out, lens_id=operation)
        import rasterio
        layer = out / "target_change_db.tif"
        with rasterio.open(layer) as dataset:
            return report, dataset.read()
    optical = None
    optical_info = None
    reference = fixture_path(key).with_name("reference_pair.npz")
    if operation == "flood_wetland" and key in {"chokwe", "qadirabad", "richmond"} and reference.is_file():
        prior = [day for day in dates if day <= cutoff]
        boundary = target
        if (target, cutoff) == FIXTURES[key][2:4]:
            contract = ROOT / f"contracts/examples/{key}-flood.json"
            boundary = json.loads(contract.read_text(encoding="utf-8"))["assessment"]["start_date"]
        optical, optical_info = load_reference(reference, cube, prior[-1], target, boundary)
    report, arrays = evaluate_lens(cube, target, operation, baseline_end=cutoff, reference=optical)
    if optical_info:
        report["evidence"]["fixture_date_check"] = {k: optical_info[k] for k in
                                                  ("status", "pre_date", "post_date", "pre_offset_days", "post_offset_days")}
    change = arrays.get("target_change_db")
    if change is not None and operation == "agriculture":
        lon, lat = center(cube)[2]
        report["neighborhood"] = agriculture_history(cube, report, np.isfinite(change).all(axis=0), lon, lat)
    return report, change


def center(cube):
    from rasterio.warp import transform

    row, col = len(cube["y"]) // 2, len(cube["x"]) // 2
    lon, lat = transform(f"EPSG:{int(cube['epsg'])}", "EPSG:4326",
                         [float(cube["x"][col])], [float(cube["y"][row])])
    return row, col, [lon[0], lat[0]]


def run_fixture(payload):
    import numpy as np
    from surface_dynamics.analysis import measured_db
    from surface_dynamics.engine import analyze, safe_json
    from surface_dynamics.exploration_lenses import LENSES
    from surface_dynamics.fingerprint_worker import process_fingerprint
    from surface_dynamics.io import load_cube, save_cube
    from surface_dynamics.probe import probe
    from surface_dynamics.service_artifacts import render_layer

    if set(payload) != {"fixture", "target", "cutoff", "operation"}:
        raise ValueError("Select a fixture, target, baseline cutoff and worker.")
    key, target, cutoff, operation = (payload[name] for name in ("fixture", "target", "cutoff", "operation"))
    if key not in FIXTURES or operation not in {*LENSES, "analysis", "fingerprint"}:
        raise ValueError("Select a listed fixture and worker.")
    if not fixture_path(key).is_file():
        raise ValueError("Restore the selected release fixture with scripts/download_examples.py.")
    started = perf_counter()
    cube = load_cube(fixture_path(key))
    dates = cube["dates"].astype(str).tolist()
    if target not in dates or cutoff not in dates or cutoff >= target:
        raise ValueError("Select two ordered dates from this measured fixture.")
    run = uuid4().hex
    out = RUNS / run
    out.mkdir(parents=True)
    source = json.loads(str(cube.get("source_metadata_json", "[]")))
    index = dates.index(target)
    row, col, point = center(cube)
    values = measured_db(cube)[index, :, row, col]
    point_values = safe_json(dict(zip(cube["polarizations"], values)))
    prefix = f"/runs/{run}"
    pngs = []
    if operation in LENSES:
        report, changes = lens_result(key, cube, dates, target, cutoff, operation, out)
        if changes is not None:
            for pol, band in zip(report["radar"]["polarizations"], changes):
                layer = render_layer(out, prefix, f"change_{pol}", band, cube["x"], cube["y"],
                                     int(cube["epsg"]), units="dB", cmap="RdBu_r", stretch=(-8, 8))
                pngs.append({"title": f"{pol} target minus measured baseline (dB)", "url": layer["png_url"],
                             "download": layer["cog_url"]})
        metrics = {"baseline_dates": report["baseline_dates"], "radar": report["radar"],
                   "evidence": report["evidence"], "neighborhood": report.get("neighborhood"),
                   "reason": report.get("reason")}
    elif operation == "fingerprint":
        report = process_fingerprint(
            {"contract_version": "1.0", "request_id": run,
             "source": {"mode": "local_cube", "cube_path": str(fixture_path(key))},
             "baseline": {"start_date": dates[0], "end_date": cutoff},
             "assessment": {"start_date": (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat(),
                            "end_date": target}, "maximum_points": 4}, out)
        metrics = {"quality": report["quality"], "features": report["features"],
                   "phenomenon_class": report["phenomenon_class"]}
    else:
        subset = {**cube, **{name: cube[name][:index + 1] for name in ("power", "valid", "dates")}}
        if len(source) == len(dates):
            subset["source_metadata_json"] = np.array(json.dumps(source[:index + 1]))
        file = out / "input.npz"
        save_cube(subset, file)
        report = analyze({"contract_version": "1.0", "request_id": run,
                          "source": {"mode": "local_cube", "cube_path": str(file)},
                          "analyses": ["physical", "spectral", "stochastic", "transformer"],
                          "transformer_checkpoint": str(ROOT / "models/global_explorer_v2/masked_temporal_transformer.pt")}, out)
        file.unlink()
        metrics = {"acquisition_count": len(subset["dates"]),
                   "capabilities": {name: item["status"] for name, item in report["capabilities"].items()}}
        dataset = out / "surface_dynamics.nc"
        if dataset.is_file():
            selected = probe({"contract_version": "1.0", "request_id": run,
                              "dataset_path": str(dataset), "longitude": point[0], "latitude": point[1]})
            latent = selected.get("latent_at_selected_pixel")
            metrics["point_probe"] = {"status": selected["status"], "latent_dimensions": len(latent) if latent else None,
                                      "target_values": [r for r in selected["observed_series"] if r["date"] == target]}
        for artifact in report["artifacts"]:
            if artifact["href"].endswith(".png"):
                pngs.append({"title": artifact["role"].replace("_", " "), "url": f"{prefix}/{artifact['href']}"})
    payload = safe_json({
        "source": "Measured local release fixture", "fixture": FIXTURES[key][0], "worker": operation,
        "target": target, "status": report["status"], "selected_point": point, "selected_pixel": [row, col],
        "target_gamma0_db": point_values, "target_source": source[index] if len(source) == len(dates) else None,
        "posting_m": float(cube["x"][1] - cube["x"][0]), "seconds": round(perf_counter() - started, 3),
        "metrics": metrics, "images": pngs, "report_url": f"{prefix}/report.json"
    })
    (out / "report.json").write_text(json.dumps(safe_json(report), indent=2, allow_nan=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    return payload


class Handler(BaseHTTPRequestHandler):
    def output(self, data, code=200, mime="application/json"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def problem(self, text, code):
        self.output({"message": text}, code)

    def local_host(self):
        return self.headers.get("Host", "") in {f"{host}:{self.server.server_port}" for host in ("127.0.0.1", "localhost")}

    def modal(self, path, *, artifact=False, body=None):
        data = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        method = "GET" if data is None else "POST"
        payload = bytearray()
        with requests.request(method, BASE + path, data=data, headers=headers, stream=True,
                              timeout=(5, 20), allow_redirects=False) as response:
            status = response.status_code
            mime = response.headers.get("Content-Type", "application/octet-stream")
            for chunk in response.iter_content(64 * 1024):
                if len(payload) + len(chunk) > MAX_ASSET:
                    return self.problem("Modal returned an oversized response.", 502)
                payload.extend(chunk)
        if artifact and 200 <= status < 300:
            return self.output(bytes(payload), status, mime)
        try:
            body = json.loads(payload)
        except ValueError:
            body = {"message": f"Modal responded with HTTP {status}."}
        self.output(body, status)

    def do_GET(self):
        if not self.local_host():
            return self.problem("Use the localhost address printed by the mock.", 403)
        url = urlsplit(self.path)
        try:
            if url.path == "/":
                return self.output((HERE / "index.html").read_bytes(), mime="text/html; charset=utf-8")
            if url.path == "/fixtures":
                return self.output(inventory())
            if url.path == "/modal/health":
                return self.modal("/health")
            if url.path == "/modal/capabilities":
                return self.modal("/v2/capabilities")
            if url.path == "/modal/coverage":
                return self.modal("/v2/coverage?" + url.query)
            match = re.fullmatch(r"/modal/jobs/([a-f0-9]{32})", url.path)
            if match:
                return self.modal("/v2/jobs/" + match[1])
            match = re.fullmatch(
                r"/modal(/v2/jobs/[a-f0-9]{32}/layers/[A-Za-z0-9_-]{1,180}\.(?:png|gif|tif|nc|csv|geojson))", url.path,
            )
            if match:
                return self.modal(match[1], artifact=True)
            match = re.fullmatch(r"/runs/([a-f0-9]{32})/([A-Za-z0-9_-]+\.(png|tif|json))", url.path)
            if match and (path := RUNS / match[1] / match[2]).is_file():
                mime = {"png": "image/png", "tif": "image/tiff", "json": "application/json"}[match[3]]
                return self.output(path.read_bytes(), mime=mime)
            self.problem("Resource unavailable.", 404)
        except requests.RequestException:
            self.problem("Could not reach the Modal API.", 502)
        except ValueError:
            self.problem("Enter valid coordinates and an increasing date window of up to 366 days.", 422)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if (not self.local_host() or self.headers.get("Content-Type") != "application/json"
                or (origin is not None and origin != "http://" + self.headers["Host"])):
            return self.problem("Use this local page and submit JSON.", 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 8192:
                return self.problem("Request must contain at most 8 KiB of JSON.", 413)
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                return self.problem("Use a JSON object.", 422)
            if self.path == "/modal/jobs":
                return self.modal("/v2/jobs", body=body)
            if self.path == "/fixture/run":
                if not WORK.acquire(blocking=False):
                    return self.problem("A local worker is already running.", 429)
                try:
                    return self.output(run_fixture(body))
                finally:
                    WORK.release()
            self.problem("Resource unavailable.", 404)
        except requests.RequestException:
            self.problem("Could not reach the Modal API.", 502)
        except ImportError:
            self.problem("Install the science dependencies to run measured fixtures. See the mock guide.", 503)
        except ValueError as error:
            self.problem(str(error).splitlines()[0], 422)
        except Exception:
            logging.exception("Mock worker failed")
            self.problem("The worker failed. Check the local terminal for details.", 500)


def verify():
    checks = [("chokwe", "flood_wetland"), ("rowe_creek", "vegetation_disturbance"),
              ("rowe_creek", "wildfire"), ("wheatbelt", "agriculture"),
              ("qadirabad", "fingerprint"), ("hokkaido", "analysis")]
    reports = []
    for key, operation in checks:
        _, _, target, cutoff, _ = FIXTURES[key]
        result = run_fixture({"fixture": key, "target": target, "cutoff": cutoff, "operation": operation})
        assert result["status"] in {"complete", "partial", "evaluated"}, result
        reports.append(result)
        print(f"{operation} {result['status']} in {result['seconds']} s", flush=True)
    assert reports[0]["metrics"]["evidence"]["status"] == "limited_support"
    model = reports[-1]["metrics"]
    assert model["capabilities"]["transformer"] == "experimental" and model["point_probe"]["latent_dimensions"] == 48, model
    assert reports[3]["metrics"]["neighborhood"]["status"] == "evaluated"
    (WORK_DIR / "verified-results.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--verify", action="store_true", help="Run six measured checks from the release fixtures.")
    args = parser.parse_args()
    if args.verify:
        verify()
    else:
        address = ("127.0.0.1", args.port)
        print(f"Open http://127.0.0.1:{args.port}", flush=True)
        ThreadingHTTPServer(address, Handler).serve_forever()
