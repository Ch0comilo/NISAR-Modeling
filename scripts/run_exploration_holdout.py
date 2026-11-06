"""Run a frozen, geographically separated check with the production lens workers."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import file_digest
import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from surface_dynamics.explore import discover, enrich_lens, measure, parse_request, select


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "configs/exploration_holdout_v1.json"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def points(value, label):
    if isinstance(value, dict):
        if type(value.get("longitude")) in (int, float) and type(value.get("latitude")) in (int, float):
            yield (value["longitude"], value["latitude"], value.get("id", label))
        point = value.get("point")
        if isinstance(point, list) and len(point) == 2 and all(type(v) in (int, float) for v in point):
            yield (*point, value.get("id", label))
        for child in value.values():
            yield from points(child, label)
    elif isinstance(value, list):
        for child in value:
            yield from points(child, label)


def distance_km(lon, lat, other_lon, other_lat):
    dlat, dlon = radians(other_lat - lat), radians(other_lon - lon)
    h = sin(dlat / 2)**2 + cos(radians(lat)) * cos(radians(other_lat)) * sin(dlon / 2)**2
    return 12742 * asin(sqrt(min(1, h)))


def freeze(recipe, output):
    manifest = output / "freeze.json"
    inputs = [*sorted((ROOT / "configs").glob("*.json")),
              *sorted((ROOT / "docs/results").glob("*.json")),
              ROOT / "research/worldwide/catalog/summary.json"]
    known = [
        point for path in inputs if path != RECIPE and path.is_file()
        for point in points(json.loads(path.read_text(encoding="utf-8-sig")), path.stem)
        if -180 <= point[0] <= 180 and -90 <= point[1] <= 90
    ]
    checks = {}
    for case in recipe["cases"]:
        km, neighbor = min((distance_km(*case["point"], lon, lat), str(label)) for lon, lat, label in known)
        if km < recipe["minimum_distance_from_known_development_km"]:
            raise ValueError(f"{case['id']} is only {km:.1f} km from {neighbor}.")
        checks[case["id"]] = {"nearest_known_site": neighbor, "distance_km": round(km, 1)}
    source = [RECIPE, Path(__file__).resolve(), *sorted((ROOT / "src/surface_dynamics").glob("*.py")),
              ROOT / "src/surface_dynamics/schemas/flood-policy.json"]
    frozen = {"protocol_id": recipe["protocol_id"], "geographic_checks": checks,
              "sha256": {path.relative_to(ROOT).as_posix(): digest(path) for path in source}}
    if manifest.is_file():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if {k: v for k, v in previous.items() if k != "frozen_at_utc"} != frozen:
            raise ValueError("The holdout rules changed. Retain the original result and use a new protocol.")
    else:
        frozen["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest, frozen)
    return checks


def run_case(case, output):
    path = output / case["id"]
    path.mkdir(exist_ok=True)
    query = parse_request({
        "longitude": case["point"][0], "latitude": case["point"][1],
        **{key: case[key] for key in ("start_date", "end_date", "baseline_end_date")},
        "lens": case["lenses"][0], "max_dates": 5,
    })
    coverage, groups = discover(query)
    key, entries, reason = select(query, groups)
    write_json(path / "coverage.json", coverage)
    result = {
        "case": case, "series_key": key, "catalog_hits": coverage["catalog_hits"],
        "selected_dates": [entry["time_start"][:10] for entry in entries], "reason": reason, "lenses": {},
    }
    for lens_id in case["lenses"]:
        out = path / lens_id
        out.mkdir(exist_ok=True)
        if not entries:
            result["lenses"][lens_id] = {"status": "no_data", "reason": reason}
            continue
        request = replace(query, lens=lens_id, optical_evidence=lens_id != "agriculture")
        job = {}
        follow = measure(request, entries, path / "chips", out, lambda **fields: job.update(fields))
        if follow is not None:
            status, lens, paths = follow
            lens["evidence"] = enrich_lens(request, lens, paths, path / "optical", out)
            job.update(status=status, phase=None, lens=lens)
        write_json(out / "result.json", job)
        lens = job["lens"] or {}
        summary = {
            "status": lens.get("status", job["status"]), "reason": lens.get("reason"),
            "baseline_dates": lens.get("baseline_dates", []), "target_date": lens.get("target_date"),
            "common_cells": (lens.get("radar") or {}).get("common_cells"),
            "evidence_status": lens.get("evidence", {}).get("status"),
            "evidence_reason": lens.get("evidence", {}).get("reason"),
            "agriculture_status": lens.get("agriculture", {}).get("status"),
            "agriculture_reason": lens.get("agriculture", {}).get("reason"),
            "href": f"{case['id']}/{lens_id}/result.json",
        }
        result["lenses"][lens_id] = summary
        print(case["id"], lens_id, summary["status"], summary["evidence_status"], flush=True)
    write_json(path / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "scratch/exploration-holdout-v1")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    checks = freeze(recipe, args.output)
    print("Geographic checks frozen:", checks, flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, case, args.output) for case in recipe["cases"]]
        results = []
        for case, future in zip(recipe["cases"], futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"case": case, "status": "worker_error", "error_type": type(exc).__name__})
    write_json(args.output / "summary.json", {"protocol_id": recipe["protocol_id"], "cases": results})


if __name__ == "__main__":
    main()
