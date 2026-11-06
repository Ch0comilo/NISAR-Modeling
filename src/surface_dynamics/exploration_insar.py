"""Catalog inventory and wrapped GUNW observations for point exploration."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re

import numpy as np
import requests
from urllib3.exceptions import HTTPError, TimeoutError as RangeTimeout

from .catalog import COLLECTIONS, search
from .engine import write_cog
from .explore import ExploreRequest, _window, parse_request
from .insar import fetch_pair, wrapped_products
from .network import acquisition_dates, build_network


EXTENT_KM = 5
POINT_FIELDS = {"longitude", "latitude", "start_date", "end_date"}


@dataclass(frozen=True)
class PairRequest:
    point: ExploreRequest
    pair_id: str
    coherence_threshold: float


def parse_pair_request(body):
    required = POINT_FIELDS | {"pair_id"}
    if not isinstance(body, dict) or not required <= body.keys() <= required | {"coherence_threshold"}:
        raise ValueError("Expected point coordinates, start_date, end_date, pair_id and optional coherence_threshold.")
    point = parse_request({key: body[key] for key in POINT_FIELDS})
    identifier = body["pair_id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"NISAR_L2_[A-Za-z0-9_]{1,170}", identifier):
        raise ValueError("pair_id must be a GUNW identifier from the point inventory.")
    threshold = body.get("coherence_threshold", .35)
    if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
        raise ValueError("coherence_threshold must be a number between zero and one.")
    return PairRequest(point, identifier, float(threshold))


def discover_pairs(point):
    """List catalog pairs with both acquisitions inside the requested UTC window."""
    catalog = search(
        point.longitude, point.latitude, "GUNW", start=point.start_date + "T00:00:00Z",
        end=point.end_date + "T23:59:59.999Z", timeout=(3, 8),
    )
    eligible = {}
    for entry in catalog["entries"]:
        try:
            dates = acquisition_dates(entry["title"])
        except (KeyError, ValueError):
            continue
        if all(point.start_date <= day <= point.end_date for day in dates):
            eligible[entry["title"]] = entry
    pairs = []
    for group in build_network(eligible.values(), max_baseline_days=366):
        for edge in group["edges"]:
            pairs.append({
                "id": edge["id"], "series_key": group["series_key"],
                "reference_date": edge["reference_date"], "secondary_date": edge["secondary_date"],
                "temporal_baseline_days": edge["temporal_baseline_days"],
                "granule_id": eligible[edge["id"]].get("id"),
            })
    pairs.sort(key=lambda row: (row["secondary_date"], row["reference_date"], row["id"]), reverse=True)
    return {
        "contract_version": "1.0", "collection": COLLECTIONS["GUNW"],
        "point": {"longitude": point.longitude, "latitude": point.latitude},
        "date_window": [point.start_date, point.end_date],
        "catalog_queried_at_utc": catalog["queried_at_utc"], "catalog_hits": catalog["hits"],
        "coverage_status": "catalog_pairs" if pairs else "no_catalog_pairs",
        "pixel_support": "unchecked", "pairs": pairs,
    }, {row["id"]: eligible[row["id"]] for row in pairs}


def pair_cache_path(query, entry, cache):
    point = query.point
    key = [1, COLLECTIONS["GUNW"], entry.get("id"), entry["title"], point.longitude,
           point.latitude, EXTENT_KM, "wrapped_only"]
    return cache / "gunw" / (sha256(json.dumps(key).encode()).hexdigest() + ".npz")


def measure_pair(query, entry, path, out):
    """Read one bounded wrapped grid and publish coherence and phase independently."""
    point = query.point
    try:
        fetch_pair(entry, point.longitude, point.latitude, path, EXTENT_KM, wrapped_only=True)
    except (OSError, ValueError, KeyError, RuntimeError, requests.RequestException, HTTPError) as exc:
        if isinstance(exc, (requests.Timeout, RangeTimeout)):
            reason = "archive_timeout"
        elif str(exc) == "Query point outside GUNW quantitative grid":
            return {"status": "no_data", "reason": "outside_quantitative_grid"}
        elif "EARTHDATA_TOKEN" in str(exc) or str(exc).endswith(("request: 401", "request: 403")):
            reason = "archive_auth_unavailable"
        elif str(exc) == "Cached GUNW checksum mismatch":
            reason = "cache_integrity_error"
        else:
            reason = "archive_read_failed"
        return {"status": "unavailable", "reason": reason}
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    with np.load(path, allow_pickle=False) as pair:
        coherence, phase = wrapped_products(pair, query.coherence_threshold)
        grid = {"x": pair["wrapped_x"], "y": pair["wrapped_y"], "epsg": pair["wrapped_epsg"],
                "polarizations": ["coherence", "wrapped_phase_rad"]}
        window = _window(grid, np.stack((coherence, phase)), point)
    counts = window["valid_pixel_count_by_pol"]
    layers = []
    for values, key, units, angular in (
        (coherence, "coherence", "unitless", False),
        (phase, "wrapped_phase_rad", "rad", True),
    ):
        if not counts[key]:
            continue
        filename = key + ".tif"
        write_cog(out / filename, values, grid["x"], grid["y"], grid["epsg"], units, [key], angular=angular)
        layers.append({
            "role": key, "href": f"/v1/jobs/{out.name}/assets/{filename}", "units": units,
            "display_range": [-np.pi, np.pi] if angular else [0, 1],
            "resampling": "nearest" if angular else "continuous",
        })
    if not counts["coherence"]:
        status, reason = "no_data", "no_focused_coherence"
    elif not counts["wrapped_phase_rad"]:
        status, reason = "partial", "no_wrapped_phase_at_threshold"
    else:
        status, reason = "complete", None
    return {
        "status": status, "reason": reason, "grid": window["grid"], "layers": layers,
        "point": window["point_db"], "valid_pixels": counts, "polarization": meta["polarization"],
        "median_coherence": float(np.nanmedian(coherence)) if counts["coherence"] else None,
        "coherence_threshold": query.coherence_threshold, "corrections_applied": [],
        "phase_reference": "Angle of the native wrapped interferogram",
        "los_displacement_cm": None,
    }
