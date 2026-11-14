"""Bounded HTTP-to-science translation. No client-selected paths or archive URLs."""
from csv import DictReader
from datetime import date, timedelta
import logging
from math import isfinite
import re
from pathlib import Path
from time import monotonic, perf_counter
from tempfile import TemporaryDirectory

from .service_artifacts import (UnrepresentableDisplayGrid, data_layers, publish_files, published_layers,
                                render_layer, write_json)


LOG = logging.getLogger(__name__)
MODEL = "models/global_explorer_v2/masked_temporal_transformer.pt"
PREVIEWS = {
    "physical.png": ("Observed surface dynamics", "descriptive"),
    "observed_evolution.gif": ("Measured acquisition animation", "descriptive"),
    "spectral.png": ("Candidate temporal periods and their mapped distribution", "experimental"),
    "spatial_spectrum.png": ("Spatial image spectrum", "descriptive"),
    "time_frequency.png": ("Candidate time-frequency structure", "experimental"),
    "stochastic.png": ("Historical Gaussian-process fit", "conditional_model"),
    "learned.png": ("Frozen Transformer representation", "experimental"),
    "learned_evolution.gif": ("Learned state at observed dates", "experimental"),
    "controlled_dynamics.gif": ("Learned contrast against constant trajectory", "experimental"),
}
ANALYSIS_MAPS = {
    "median_db": ("dB", "gray", (-30, 0)),
    "mad_db": ("dB", "magma", (0, 8)),
    "slope_db_per_day": ("dB/day", "RdBu_r", (-0.5, 0.5)),
    "maximum_step_db": ("dB", "magma", (0, 12)),
    "peak_normalized_power": ("normalized power", "viridis", (0, 1)),
}
ANALYSIS_TABLES = {
    "regional_timeseries": ("date", "polarization"),
    "spectral_series": (),
    "gp_historical_series": ("date", "polarization", "status"),
}


def analysis_dashboard(out, dates):
    """Expose the existing bounded CSV series as JSON with the same measured rows."""
    series = {}
    for name, text_fields in ANALYSIS_TABLES.items():
        path = out / f"{name}.csv"
        if path.is_file():
            with path.open(newline="", encoding="utf8") as stream:
                rows = []
                for row in DictReader(stream):
                    record = {}
                    for key, value in row.items():
                        if key in text_fields:
                            record[key] = value
                        else:
                            number = float(value) if value else None
                            record[key] = number if number is not None and isfinite(number) else None
                    rows.append(record)
                series[name] = rows
    days = [date.fromisoformat(day) for day in dates]
    gaps = [(later - earlier).days for earlier, later in zip(days, days[1:])]
    return {"version": "1.0", "acquisition_gaps_days": gaps, "series": series}


def planned_coverage(query, key, entries):
    """Coverage metadata for a backend scene plan; nothing was searched."""
    from .catalog import COLLECTIONS
    from .explore import CONTRACT_VERSION
    return {
        "contract_version": CONTRACT_VERSION,
        "point": {"longitude": query.longitude, "latitude": query.latitude},
        "date_window": [query.start_date, query.end_date],
        "collection": COLLECTIONS["GCOV"],
        "catalog_queried_at_utc": None,
        "catalog_hits": len(entries),
        "coverage_status": "planned_scenes",
        "pixel_support": "unchecked",
        "series": [{"key": key, "dates": [entry["time_start"][:10] for entry in entries],
                    "frequency_a_supported": True}],
    }


def run_explore(body, out, cache):
    from dataclasses import replace

    from . import explore as exploration
    from .explore import discover, enrich_lens, measure, parse_request, select, select_alternate
    from .flood_lens import with_evidence
    fields = {"longitude", "latitude", "start_date", "end_date", "series_key",
              "compare_dates", "lens", "optical_evidence", "baseline_end_date"}
    query = parse_request({**{k: v for k, v in body.items() if k in fields}, "max_dates": 5})
    if body.get("window_pixels"):
        query = replace(query, chip_pixels=body["window_pixels"])
    started = perf_counter()
    timings, sink = {}, {}
    experiment = body.get("gcov_experiment")
    aggregate = 4 if experiment and experiment["posting"] == 40 else 1
    aggregation_timings = {"aggregate_s": 0.0}
    reads = {}
    if experiment:
        cache = cache / "gcov-ab" / experiment["reader"] / experiment["chips"]
    if body.get("scenes"):
        from .plan_input import entries_from_scenes, prefetch_chips

        key, entries = entries_from_scenes(body["scenes"])
        coverage = planned_coverage(query, key, entries)
        choices = [(key, entries)]
        prefetch_start = perf_counter()
        archive_deadline = monotonic() + 150
        def fetch(entry, path):
            options = {"deadline": archive_deadline, "cache_only": False}
            stats = {}
            started_read = perf_counter()
            if experiment:
                reads[entry["id"]] = stats
                options.update(backend=experiment["reader"], stats=stats)
            try:
                return exploration.fetch_chip(entry, query.longitude, query.latitude, path,
                                              pixels=query.chip_pixels, **options)
            except Exception as exc:
                if experiment:
                    stats["failure"] = exploration._read_error(exc)
                    stats["failure_type"] = type(exc).__name__
                    if isinstance(exc, (TypeError, IndexError, NotImplementedError)):
                        from traceback import extract_tb

                        stats["failure_location"] = [
                            f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                            for frame in extract_tb(exc.__traceback__)[-5:]
                        ]
                        raise RuntimeError("Experimental archive reader failed") from exc
                raise
            finally:
                if experiment:
                    stats["seconds"] = round(perf_counter() - started_read, 3)

        failures = prefetch_chips(entries, lambda entry: exploration._chip_path(query, entry, cache), fetch)
        timings["prefetch_s"] = round(perf_counter() - prefetch_start, 3)
        LOG.info("GCOV prefetch completed: %d planned, %d failed in %.3fs",
                 len(entries), len(failures), timings["prefetch_s"])
    else:
        failures = {}
        coverage, groups = discover(query)
        key, entries, reason = select(query, groups)
        if not entries:
            return {"status": "no_data", "reason": reason, "coverage": coverage}
        choices = [(key, entries)]
        alternate = select_alternate(query, groups, key)
        if alternate:
            choices.append(alternate)
    attempts = []
    measure_start = perf_counter()
    for key, entries in choices:
        state = {}
        def record(**fields):
            state.update(fields)
        options = {"sink": sink} if body.get("aoi") else {}
        if failures:
            options["read_failures"] = failures
        if experiment:
            options.update(aggregation=aggregate, aggregation_timings=aggregation_timings)
        followup = measure(query, entries, cache, out, record, **options)
        attempts.append({"series_key": key, "status": state["status"],
                         "usable_window_date_count": state["usable_window_date_count"]})
        if state["status"] != "no_data":
            break
    timings["measure_s"] = round(perf_counter() - measure_start, 3)
    LOG.info("GCOV measurement completed in %.3fs", timings["measure_s"])
    if followup:
        status, lens, paths = followup
        try:
            optical_options = {"native_aggregate": aggregate} if aggregate > 1 else {}
            evidence = enrich_lens(query, lens, paths, cache / "optical", out, **optical_options)
        except Exception as exc:
            LOG.warning("Optical evidence unavailable (%s)", type(exc).__name__)
            evidence = {"status": "unavailable", "reason": "optical_worker_error"}
        state.update(status=status, phase=None, lens=with_evidence(lens, evidence))
        attempts[-1]["status"] = status
    if body.get("aoi") and sink.get("report", {}).get("status") == "evaluated":
        from .plan_input import summarize_aoi

        state["aoi_summary"] = summarize_aoi(sink["cube"], sink["arrays"], body["aoi"],
                                             sink["report"]["radar"]["polarizations"])
    timings["total_s"] = round(perf_counter() - started, 3)
    if experiment:
        timings["aggregate_s"] = round(aggregation_timings["aggregate_s"], 3)
        timings["analysis_s"] = round(timings["total_s"] - timings["prefetch_s"] - timings["aggregate_s"], 3)
        state["experiment"] = {**experiment, "native_window_pixels": query.chip_pixels, "reads": reads}
        if state["status"] == "unavailable":
            # Preserve measured failure diagnostics in the benchmark result.
            state.update(status="no_data", reason="all_archive_reads_failed")
    return {**state, "coverage": coverage, "series_key": key, "series_attempts": attempts, "timings": timings}


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
        from .spectral import MINIMUM_SPECTRAL_DATES

        spectral = "spectral" in body["analyses"]
        source.update(max_dates=MINIMUM_SPECTRAL_DATES if spectral else 5, native_chip_pixels=256,
                      target_posting_m=160 if "transformer" in body["analyses"] else 40)
        if "series_key" in body:
            source["series_key"] = body["series_key"]
        request["analyses"] = body["analyses"]
        if "transformer" in body["analyses"]:
            request["transformer_checkpoint"] = MODEL
        return analyze(request, out, archive_read_workers=3 if spectral else 1)
    source.pop("start_date")
    source.pop("end_date")
    source.update(max_dates=5, native_chip_pixels=256, target_posting_m=40)
    cutoff = date.fromisoformat(body["baseline_end_date"])
    request["baseline"] = {"start_date": body["start_date"], "end_date": cutoff.isoformat()}
    request["assessment"] = {"start_date": (cutoff + timedelta(days=1)).isoformat(), "end_date": body["end_date"]}
    return {"characterization": process_characterization, "flood": process_flood,
            "fingerprint": process_fingerprint}[mode](request, out)


def live_layers(out, prefix, operation, lens=None, *, analysis_maps=None):
    """Render the available GCOV, analysis and InSAR overlays."""
    import numpy as np
    import rasterio
    from rasterio.transform import xy
    from .service_artifacts import LIVE_DISPLAY_EDGE

    layers = data_layers(out, prefix)
    if layers:
        return layers
    if operation not in {"explore", "insar-pair", "analysis"}:
        return layers
    analysis_maps = analysis_maps or {}
    lens = lens or {}
    evidence = lens.get("evidence") or {}
    diagnostics = {
        "flood_signature_flags": ("diagnostic mask", "Blues", (0, 1), {0: "absent", 1: "present"}),
        "agriculture_populations": ("diagnostic mask", "Blues", (0, 1), {0: "outside population", 1: "in population"}),
        "agriculture_step_magnitude_db": ("dB", "RdBu_r", (-8, 8), None),
    }
    for layer in (lens.get("radar") or {}).get("layers", []):
        if layer["role"] == "cross_pol_pattern":
            diagnostics["cross_pol_pattern"] = ("pattern code", "viridis", (0, 3), layer["legend"])
    optical_legend = (evidence.get("layer") or {}).get("legend")
    if optical_legend:
        diagnostics["optical_groups"] = ("optical group code", "viridis", (0, 4), optical_legend)
    for path in sorted(out.glob("*.tif")):
        if operation == "analysis" and path.stem not in analysis_maps:
            continue
        if path.stem in {"reference_group", "reference_groups"}:
            continue
        with rasterio.open(path) as source:
            x, _ = xy(source.transform, [0] * source.width, range(source.width))
            _, y = xy(source.transform, range(source.height), [0] * source.height)
            for band in range(1, source.count + 1):
                suffix = source.descriptions[band - 1] or str(band)
                name = f"display_{path.stem}_{suffix}"
                legend = None
                if path.stem in analysis_maps:
                    units, cmap, stretch = analysis_maps[path.stem]
                elif path.stem == "coherence":
                    units, cmap, stretch = "unitless", "cividis", (0, 1)
                elif path.stem == "wrapped_phase_rad":
                    units, cmap, stretch = "rad", "twilight", (-3.1415926536, 3.1415926536)
                elif path.stem == "cross_pol_decline_count" and lens.get("assessment_dates"):
                    units, cmap, stretch = "observations", "viridis", (0, len(lens["assessment_dates"]))
                elif "change" in path.stem or "delta" in path.stem:
                    units, cmap, stretch = "dB", "RdBu_r", (-8, 8)
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                    units, cmap, stretch = "dB", "gray", (-30, 0)
                elif path.stem in diagnostics:
                    units, cmap, stretch, legend = diagnostics[path.stem]
                else:
                    continue
                values = source.read(band)
                if operation == "analysis" and not np.isfinite(values).any():
                    continue
                try:
                    layer = render_layer(out, prefix, name, values, x, y, source.crs.to_epsg(),
                                         units=units, cmap=cmap, stretch=stretch, legend=legend,
                                         max_edge=LIVE_DISPLAY_EDGE)
                    if legend and path.stem != "optical_groups":
                        layer["colormap"] = "diagnostic_categories"
                    layers.append(layer)
                except UnrepresentableDisplayGrid:
                    continue
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
    render_start = perf_counter()
    analysis_maps = None
    if body["operation"] == "analysis" and (dates := raw.get("observations", {}).get("dates")):
        dashboard = analysis_dashboard(out, dates)
        raw["dashboard"] = dashboard
        analysis_maps = {**ANALYSIS_MAPS, "valid_date_count": ("count", "viridis", (0, len(dates)))}
        spectrum = dashboard["series"].get("spectral_series")
        if spectrum:
            periods = [row["period_days"] for row in spectrum]
            analysis_maps["candidate_period_days"] = ("days", "viridis", (min(periods), max(periods)))
    try:
        layers = live_layers(out, prefix, body["operation"], raw.get("lens"), analysis_maps=analysis_maps)
    except UnrepresentableDisplayGrid:
        layers = []
    visualizations = []
    if body["operation"] == "explore" and raw.get("observations"):
        from .service_artifacts import LIVE_DISPLAY_EDGE
        from .service_visualizations import gcov_tiff_frames, render_gcov_visualizations

        observations = raw["observations"]
        pair = raw.get("comparison") or {}
        requested_dates = body.get("compare_dates")
        target = requested_dates[-1] if requested_dates else observations[-1]["date"]
        comparison_dates = pair["dates"] if pair.get("status") == "available" else ()
        try:
            visualizations = render_gcov_visualizations(out, prefix, gcov_tiff_frames(out, observations),
                                                        target, comparison_dates, max_edge=LIVE_DISPLAY_EDGE)
        except UnrepresentableDisplayGrid:
            pass
    visualization_seconds = perf_counter() - render_start
    priority = [
        *(layer["png_url"].rsplit("/", 1)[-1] for layer in layers),
        *(view["png_url"].rsplit("/", 1)[-1] for view in visualizations),
        *PREVIEWS,
        *(f"{name}.csv" for name in ANALYSIS_TABLES),
    ]
    assets = publish_files(out, prefix, priority=priority)
    science_artifacts = {asset["href"]: asset for asset in raw.pop("artifacts", [])}
    previews = []
    for asset in assets:
        name = asset["name"]
        if name in PREVIEWS and name in science_artifacts:
            title, interpretation = PREVIEWS[name]
            previews.append({"href": asset["href"], "title": title, "interpretation": interpretation,
                             "legend": science_artifacts[name].get("legend")})
    raw = published_science(raw, {asset["name"]: asset["href"] for asset in assets})
    if raw.get("status") in {"failed", "unavailable"}:
        raise RuntimeError("The scientific source could not be read.")
    result = {"operation": body["operation"], "science_status": raw["status"], "revision": revision,
              "point": [body["longitude"], body["latitude"]], "isDemo": False if assets else None,
              "science": raw, "layers": published_layers(layers, assets, require_cog=False), "artifacts": assets,
              "visualizations": published_layers(visualizations, assets, require_cog=False), "previews": previews,
              "visualization_seconds": round(visualization_seconds, 3),
              "elapsed_seconds": round(perf_counter() - start, 3)}
    write_json(out / "manifest.json", result)
    LOG.info("job=%s operation=%s science_status=%s assets=%d seconds=%.3f",
             job_id, body["operation"], raw["status"], len(assets), result["elapsed_seconds"])
    return result
