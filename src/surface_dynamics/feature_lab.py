"""Measured Feature Lab cards on one fixed population and actual dates."""

from hashlib import sha256

import numpy as np
from scipy.ndimage import binary_dilation, label

from .analysis import elapsed_days, measured_db
from .io import point_location, validate_cube
from .stochastic import smooth_observations


VERSION = "1.0"
POLICY_ID = "feature_lab_measured_v1"
MEANING = {
    "HH": "Co-polarized return responds to structure, roughness, moisture and viewing geometry.",
    "HV": "Cross-polarized return responds to depolarizing structure, vegetation, roughness and noise.",
    "VV": "V-transmit co-polarized return responds to structure, moisture and viewing geometry.",
    "VH": "V-transmit cross-polarized return responds to depolarizing structure and noise.",
}


def empty_feature_lab(reason, status="insufficient_data"):
    return {
        "contract_version": VERSION, "policy_id": POLICY_ID, "status": status, "reason": reason,
        "dates": [], "baseline_dates": [], "requested_target_date": None,
        "excluded_unusable_dates": [],
        "grid": None, "point": None, "support": None, "features": [], "layers": [],
        "uncertainty": {
            "observation": {"status": "unavailable", "reason": "Sensor and speckle error are unestimated."},
            "spatial": "Quartiles describe the fixed common cell population.",
            "model": "GP intervals are conditional on fitted parameters and model assumptions.",
        },
        "phenomenon_class": None, "phenomenon_probability": None,
        "causal_attribution": None, "displacement_cm": None,
    }


def number(value):
    return float(value) if np.isfinite(value) else None


def feature_values(cube, db):
    definitions = [
        {"id": pol, "kind": "power", "units": "dB", "source_channels": [pol],
         "definition": "10 log10 of measured linear gamma-zero power.", "physical_meaning": MEANING[pol]}
        for pol in map(str, cube["polarizations"])
    ]
    values = [db[:, index] for index in range(db.shape[1])]
    pols = list(cube["polarizations"])
    for co, cross in (("HH", "HV"), ("VV", "VH")):
        if co in pols and cross in pols:
            definitions.append({
                "id": f"{cross}_over_{co}", "kind": "log_power_ratio", "units": "dB",
                "source_channels": [cross, co], "definition": f"{cross} dB minus {co} dB.",
                "physical_meaning": "Relative cross-polarized power. This ratio depends algebraically on its source channels.",
            })
            values.append(db[:, pols.index(cross)] - db[:, pols.index(co)])
    return definitions, np.stack(values, axis=1)


def directional_texture(image, common, dx, dy):
    if common.sum() < 64:
        return {"status": "insufficient_data", "reason": "fewer_than_64_common_cells", "bins": []}
    bins = []
    directions = ((0, 1, "east"), (-1, 0, "north"), (-1, 1, "northeast"), (-1, -1, "northwest"))
    height, width = image.shape
    for row_direction, col_direction, name in directions:
        for lag in (1, 2, 4, 8):
            if lag > min(height, width) // 2:
                continue
            row_offset, col_offset = lag * row_direction, lag * col_direction
            rows = slice(max(0, -row_offset), min(height, height - row_offset))
            cols = slice(max(0, -col_offset), min(width, width - col_offset))
            other_rows = slice(max(0, row_offset), min(height, height + row_offset))
            other_cols = slice(max(0, col_offset), min(width, width + col_offset))
            valid = common[rows, cols] & common[other_rows, other_cols]
            differences = image[other_rows, other_cols][valid] - image[rows, cols][valid]
            count = len(differences)
            distance = float(np.hypot(col_offset * dx, row_offset * dy))
            bins.append({
                "direction": name, "lag_m": distance, "pairs": count,
                "status": "available" if count >= 30 else "insufficient_data",
                "semivariance_db2": float(0.5 * np.mean(differences**2)) if count >= 30 else None,
                "median_absolute_gradient_db_per_m": float(np.median(abs(differences)) / distance) if count >= 30 else None,
            })
    return {
        "status": "available" if any(item["status"] == "available" for item in bins) else "insufficient_data",
        "reason": None if any(item["status"] == "available" for item in bins) else "fewer_than_30_pairs_per_bin",
        "definition": "Empirical directional differences over measured cell pairs. Gradients and trends can dominate.",
        "bins": bins,
    }


def patches(change, common, dx, dy):
    if common.sum() < 64:
        return {"status": "insufficient_data", "reason": "fewer_than_64_common_cells"}, None
    labels = np.where(common, 0.0, np.nan)
    clipped = binary_dilation(~common, structure=np.ones((3, 3), bool))
    clipped[[0, -1], :] = True
    clipped[:, [0, -1]] = True
    objects = []
    next_id = 1
    for sign, direction in ((-1, "decrease"), (1, "increase")):
        components, count = label(common & (sign * change >= 3), structure=np.ones((3, 3), int))
        sizes = np.bincount(components.ravel(), minlength=count + 1)
        for component in np.flatnonzero(sizes[1:] >= 4) + 1:
            mask = components == component
            object_id = sign * next_id
            next_id += 1
            labels[mask] = object_id
            outside = binary_dilation(mask, structure=np.ones((3, 3), bool)) & common & ~mask
            objects.append({
                "id": object_id, "direction": direction, "cells": int(mask.sum()),
                "area_m2": float(mask.sum() * dx * dy),
                "touches_chip_edge_or_nodata": bool((mask & clipped).any()),
                "inside_mean_change_db": float(change[mask].mean()),
                "outside_cells": int(outside.sum()),
                "boundary_contrast_db": float(change[mask].mean() - change[outside].mean()) if outside.sum() >= 4 else None,
            })
    areas = [obj["area_m2"] for obj in objects]
    return {
        "status": "available", "reason": None, "threshold_absolute_change_db": 3.0,
        "minimum_cells": 4, "connectivity": 8, "patch_count": len(objects),
        "area_quantiles_25_50_75_m2": np.quantile(areas, [.25, .5, .75]).tolist() if areas else None,
        "objects": sorted(objects, key=lambda item: -item["cells"])[:100],
        "objects_truncated": len(objects) > 100,
        "interpretation": "Footprints of signed radar changes at this posting. Edge and mask clipping are recorded.",
    }, labels


def change_timing(series, dates, before, after):
    if len(before) < 3 or not len(after) or not np.isfinite(series).all():
        return {"status": "insufficient_data", "reason": "three_baseline_dates_and_common_history_required"}
    baseline = float(np.median(series[before]))
    mad = float(1.4826 * np.median(abs(series[before] - baseline)))
    band = max(3.0, 3 * max(mad, 1.0))
    departures = [index for index in after if abs(series[index] - baseline) > band]
    bracket = None
    if departures:
        first = departures[0]
        earlier = [index for index in range(first) if abs(series[index] - baseline) <= band]
        bracket = [str(dates[earlier[-1]]) if earlier else None, str(dates[first])]
    return {
        "status": "available", "reason": None, "baseline_median_db": baseline,
        "baseline_scaled_mad_db": mad, "diagnostic_half_width_db": band,
        "first_departure_bracket": bracket, "departure_acquisitions": len(departures),
        "assessment_acquisitions": len(after), "exact_onset_inferred": False,
    }


def gp_cards(values, common, dates):
    empty = [{"status": "insufficient_data", "reason": "six_dates_and_eight_common_cells_required"}
             for _ in range(values.shape[1])]
    if len(dates) < 6 or common.sum() < 8:
        return empty
    days = elapsed_days(dates)
    try:
        fitted = smooth_observations(np.where(common[None, None], values, np.nan), days, draws=128, seed=31)
    except (np.linalg.LinAlgError, ValueError, FloatingPointError):
        return [{"status": "unavailable", "reason": "gp_numerical_failure"} for _ in empty]
    results = []
    for index, params in enumerate(fitted["parameters"]):
        observed = values[:, index, common].mean(1)
        if np.ptp(observed) < 1e-10:
            results.append({"status": "insufficient_data", "reason": "constant_regional_history"})
        elif not params.get("optimizer_success"):
            results.append({"status": "unavailable", "reason": "gp_fit_not_converged"})
        else:
            draws = fitted["region_draws_db"][:, index]
            results.append({
                "status": "available", "reason": None, "dates": list(map(str, dates)),
                "scope": "Fixed common-grid mean dB treated as one representative scalar process.",
                "parameters": params, "draw_count": 128,
                "mean_db": fitted["mean_db"][:, index, common].mean(1).tolist(),
                "quantiles_025_50_975_db": np.quantile(draws, [.025, .5, .975], axis=0).tolist(),
                "draws_db": draws.tolist(), "interpretation": fitted["interpretation"],
                "observed_temporal_mean_db": float(observed.mean()),
                "observed_temporal_sd_db": float(observed.std()),
            })
    return results


def build_feature_lab(cube, target_date, *, baseline_end=None, point=None):
    """Return cards, arrays, and a masked cube for comparisons on identical cells."""
    validate_cube(cube)
    dates = cube["dates"].astype(str)
    keep = dates <= target_date
    if not keep.any():
        report = empty_feature_lab("no_observations_on_or_before_target")
        report["requested_target_date"] = target_date
        return report, {}, None
    cube = {**cube, **{key: cube[key][keep] for key in ("power", "valid", "dates")}}
    dates = cube["dates"].astype(str)
    before = np.flatnonzero((dates < target_date) & (dates <= (baseline_end or target_date)))
    after = np.flatnonzero(dates == target_date)
    if baseline_end is not None:
        after = np.flatnonzero((dates > baseline_end) & (dates <= target_date))
    raw = measured_db(cube).astype("float64")
    common = np.isfinite(raw).all(axis=(0, 1))
    shared = {**cube, "valid": cube["valid"] & common[None, None]}
    definitions, values = feature_values(cube, raw)
    location = point_location(cube, point)
    row, col = location["row"], location["column"]
    point_common = location["inside_grid"] and bool(common[row, col])
    dx, dy = float(cube["x"][1] - cube["x"][0]), float(cube["y"][0] - cube["y"][1])
    report = empty_feature_lab(None)
    report.update(
        status="available" if common.any() else "insufficient_data",
        reason=None if common.any() else "no_fixed_common_support",
        dates=dates.tolist(), baseline_dates=dates[before].tolist(), requested_target_date=target_date,
        grid={"epsg": int(cube["epsg"]), "width": len(cube["x"]), "height": len(cube["y"]),
              "pixel_spacing_m": [dx, dy],
              "bounds_native": [float(cube["x"][0] - dx / 2), float(cube["y"][-1] - dy / 2),
                                float(cube["x"][-1] + dx / 2), float(cube["y"][0] + dy / 2)]},
        point={**location, "fixed_common_support": bool(point_common)},
        support={"scope": "All available named channels and used dates on the native grid.",
                 "common_cells": int(common.sum()), "grid_cells": int(common.size),
                 "common_mask_sha256": sha256(common.astype("uint8").tobytes()).hexdigest()},
    )
    models = gp_cards(values, common, dates)
    target = np.flatnonzero(dates == target_date)
    arrays = {"common_support": common.astype(float)}
    baselines = np.full(values.shape[1:], np.nan)
    changes = baselines.copy()
    patch_maps = baselines.copy()
    if len(before) >= 3 and common.any():
        baselines[:, common] = np.median(values[before][:, :, common], axis=0)
        if len(target):
            changes[:, common] = values[target[0]][:, common] - baselines[:, common]
    for index, definition in enumerate(definitions):
        channel = values[:, index]
        mean = channel[:, common].mean(1) if common.any() else np.full(len(dates), np.nan)
        quantiles = np.quantile(channel[:, common], [.25, .5, .75], axis=1).T if common.any() else np.full((len(dates), 3), np.nan)
        pixel = channel[:, row, col] if location["inside_grid"] else np.full(len(dates), np.nan)
        series = [
            {"date": str(day), "point_db": number(pixel[i]), "roi_mean_db": number(mean[i]),
             "roi_quantiles_25_50_75_db": [number(v) for v in quantiles[i]]}
            for i, day in enumerate(dates)
        ]
        if definition["kind"] == "log_power_ratio":
            for sample in series:
                sample["point_linear_ratio"] = 10**(sample["point_db"] / 10) if sample["point_db"] is not None else None
        spatial = {"status": "insufficient_data", "reason": "target_or_baseline_unavailable"}
        if len(target) and common.any():
            spatial = {"status": "available", "reason": None,
                       "directional_texture": directional_texture(channel[target[0]], common, dx, dy)}
            if len(before) >= 3:
                spatial["patches"], patch_map = patches(changes[index], common, dx, dy)
                if patch_map is not None:
                    patch_maps[index] = patch_map
        report["features"].append({
            **definition, "series": series,
            "point_timing": change_timing(pixel, dates, before, after) if point_common else {
                "status": "insufficient_data", "reason": "point_lacks_fixed_common_support",
            },
            "roi_timing": change_timing(mean, dates, before, after),
            "target_change_db": number(changes[index, row, col]) if point_common else None,
            "spatial": spatial, "gp": models[index],
        })
    if len(target):
        arrays["target_db"] = np.where(common[None], values[target[0]], np.nan)
    if len(before) >= 3 and common.any():
        arrays["baseline_db"] = baselines
        if len(target):
            arrays["change_db"] = changes
            if np.isfinite(patch_maps).any():
                arrays["patch_ids"] = patch_maps
    return report, arrays, shared


def write_feature_layers(output, report, arrays, cube, href_prefix=""):
    from .engine import write_cog

    layers = []
    names = [item["id"] for item in report["features"]]
    for key, values in arrays.items():
        categorical = key in {"common_support", "patch_ids"}
        units = {"common_support": "support flag", "patch_ids": "signed diagnostic patch id"}.get(key, "dB")
        filename = f"feature_lab_{key}.tif"
        write_cog(output / filename, values, cube["x"], cube["y"], cube["epsg"], units,
                  ["common_support"] if key == "common_support" else names, angular=categorical)
        layers.append({
            "role": key, "href": href_prefix + filename, "units": units,
            "bands": ["common_support"] if key == "common_support" else names,
            "resampling": "nearest" if categorical else "average",
            "sha256": sha256((output / filename).read_bytes()).hexdigest(),
        })
    return layers
