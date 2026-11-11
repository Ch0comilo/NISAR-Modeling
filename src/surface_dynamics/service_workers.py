"""Bounded HTTP-to-science translation. No client-selected paths or archive URLs."""
from datetime import date, timedelta
import logging
import re
from pathlib import Path
from time import perf_counter
from tempfile import TemporaryDirectory

from .service_artifacts import data_layers, publish_files, render_layer, write_json


LOG = logging.getLogger(__name__)
MODEL = "models/global_explorer_v2/masked_temporal_transformer.pt"


def run_explore(body, out, cache):
    from .explore import discover, enrich_lens, measure, parse_request, select, select_alternate
    fields = {"longitude", "latitude", "start_date", "end_date", "series_key",
              "compare_dates", "lens", "optical_evidence", "baseline_end_date"}
    query = parse_request({**{k: v for k, v in body.items() if k in fields}, "max_dates": 5})
    coverage, groups = discover(query)
    key, entries, reason = select(query, groups)
    if not entries:
        return {"status": "no_data", "reason": reason, "coverage": coverage}
    choices = [(key, entries)]
    alternate = select_alternate(query, groups, key)
    if alternate:
        choices.append(alternate)
    attempts = []
    for key, entries in choices:
        state = {}
        def record(**fields):
            state.update(fields)
        followup = measure(query, entries, cache, out, record)
        attempts.append({"series_key": key, "status": state["status"],
                         "usable_window_date_count": state["usable_window_date_count"]})
        if state["status"] != "no_data":
            break
    if followup:
        status, lens, paths = followup
        evidence = enrich_lens(query, lens, paths, cache / "optical", out)
        state.update(status=status, lens={**lens, "evidence": evidence})
    return {**state, "coverage": coverage, "series_key": key, "series_attempts": attempts}


def process(body, job_id, out, cache):
    from .characterization import process_characterization
    from .engine import analyze
    from .fingerprint_worker import process_fingerprint
    from .flood import process_flood
    from .insar_readiness import process_readiness
    from .insar_stack import process_stack
    from .network import plan_network

    mode = body["operation"]
    if mode == "explore":
        return run_explore(body, out, cache)
    source = {"mode": "catalog_point", **{k: body[k] for k in
              ("longitude", "latitude", "start_date", "end_date")}}
    request = {"contract_version": "1.0", "request_id": job_id, "source": source}
    if mode == "insar-pair":
        from .exploration_insar import discover_pairs, measure_pair, pair_cache_path, parse_pair_request
        query = parse_pair_request({k: body[k] for k in
            ("longitude", "latitude", "start_date", "end_date", "pair_id", "coherence_threshold")})
        coverage, entries = discover_pairs(query.point)
        entry = entries.get(query.pair_id)
        if not entry:
            return {"status": "no_data", "reason": "pair_unavailable", "coverage": coverage}
        result = measure_pair(query, entry, pair_cache_path(query, entry, cache), out)
        return {**result, "pair_id": query.pair_id}
    if mode == "insar-network":
        return plan_network(request, out)
    if mode.startswith("insar"):
        request["coherence_threshold"] = body["coherence_threshold"]
        source.update(max_pairs=3, extent_km=5)
        if "series_key" in body:
            source["series_key"] = body["series_key"]
        if mode == "insar-stack":
            return process_stack(request, out)
        with TemporaryDirectory() as staging:
            stack = process_stack(request, staging)
            if stack["status"] == "no_data":
                return stack
            request["source"] = {"mode": "local_pairs",
                                 "pair_paths": [str(p) for p in sorted((Path(staging) / "cache").glob("pair_*.npz"))]}
            return process_readiness(request, out)
    if mode == "analysis":
        source.update(max_dates=5, native_chip_pixels=256,
                      target_posting_m=160 if "transformer" in body["analyses"] else 40)
        if "series_key" in body:
            source["series_key"] = body["series_key"]
        request["analyses"] = body["analyses"]
        if "transformer" in body["analyses"]:
            request["transformer_checkpoint"] = MODEL
        return analyze(request, out)
    source.pop("start_date")
    source.pop("end_date")
    source.update(max_dates=5, native_chip_pixels=256, target_posting_m=40)
    cutoff = date.fromisoformat(body["baseline_end_date"])
    request["baseline"] = {"start_date": body["start_date"], "end_date": cutoff.isoformat()}
    request["assessment"] = {"start_date": (cutoff + timedelta(days=1)).isoformat(), "end_date": body["end_date"]}
    return {"characterization": process_characterization, "flood": process_flood,
            "fingerprint": process_fingerprint}[mode](request, out)


def live_layers(out, prefix, operation):
    """Render continuous GCOV comparison and coherence with fixed scales."""
    import rasterio
    from rasterio.transform import xy

    layers = data_layers(out, prefix)
    if layers:
        return layers
    if operation not in {"explore", "insar-pair"}:
        return layers
    for path in sorted(out.glob("*.tif")):
        if path.stem in {"reference_group", "reference_groups"}:
            continue
        with rasterio.open(path) as source:
            x, _ = xy(source.transform, [0] * source.width, range(source.width))
            _, y = xy(source.transform, range(source.height), [0] * source.height)
            for band in range(1, source.count + 1):
                suffix = source.descriptions[band - 1] or str(band)
                name = f"display_{path.stem}_{suffix}"
                if path.stem == "coherence":
                    units, cmap, stretch = "unitless", "cividis", (0, 1)
                elif path.stem == "wrapped_phase_rad":
                    units, cmap, stretch = "rad", "twilight", (-3.1415926536, 3.1415926536)
                elif "change" in path.stem or "delta" in path.stem:
                    units, cmap, stretch = "dB", "RdBu_r", (-8, 8)
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                    units, cmap, stretch = "dB", "gray", (-30, 0)
                else:
                    continue
                layers.append(render_layer(out, prefix, name, source.read(band), x, y, source.crs.to_epsg(),
                                           units=units, cmap=cmap, stretch=stretch))
    return layers


def published_science(value, hrefs):
    if isinstance(value, list):
        return [published_science(item, hrefs) for item in value]
    if isinstance(value, dict):
        result = {key: published_science(item, hrefs) for key, item in value.items()}
        href = result.get("href")
        if isinstance(href, str) and href.startswith("/v1/jobs/"):
            result["href"] = hrefs.get(href.rsplit("/", 1)[-1])
        return result
    return value


def execute_job(body, job_id, out, cache, revision):
    start = perf_counter()
    out.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)
    raw = process(body, job_id, out, cache)
    prefix = f"/v2/jobs/{job_id}/layers"
    layers = live_layers(out, prefix, body["operation"])
    assets = publish_files(out, prefix)
    allowed = {asset["href"] for asset in assets}
    layers = [layer for layer in layers if {layer["png_url"], layer["cog_url"]} <= allowed]
    raw.pop("artifacts", None)
    raw = published_science(raw, {asset["name"]: asset["href"] for asset in assets})
    if raw.get("status") in {"failed", "unavailable"}:
        raise RuntimeError("The scientific source could not be read.")
    result = {"operation": body["operation"], "science_status": raw["status"], "revision": revision,
              "point": [body["longitude"], body["latitude"]], "isDemo": False if assets else None,
              "science": raw, "layers": layers, "artifacts": assets,
              "elapsed_seconds": round(perf_counter() - start, 3)}
    write_json(out / "manifest.json", result)
    LOG.info("job=%s operation=%s science_status=%s assets=%d seconds=%.3f",
             job_id, body["operation"], raw["status"], len(assets), result["elapsed_seconds"])
    return result
