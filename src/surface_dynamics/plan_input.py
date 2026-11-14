"""Backend scene plans as exploration input: granule names in, catalog entries out.

A plan names the scenes to read; the archive URLs are built here from a fixed host and collection
table, never taken from the request, so a client cannot point the token at another location.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import re

LOG = logging.getLogger(__name__)
HOST = "https://nisar.asf.earthdatacloud.nasa.gov"
COLLECTION_PATHS = {
    "PROVISIONAL": "NISAR/NISAR_L2_GCOV_PROVISIONAL_V1",
    "VALIDATED": "NISAR/NISAR_L2_GCOV_V1",
    "BETA": "NISAR/NISAR_L2_GCOV_BETA_V1",
    "UR": "NISAR-UR/UR_L2_L_GCOV",
}
MAX_SCENES = 5
GRANULE = re.compile(
    r"NISAR_L2_(?P<config>PR|UR)_GCOV_(?P<cycle>\d{3})_(?P<track>\d{3})_(?P<direction>[AD])"
    r"_(?P<frame>\d{3})_(?P<code>\d{4})_(?P<mode>[A-Z]{4})_[A-Z]"
    r"_(?P<start>\d{8}T\d{6})_\d{8}T\d{6}_[A-Z]\d{5}_[A-Z]_[A-Z]_[A-Z]_\d{3}"
)


def _parse(granule_id, collection):
    match = GRANULE.fullmatch(granule_id) if isinstance(granule_id, str) else None
    if match is None:
        raise ValueError("Scene plans must name NISAR L2 GCOV granules.")
    if collection not in COLLECTION_PATHS or (match["config"] == "UR") != (collection == "UR"):
        raise ValueError("The scene collection must match the granule configuration.")
    return match


def series_key(granule_id):
    """Same series identity as the catalog grouping: scenes of one key share a grid."""
    match = GRANULE.fullmatch(granule_id)
    if match is None:
        raise ValueError("Scene plans must name NISAR L2 GCOV granules.")
    return "_".join(match[name] for name in ("track", "direction", "frame", "code", "mode"))


def entries_from_scenes(scenes):
    """Return (series key, catalog entries sorted by time) for one plan, or raise ValueError."""
    if not 1 <= len(scenes) <= MAX_SCENES:
        raise ValueError(f"A scene plan holds 1 to {MAX_SCENES} scenes.")
    entries, keys = {}, set()
    for scene in scenes:
        granule_id = scene["granule_id"]
        match = _parse(granule_id, scene["collection"])
        keys.add(series_key(granule_id))
        started = datetime.strptime(match["start"], "%Y%m%dT%H%M%S")
        day = started.date().isoformat()
        if day in entries:
            raise ValueError("A scene plan holds at most one scene per day.")
        url = f"{HOST}/{COLLECTION_PATHS[scene['collection']]}/{granule_id}/{granule_id}.h5"
        entries[day] = {"id": granule_id, "title": granule_id,
                        "time_start": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "links": [{"href": url}]}
    if len(keys) != 1:
        raise ValueError("A scene plan must belong to one series; mixed grids cannot be stacked.")
    return keys.pop(), [entries[day] for day in sorted(entries)]


def prefetch_chips(entries, chip_path, fetch, workers=None):
    """Read every scene's window at once; return {granule id: error} for the ones that failed."""
    failures = {}
    with ThreadPoolExecutor(max_workers=workers or max(1, len(entries))) as pool:
        futures = [(entry, pool.submit(fetch, entry, chip_path(entry))) for entry in entries]
        for entry, future in futures:
            try:
                future.result()
            except Exception as exc:
                LOG.warning("prefetch of %s failed (%s)", entry["id"], type(exc).__name__)
                failures[entry["id"]] = exc
    return failures


def _projected_area(geometry):
    """Shoelace area of the outer rings of a projected Polygon or MultiPolygon, in square metres."""
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    total = 0.0
    for polygon in polygons:
        ring = polygon[0]
        total += abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))) / 2
    return total


def summarize_aoi(cube, arrays, geometry, polarizations):
    """Count the cells of the lens window inside a polygon: measured, and departing the baseline."""
    # The gateway image has no numpy or rasterio and imports this module through the contracts.
    import numpy as np
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.warp import transform_geom

    x, y, epsg = np.asarray(cube["x"]), np.asarray(cube["y"]), int(cube["epsg"])
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    grid = from_origin(x[0] - dx / 2, y[0] + dy / 2, dx, dy)
    inside = geometry_mask([transform_geom("EPSG:4326", f"EPSG:{epsg}", geometry)],
                           out_shape=(len(y), len(x)), transform=grid, invert=True)
    area_m2 = _projected_area(transform_geom("EPSG:4326", f"EPSG:{epsg}", geometry))
    delta, half_width = arrays["target_change_db"], arrays["diagnostic_half_width_db"]
    measured = inside & np.isfinite(delta).all(axis=0)
    departing = measured & (np.abs(delta) > half_width).any(axis=0)
    count = int(measured.sum())
    covered = float(min(1.0, inside.sum() * abs(dx * dy) / area_m2)) if area_m2 else 0.0
    return {
        "cells_in_area": int(inside.sum()),
        "area_covered_fraction": round(covered, 4),
        "reason": None if inside.any() else "the area lies outside the analysed window",
        "cells_with_measurement": count,
        "cells_departing_from_baseline": int(departing.sum()),
        "departing_fraction": float(departing.sum() / count) if count else None,
        "median_target_minus_baseline_db": {
            pol: float(np.median(band[measured])) if count else None
            for pol, band in zip(polarizations, delta)
        },
        "definition": "Cells where the target departs from the observed baseline by more than "
                      "the diagnostic half-width in any polarization; descriptive, not a classification.",
    }
