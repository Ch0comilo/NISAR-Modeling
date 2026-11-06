"""Measured point exploration backed by CMR and bounded GCOV windows."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from math import isfinite

import numpy as np
import requests
from rasterio.warp import transform
from urllib3.exceptions import HTTPError, TimeoutError as RangeTimeout

from .analysis import measured_db
from .catalog import COLLECTIONS, group_entries, search
from .engine import write_cog
from .exploration_lenses import (
    AGRICULTURE, FLOOD_WETLAND, GROUPS, LENSES, TEMPORAL_LENSES, VEGETATION_GROUPS, WILDFIRE, WILDFIRE_PATTERNS,
    agriculture_history, empty_lens, evaluate_lens, evaluate_vegetation_evidence, find_optical_reference,
    find_vegetation_reference, write_wildfire_layers,
)
from .io import cube_from_chips, fetch_chip


CHIP_PIXELS = 64
CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class ExploreRequest:
    longitude: float
    latitude: float
    start_date: str
    end_date: str
    series_key: str | None = None
    max_dates: int = 6
    compare_dates: tuple[str, str] | None = None
    lens: str | None = None
    optical_evidence: bool = False
    baseline_end_date: str | None = None


def _iso_day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Dates must be ISO UTC calendar days (YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Dates must be ISO UTC calendar days (YYYY-MM-DD).") from None
    if parsed.isoformat() != value:
        raise ValueError("Dates must be ISO UTC calendar days (YYYY-MM-DD).")
    return parsed


def parse_request(body):
    required = {"longitude", "latitude", "start_date", "end_date"}
    allowed = required | {"series_key", "max_dates", "compare_dates", "lens", "optical_evidence", "baseline_end_date"}
    if not isinstance(body, dict) or not required <= body.keys() <= allowed:
        raise ValueError("Expected point coordinates, start_date, end_date and optional series_key, max_dates, compare_dates, lens, optical_evidence, baseline_end_date.")
    for key, limit in (("longitude", 180), ("latitude", 90)):
        value = body[key]
        if type(value) not in (int, float) or not -limit <= value <= limit or not isfinite(value):
            raise ValueError(f"{key} is outside WGS84 bounds.")
    start, end = _iso_day(body["start_date"]), _iso_day(body["end_date"])
    if not 0 <= (end - start).days <= 366:
        raise ValueError("Select an increasing window of at most 366 days.")
    series_key = body.get("series_key")
    if "series_key" in body and (not isinstance(series_key, str) or not 1 <= len(series_key) <= 64):
        raise ValueError("series_key must be a catalog series identifier.")
    max_dates = body.get("max_dates", 6)
    if type(max_dates) is not int or not 1 <= max_dates <= 6:
        raise ValueError("max_dates must be an integer from 1 to 6.")
    compare_dates = body.get("compare_dates")
    if "compare_dates" in body:
        if not isinstance(compare_dates, list) or len(compare_dates) != 2 or max_dates < 2:
            raise ValueError("compare_dates requires two dates and max_dates of at least 2.")
        before, after = map(_iso_day, compare_dates)
        if not start <= before < after <= end:
            raise ValueError("compare_dates must be distinct, increasing and within the search window.")
        compare_dates = tuple(compare_dates)
    lens = body.get("lens")
    if "lens" in body and (not isinstance(lens, str) or lens not in LENSES):
        raise ValueError("Supported lenses are " + ", ".join(LENSES) + ".")
    optical = body.get("optical_evidence", False)
    if type(optical) is not bool or (optical and lens is None):
        raise ValueError("optical_evidence must be a Boolean and requires a lens when true.")
    if optical and lens == AGRICULTURE["id"]:
        raise ValueError("Agriculture currently returns measured radar and neighborhood comparisons.")
    baseline_end = body.get("baseline_end_date")
    if "baseline_end_date" in body:
        cutoff = _iso_day(baseline_end)
        if lens is None or not start <= cutoff < (after if compare_dates else end):
            raise ValueError("baseline_end_date requires a lens and must be within the window before the target.")
    return ExploreRequest(
        float(body["longitude"]), float(body["latitude"]), body["start_date"], body["end_date"],
        series_key, max_dates, compare_dates, lens, optical, baseline_end,
    )


def _day(entry):
    return entry["time_start"][:10]


def _supports_frequency_a(key):
    # GCOV filenames encode the two frequency polarization modes in order.
    return not key.rsplit("_", 1)[-1].startswith("NA")


def discover(query):
    """Return small public coverage metadata and private granules grouped by geometry."""
    result = search(
        query.longitude, query.latitude, start=query.start_date + "T00:00:00Z",
        end=query.end_date + "T23:59:59.999Z", timeout=(3, 8),
    )
    groups = {}
    for key, entries in group_entries(result["entries"]).items():
        daily = {}
        for entry in entries:
            daily[_day(entry)] = entry
        groups[key] = list(daily.values())
    coverage = {
        "contract_version": CONTRACT_VERSION,
        "point": {"longitude": query.longitude, "latitude": query.latitude},
        "date_window": [query.start_date, query.end_date],
        "collection": COLLECTIONS["GCOV"],
        "catalog_queried_at_utc": result["queried_at_utc"],
        "catalog_hits": result["hits"],
        "coverage_status": "catalog_match" if result["hits"] else "no_catalog_match",
        "pixel_support": "unchecked",
        "series": [
            {"key": key, "dates": [_day(entry) for entry in entries],
             "frequency_a_supported": _supports_frequency_a(key)}
            for key, entries in sorted(groups.items())
        ],
    }
    return coverage, groups


def select(query, groups):
    """Pick one compatible series and at most six dates, retaining the requested pair."""
    if not groups:
        return None, [], "no_catalog_match"
    candidates = groups if query.series_key is None else {
        key: entries for key, entries in groups.items() if key == query.series_key
    }
    if not candidates:
        return None, [], "series_unavailable"
    supported = {key: entries for key, entries in candidates.items() if _supports_frequency_a(key)}
    if not supported:
        return None, [], "unsupported_frequency"
    if query.compare_dates:
        supported = {
            key: entries for key, entries in supported.items()
            if set(query.compare_dates) <= {_day(entry) for entry in entries}
        }
        if not supported:
            return None, [], "pair_unavailable"
    # Prefer usable comparisons to a single acquisition, then favor dual-pol series.
    def rank(key):
        rows = supported[key]
        cutoff = query.baseline_end_date
        covers_windows = bool(cutoff and _day(rows[-1]) > cutoff and sum(_day(entry) <= cutoff for entry in rows) >= 3)
        return (covers_windows, len(rows) >= 2, key.endswith(("DHDH", "DVDV")), len(rows), key)

    key = max(supported, key=rank)
    entries = supported[key]
    selected = [entry for entry in entries if _day(entry) in (query.compare_dates or ())]
    priority = list(reversed(entries))
    if query.baseline_end_date:
        if not selected:
            selected.append(entries[-1])
        prior = [entry for entry in priority if _day(entry) <= query.baseline_end_date]
        if query.lens in TEMPORAL_LENSES:
            target = query.compare_dates[1] if query.compare_dates else _day(entries[-1])
            post = [entry for entry in priority if query.baseline_end_date < _day(entry) <= target]
            needed = max(0, 3 - sum(_day(entry) <= query.baseline_end_date for entry in selected))
            remaining_prior = [entry for entry in prior if entry not in selected]
            priority = remaining_prior[:needed] + post + remaining_prior[needed:] + priority
        else:
            priority = prior + priority
    for entry in priority:
        if len(selected) == query.max_dates:
            break
        if entry not in selected:
            selected.append(entry)
    return key, sorted(selected, key=_day), None


def select_alternate(query, groups, primary_key):
    """Use the normal ranking on another eligible series for a coverage retry."""
    if query.series_key is not None or primary_key is None:
        return None
    candidates = {key: rows for key, rows in groups.items() if key != primary_key}
    if query.lens:
        if query.max_dates < 4:
            return None
        eligible = {}
        for key, rows in candidates.items():
            if not key.rsplit("_", 1)[-1].startswith(("DH", "DV")):
                continue
            _, selected, _ = select(query, {key: rows})
            if not selected:
                continue
            target = query.compare_dates[1] if query.compare_dates else _day(selected[-1])
            cutoff = query.baseline_end_date or target
            if target > (query.baseline_end_date or query.start_date) and sum(
                _day(row) < target and _day(row) <= cutoff for row in selected
            ) >= 3:
                eligible[key] = rows
        candidates = eligible
    key, entries, _ = select(query, candidates)
    return (key, entries) if entries else None


def _chip_path(query, entry, cache):
    identity = [1, COLLECTIONS["GCOV"], entry.get("id"), entry["title"], "A",
                query.longitude, query.latitude, CHIP_PIXELS]
    key = sha256(json.dumps(identity).encode()).hexdigest()
    return cache / (key + ".npz")


def chips_cached(query, entries, cache):
    for entry in entries:
        path = _chip_path(query, entry, cache)
        if not path.is_file() or not path.with_suffix(".json").is_file():
            return False
    return bool(entries)


def _window(cube, db, query):
    x, y, epsg = cube["x"], cube["y"], int(cube["epsg"])
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    left, bottom, right, top = x[0] - dx / 2, y[-1] - dy / 2, x[-1] + dx / 2, y[0] + dy / 2
    qx, qy = transform("EPSG:4326", f"EPSG:{epsg}", [query.longitude], [query.latitude])
    inside = left <= qx[0] < right and bottom < qy[0] <= top
    values = db[:, np.argmin(abs(y - qy[0])), np.argmin(abs(x - qx[0]))] if inside else [np.nan] * len(db)
    return {
        "grid": {"epsg": epsg, "bounds_native": list(map(float, (left, bottom, right, top))),
                 "pixel_spacing_m": [dx, dy], "width": len(x), "height": len(y)},
        "point_db": {str(pol): float(value) if np.isfinite(value) else None
                     for pol, value in zip(cube["polarizations"], values)},
        "valid_pixel_count_by_pol": {
            str(pol): int(np.isfinite(band).sum()) for pol, band in zip(cube["polarizations"], db)
        },
    }


def _layer(out, filename, cube, values, role, dates):
    pols = cube["polarizations"].astype(str).tolist()
    write_cog(out / filename, values, cube["x"], cube["y"], cube["epsg"], "dB", pols)
    return {
        "role": role, "dates": dates, "polarizations": pols, "units": "dB",
        "href": f"/v1/jobs/{out.name}/assets/{filename}",
    }


def _read_error(exc):
    if isinstance(exc, (requests.Timeout, RangeTimeout)):
        return "archive_timeout"
    if isinstance(exc, FileNotFoundError):
        return "cached_chip_unavailable"
    if str(exc) == "Point outside quantitative grid":
        return "outside_quantitative_grid"
    if isinstance(exc, KeyError):
        return "unsupported_or_missing_dataset"
    if "EARTHDATA_TOKEN" in str(exc) or str(exc).endswith(("request: 401", "request: 403")):
        return "archive_auth_unavailable"
    if str(exc) == "Cached chip checksum mismatch":
        return "cache_integrity_error"
    return "archive_read_failed"


def measure(query, entries, cache, out, publish, *, cache_only=False):
    """Fetch the newest preview first and publish progress after every acquisition."""
    paths, rows = {}, {}
    dates = [_day(entry) for entry in entries]
    pair = query.compare_dates or tuple(dates[-2:])
    comparison = None
    # Read the two comparison dates first, then fill in older history.
    order = sorted(entries, key=lambda entry: (_day(entry) in pair, _day(entry)), reverse=True)
    for entry in order:
        day = _day(entry)
        row = {"date": day, "sensing_start_utc": entry["time_start"],
               "granule_id": entry.get("id"), "granule_title": entry["title"]}
        path = _chip_path(query, entry, cache)
        try:
            metadata = fetch_chip(entry, query.longitude, query.latitude, path,
                                  pixels=CHIP_PIXELS, cache_only=cache_only)
        except (OSError, ValueError, KeyError, RuntimeError, requests.RequestException, HTTPError) as exc:
            reason = _read_error(exc)
            row.update(status="outside_grid" if reason == "outside_quantitative_grid" else "unavailable",
                       reason=reason)
        else:
            cube = cube_from_chips([path], aggregate=1)
            db = measured_db(cube)[0]
            row.update(_window(cube, db, query))
            row["quality"] = metadata["flags"]
            if np.isfinite(db).any():
                paths[day] = path
                row["status"] = "observed" if any(v is not None for v in row["point_db"].values()) else "point_masked"
                row["layer"] = _layer(out, day + ".tif", cube, db, "observed_gamma0", [day])
            else:
                row.update(status="no_valid_pixels", reason="no_finite_positive_focused_power")
        rows[day] = row
        if comparison is None and len(pair) == 2 and all(day in rows for day in pair):
            comparison = _compare(query, pair, paths, out)
        publish(
            observations=[rows[day] for day in dates if day in rows],
            comparison=comparison,
        )
    observed = sum(row["status"] == "observed" for row in rows.values())
    failures = sum(row["status"] == "unavailable" for row in rows.values())
    usable = sum("layer" in row for row in rows.values())
    if not usable:
        status = "unavailable" if failures else "no_data"
    elif failures or observed != len(dates) or (comparison and comparison["status"] != "available"):
        status = "partial"
    else:
        status = "complete"
    lens = _radar_lens(query, dates, paths, out) if query.lens else None
    optical_pending = query.optical_evidence and lens["status"] == "evaluated"
    if optical_pending:
        lens["evidence"] = {"status": "pending"}
    publish(status="running" if optical_pending else status,
            phase="optical_evidence" if optical_pending else None,
            observed_date_count=observed, usable_window_date_count=usable, lens=lens,
            comparison=comparison or {"status": "unavailable", "reason": "fewer_than_two_selected_dates"})
    if optical_pending:
        assessed = lens.get("assessment_dates", [lens["target_date"]])
        return status, lens, [paths[day] for day in [*lens["baseline_dates"], *assessed]]
    return None


def enrich_lens(query, lens, paths, cache, out):
    """Evaluate optional optical evidence on a coarser grid, keeping native radar intact."""
    spacing = min(lens["radar"]["grid"]["pixel_spacing_m"])
    factor = 1
    while spacing * factor < 30 and factor < CHIP_PIXELS // 2:
        factor *= 2
    if spacing * factor < 30:
        return {"status": "unavailable", "reason": "optical_grid_unavailable"}
    cube = cube_from_chips(paths, aggregate=factor)
    target = lens["target_date"]
    radar_report, arrays = evaluate_lens(cube, target, query.lens, baseline_end=query.baseline_end_date)
    if not arrays:
        return {"status": "unavailable", "reason": "no_common_support_at_optical_scale"}
    is_water = query.lens == FLOOD_WETLAND["id"]
    lookup = find_optical_reference if is_water else find_vegetation_reference
    reference, evidence = lookup(
        cube, query.longitude, query.latitude, lens["observation_bracket"],
        np.isfinite(arrays["target_change_db"][0]), cache,
    )
    if reference is None:
        return evidence
    if is_water:
        report, arrays = evaluate_lens(cube, target, query.lens, baseline_end=query.baseline_end_date, reference=reference)
        evidence.update(report["evidence"])
        groups, legend, role = arrays["optical_groups"], GROUPS, "optical_water_class_comparison"
        if evidence["status"] != "optical_comparison_available":
            evidence["reason"] = "insufficient_joint_optical_coverage"
            evidence.pop("groups", None)
    else:
        evidence, groups = evaluate_vegetation_evidence(cube, radar_report, arrays, reference, evidence)
        legend, role = VEGETATION_GROUPS, "optical_vegetation_disturbance_groups"
    group_cube = {**cube, "polarizations": ["optical_group"]}
    window_values = groups if groups is not None else np.full(arrays["target_change_db"].shape[-2:], np.nan)
    window = _window(group_cube, window_values[None], query)
    evidence["grid"] = window["grid"]
    evidence["radar_aggregation_pixels_per_side"] = factor
    if evidence["status"] != "optical_comparison_available":
        return evidence
    point_code = window["point_db"]["optical_group"]
    evidence["point_group"] = None if point_code is None else legend[int(point_code)]
    filename = "optical_groups.tif"
    write_cog(out / filename, groups, cube["x"], cube["y"], cube["epsg"],
              "optical class code", ["optical_group"], angular=True)
    evidence["layer"] = {
        "href": f"/v1/jobs/{out.name}/assets/{filename}", "role": role,
        "units": "optical class code", "legend": legend,
    }
    return evidence


def _radar_lens(query, dates, paths, out):
    target = query.compare_dates[1] if query.compare_dates else dates[-1]
    prior = [day for day in dates if day < target and day <= (query.baseline_end_date or target)]
    before = [day for day in prior if day in paths]
    assessed = [target]
    if query.lens in TEMPORAL_LENSES and query.baseline_end_date:
        assessed = [day for day in dates if query.baseline_end_date < day <= target]
        if target not in assessed:
            assessed = [target]
    exclusions = [day for day in [*prior, *assessed] if day not in paths]
    if target not in paths:
        report = empty_lens(target, before, "target_unavailable", query.lens)
    else:
        try:
            cube = cube_from_chips([paths[day] for day in [*before, *assessed] if day in paths], aggregate=1)
        except ValueError as exc:
            if str(exc) not in {
                "No common measured polarization",
                "Incompatible grids; reproject explicitly before combining",
            }:
                raise
            report = empty_lens(target, before, "incompatible_measured_support", query.lens)
        else:
            report, arrays = evaluate_lens(cube, target, query.lens, baseline_end=query.baseline_end_date)
            if report["status"] == "evaluated":
                cube["polarizations"] = np.asarray(report["radar"]["polarizations"])
                delta = arrays["target_change_db"]
                window = _window(cube, delta, query)
                bounds = _window(cube, arrays["diagnostic_half_width_db"], query)["point_db"]
                values = window["point_db"]
                if any(value is None for value in values.values()):
                    state = "insufficient_data"
                else:
                    state = ("observed_departure" if any(abs(values[pol]) > bounds[pol] for pol in values)
                             else "within_observed_baseline")
                report["point"] = {
                    "status": state, "target_minus_baseline_db": values,
                    "diagnostic_half_width_db": bounds,
                }
                report["radar"]["grid"] = window["grid"]
                report["radar"]["layer"] = _layer(
                    out, "baseline_change.tif", cube, delta, "target_minus_observed_baseline_db", [target],
                )
                if query.lens == AGRICULTURE["id"]:
                    report["agriculture"] = agriculture_history(
                        cube, report, np.isfinite(delta).all(axis=0), query.longitude, query.latitude,
                    )
                if query.lens == WILDFIRE["id"]:
                    report["radar"]["layers"] = write_wildfire_layers(
                        out, arrays, cube, href_prefix=f"/v1/jobs/{out.name}/assets/",
                    )
                    pattern_grid = {**cube, "polarizations": ["pattern"]}
                    count = _window(pattern_grid, arrays["cross_pol_decline_count"][None], query)["point_db"]["pattern"]
                    pattern = None
                    if "cross_pol_pattern" in arrays:
                        code = _window(pattern_grid, arrays["cross_pol_pattern"][None], query)["point_db"]["pattern"]
                        pattern = None if code is None else WILDFIRE_PATTERNS[int(code)]
                    report["point"]["cross_pol_history"] = {
                        "decline_dates": None if count is None else int(count),
                        "assessed_dates": len(report["assessment_dates"]), "pattern": pattern,
                    }
    report["excluded_unusable_dates"] = exclusions
    if query.baseline_end_date is not None:
        report["requested_baseline_end_date"] = query.baseline_end_date
    return report


def _compare(query, pair, paths, out):
    if not all(day in paths for day in pair):
        return {"status": "unavailable", "dates": list(pair), "reason": "selected_date_unusable"}
    try:
        cube = cube_from_chips([paths[day] for day in pair], aggregate=1)
    except ValueError as exc:
        if str(exc) not in {
            "No common measured polarization",
            "Incompatible grids; reproject explicitly before combining",
        }:
            raise
        return {"status": "unavailable", "dates": list(pair), "reason": "incompatible_measured_support"}
    before, after = measured_db(cube)
    delta = after - before
    if not np.isfinite(delta).any():
        return {"status": "unavailable", "dates": list(pair), "reason": "no_common_valid_pixels"}
    window = _window(cube, delta, query)
    point_changes = [f"{pol} {value:+.2f} dB" for pol, value in window["point_db"].items() if value is not None]
    if point_changes:
        explanation = f"From {pair[0]} to {pair[1]}, the clicked pixel changed by {', '.join(point_changes)}."
    else:
        explanation = "The window contains comparable measurements. The clicked pixel lacks common support."
    return {
        "status": "available", "dates": list(pair), "definition": "later minus earlier in dB on common valid pixels",
        **window, "explanation": explanation,
        "layer": _layer(out, "change.tif", cube, delta, "signed_gamma0_change", list(pair)),
    }
