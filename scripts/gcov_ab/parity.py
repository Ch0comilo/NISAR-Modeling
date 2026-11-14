"""Compare archived native chips and independently check the 40 m numeric layers."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen
from xml.etree import ElementTree

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import transform


def matched_chips(h5py, pyfive):
    by_date = {}
    for control in sorted(h5py.rglob("*.npz")):
        candidate = next(iter(pyfive.rglob(control.name)), None)
        if candidate is None:
            raise ValueError(f"Missing pyfive chip for {control.name}")
        metadata = json.loads(control.with_suffix(".json").read_text())
        date = metadata["date"]
        with np.load(control, allow_pickle=False) as a, np.load(candidate, allow_pickle=False) as b:
            for key in ("epsg", "x", "y", "mask", "HHHH", "HVHV"):
                np.testing.assert_array_equal(a[key], b[key], err_msg=f"{date} {key}")
            power = np.stack([a["HHHH"], a["HVHV"]])
            valid = (a["mask"] > 0) & (a["mask"] < 255) & np.all(np.isfinite(power) & (power > 0), axis=0)
            by_date[date] = (power, valid, a["x"], a["y"], int(a["epsg"]))
    if len(by_date) != 5:
        raise ValueError(f"Expected five matched chips, found {len(by_date)}")
    return by_date


def reference(power, valid):
    height, width = valid.shape
    # Sum explicitly across the 16 offsets in float64 to avoid reusing the worker reduction.
    count = np.zeros((height // 4, width // 4), dtype="uint8")
    total = np.zeros((2, *count.shape), dtype="float64")
    for row in range(4):
        for column in range(4):
            support = valid[row::4, column::4]
            count += support
            total += np.where(support, power[:, row::4, column::4], 0)
    return np.where(count == 16, total / 16, np.nan), count


def channel_block(osm, power, coarse, x, y, epsg):
    tree = ElementTree.parse(osm)
    nodes = {node.get("id"): (float(node.get("lon")), float(node.get("lat"))) for node in tree.iter("node")}
    shape = coarse.shape[-2:]
    affine = from_bounds(x[0] - 5, y[-1] - 5, x[-1] + 5, y[0] + 5, shape[1], shape[0])
    intersections = np.zeros(shape, dtype=bool)
    names = set()
    for way in tree.iter("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        if tags.get("waterway") not in {"river", "stream"}:
            continue
        coordinates = [nodes[node.get("ref")] for node in way.findall("nd") if node.get("ref") in nodes]
        if len(coordinates) < 2:
            continue
        lon, lat = zip(*coordinates)
        line = {"type": "LineString", "coordinates": list(zip(*transform("EPSG:4326", f"EPSG:{epsg}", lon, lat)))}
        crossed = rasterize([line], out_shape=shape, transform=affine, all_touched=True).astype(bool)
        if crossed.any():
            intersections |= crossed
            names.add(tags.get("name") or "unnamed waterway")
    values = 10 * np.log10(power[0].reshape(len(y) // 4, 4, len(x) // 4, 4))
    contrasts = np.where(intersections & np.isfinite(coarse[0]),
                         values.max(axis=(1, 3)) - values.min(axis=(1, 3)), -np.inf)
    if not np.isfinite(contrasts).any():
        return None
    row, col = (int(i) for i in np.unravel_index(np.argmax(contrasts), contrasts.shape))
    return {"mapped_waterways": sorted(names), "block_row_col": [row, col],
            "hh_native_min_max_db": [float(values[row, :, col, :].min()), float(values[row, :, col, :].max())],
            "mean_power_db": (10 * np.log10(coarse[:, row, col])).tolist()}


def verify_posting(root, endpoint, runs, chips, osm=None):
    layers = [json.loads(path.read_text())["modal"]["result"] for path in runs]
    summaries = {}
    for day, (power, valid, x, y, epsg) in sorted(chips.items()):
        coarse, count = reference(power, valid)
        db = 10 * np.log10(coarse)
        bounds = (x[0] - 5, y[-1] - 5, x[-1] + 5, y[0] + 5)
        errors = []
        for result in layers:
            asset = next(a for a in result["artifacts"] if a["name"] == day + ".tif")
            path = root / (asset["href"].split("/")[3] + "-" + asset["name"])
            if not path.exists():
                with urlopen(endpoint + asset["href"], timeout=30) as response:
                    path.write_bytes(response.read())
            with MemoryFile(path.read_bytes()) as image, image.open() as raster:
                assert tuple(raster.bounds) == bounds and raster.res == (40, 40) and raster.crs.to_epsg() == epsg
                actual = raster.read()
            np.testing.assert_array_equal(np.isfinite(actual), np.isfinite(db))
            np.testing.assert_allclose(actual, db, atol=1e-5, rtol=0)
            errors.append(float(np.nanmax(np.abs(actual - db))))
        summaries[day] = {"native_valid": int(valid.sum()), "native_total": int(valid.size),
                          "coarse_valid": int((count == 16).sum()), "coarse_total": int(count.size),
                          "mixed_support_blocks": int(((count > 0) & (count < 16)).sum()),
                          "max_abs_db_error": max(errors), "epsg": epsg, "bounds": bounds}
    day = max(chips)
    power, valid, x, y, epsg = chips[day]
    coarse, count = reference(power, valid)
    lon, lat = layers[0]["point"]
    xx, yy = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    row, col = int(np.argmin(abs(y - yy[0]))), int(np.argmin(abs(x - xx[0])))
    mixed = np.argwhere((count > 0) & (count < 16))
    examples = {"target": day, "point": {"native_row_col": [row, col],
                "native_db": (10 * np.log10(power[:, row, col])).tolist() if valid[row, col] else None,
                "block_db": (10 * np.log10(coarse[:, row // 4, col // 4])).tolist()
                if count[row // 4, col // 4] == 16 else None}}
    if len(mixed):
        a, b = map(int, mixed[len(mixed) // 2])
        examples["mixed_support_edge"] = {"block_row_col": [a, b], "valid_native_cells": int(count[a, b]),
                                          "coarse_db": None}
    # Strongest within-block HH contrast identifies a feature edge for manual interpretation.
    blocks = power[0].reshape(len(y) // 4, 4, len(x) // 4, 4)
    contrast = np.where(count == 16, 10 * np.log10(blocks.max(axis=(1, 3)) / blocks.min(axis=(1, 3))), -np.inf)
    a, b = (int(i) for i in np.unravel_index(np.argmax(contrast), count.shape))
    examples["max_hh_contrast_block"] = {"block_row_col": [a, b], "range_db": float(contrast[a, b]),
                                         "mean_power_db": (10 * np.log10(coarse[:, a, b])).tolist()}
    if osm:
        examples["mapped_channel_block"] = channel_block(osm, power, coarse, x, y, epsg)
    return {"matched_native_chips": len(chips), "dates": summaries, "examples": examples}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--h5py", type=Path, required=True)
    parser.add_argument("--pyfive", type=Path, required=True)
    parser.add_argument("--run40", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--osm", type=Path, help="Optional local OpenStreetMap XML for a channel-crossing example.")
    parser.add_argument("--endpoint", default="https://ch0comilo--nisar-hazard-watch-ml-gcov-ab.us-east.modal.run")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = verify_posting(args.output.parent, args.endpoint, args.run40, matched_chips(args.h5py, args.pyfive),
                              osm=args.osm)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
