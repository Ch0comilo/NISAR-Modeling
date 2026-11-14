"""Summarize reproducible worker and browser evidence from one benchmark directory."""
import argparse
import csv
import json
from pathlib import Path


def row(path, record):
    modal = record.get("modal") or record
    result = modal.get("result") or {}
    science = result.get("science", {})
    trial = science.get("experiment", {})
    radar = (science.get("lens") or {}).get("radar") or {}
    grid = radar.get("grid") or {}
    timings = science.get("timings", {})
    reads = list(trial.get("reads", {}).values())
    pngs = record.get("pngs", [])
    return {
        "run": path.as_posix(),
        "site": record.get("site"),
        "reader": record.get("reader", trial.get("reader")),
        "requested_posting_m": record.get("posting", trial.get("posting")),
        "native_window": record.get("window", trial.get("native_window_pixels")),
        "revision": result.get("revision"),
        "job": modal.get("job_id"),
        "backend_status": record.get("backendStatus"),
        "backend_http": record.get("backendHttp"),
        "modal_status": modal.get("status"),
        "science_status": result.get("science_status"),
        "job_cache": ((record.get("modalSubmit") or {}).get("modal") or {}).get("cache"),
        "worker_instance": trial.get("worker_instance"),
        "worker_invocation": trial.get("worker_invocation"),
        "region": trial.get("region"),
        "queue_s": trial.get("queue_seconds"),
        "read_s": timings.get("prefetch_s"),
        "aggregate_s": timings.get("aggregate_s"),
        "analysis_s": timings.get("analysis_s"),
        "render_s": result.get("visualization_seconds"),
        "worker_s": result.get("elapsed_seconds"),
        "click_to_drape_s": record.get("firstVisibleSeconds"),
        "click_to_visible_panel_s": record.get("firstPanelVisibleSeconds"),
        "click_to_png_response_s": min((p["seconds"] for p in pngs), default=None),
        "asf_requests": sum(r.get("http_requests", 0) for r in reads) if reads else None,
        "asf_bytes": sum(r.get("asf_bytes", 0) for r in reads) if reads else None,
        "range_batches": sum(r.get("range_batches", 0) for r in reads) if reads else None,
        "batch_ranges": sum(r.get("batch_ranges", 0) for r in reads) if reads else None,
        "peak_scene_requests": max((r.get("peak_requests", 0) for r in reads), default=None),
        "chip_hits": sum(r.get("chip_hit", False) for r in reads) if reads else None,
        "usable_scenes": science.get("usable_window_date_count"),
        "epsg": grid.get("epsg"),
        "bounds": grid.get("bounds_native"),
        "posting_m": grid.get("pixel_spacing_m"),
        "shape": [grid.get("height"), grid.get("width")] if grid else None,
        "grid_cells": radar.get("grid_cells"),
        "common_cells": radar.get("common_cells"),
        "browser_png_sizes": sorted({f'{p["width"]}x{p["height"]}' for p in pngs}),
        "browser_png_bytes": [p["bytes"] for p in pngs],
        "backend_error": (record.get("backendError") or {}).get("reasonCode"),
        "error": (record.get("backendSubmit") or {}).get("reasonCode") or record.get("error")
        or (modal.get("error") or {}).get("code") or science.get("reason"),
        "read_failures": sorted({r["failure"] for r in reads if "failure" in r}),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    rows = []
    paths = [*args.directory.glob("*.json"), *(args.directory / "retry").glob("*.json")]
    for path in sorted(paths):
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if ("selector" in record and "site" in record) or ("job_id" in record and
                record.get("status") in {"succeeded", "failed"}):
            rows.append(row(path.relative_to(args.directory), record))
    with (args.directory / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
