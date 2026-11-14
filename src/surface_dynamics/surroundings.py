"""Compare local changes on fixed, baseline-matched radar support."""

import numpy as np

from .analysis import measured_db
from .flood_signatures import POLICY, match_controls, spatial_contrast
from .io import local_neighborhood


def matched_comparison(baseline, delta, focal, surrounding, *, maximum_candidates=16):
    controls, positives, matches = match_controls(baseline, focal, surrounding, maximum_candidates=maximum_candidates)
    return {
        "matches": matches,
        "channels": [spatial_contrast(channel, positives, controls) for channel in delta],
    }, positives, controls


def surrounding_comparison(cube, features, delta, location):
    """Choose local populations geometrically, then match only on baseline radar levels."""
    result = {
        "status": "insufficient_data", "reason": "point_outside_grid",
        "definition": "Change in a 60 m focal patch minus change in unique baseline-level matched cells 120–300 m away.",
        "focal_radius_m": 60, "comparison_annulus_m": [120, 300],
        "minimum_cells": 5, "minimum_supported_fraction": 0.5, "minimum_matched_fraction": 0.5,
        "independently_stable_controls": False,
    }
    if not location["inside_grid"]:
        return result
    common = features["maps"]["common_support"]
    neighborhood, focal, ring = local_neighborhood(cube, location, common)
    if focal is not None:
        result.update(focal_cells=neighborhood["focal_cells"], surrounding_cells=neighborhood["comparison_cells"],
                      focal_grid_cells=neighborhood["focal_grid_cells"],
                      surrounding_grid_cells=neighborhood["comparison_grid_cells"])
    if neighborhood["status"] != "evaluated":
        return {**result, "reason": neighborhood["reason"]}

    compared, positives, controls = matched_comparison(
        features["maps"]["baseline_db"], delta, focal, ring, maximum_candidates=128,
    )
    pols = features["polarizations"]
    x, y = cube["x"], cube["y"]
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    result.update(matches=compared["matches"], channels=dict(zip(pols, compared["channels"])),
                  spatial_block_size_m=[POLICY["spatial_block_pixels"] * dx, POLICY["spatial_block_pixels"] * dy])
    if compared["matches"]["matched_positive_pixels"] < 5 or compared["matches"]["matched_fraction"] < 0.5:
        return {**result, "reason": "insufficient_baseline_matches"}

    indices = [*features["before"], *features["after"]]
    channel_indices = [list(cube["polarizations"]).index(pol) for pol in pols]
    focal_means, control_means = [
        measured_db({key: cube[key][..., mask][indices][:, channel_indices] for key in ("power", "valid")}).mean(axis=-1)
        for mask in (positives, controls)
    ]
    result["series"] = [
        {
            "date": str(cube["dates"][index]),
            "focal_mean_db": dict(zip(pols, focal_values.tolist())),
            "control_mean_db": dict(zip(pols, control_values.tolist())),
        }
        for index, focal_values, control_values in zip(indices, focal_means, control_means)
    ]
    result.update(status="evaluated", reason=None)
    return result
