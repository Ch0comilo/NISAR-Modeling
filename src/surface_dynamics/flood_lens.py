"""Versioned observations and comparisons for the Explore flood lens."""

from datetime import date
from hashlib import file_digest
import numpy as np

from .analysis import measured_db
from .flood_signatures import POLICY, match_controls, spatial_contrast
from .io import point_location


VERSION = "1.0"
POLICY_ID = "explore_flood_observations_v1"
SIGNATURES = ("dual_darkening", "co_pol_brightening")
QUESTIONS = [
    "Did both radar channels darken relative to this pixel's observed baseline?",
    "Did a baseline-matched surrounding population change differently from the focal patch?",
    "Do independently dated optical snapshots show new water or water already present?",
]


def unavailable_evaluation(target_date, baseline_dates, reason):
    return {
        "contract_version": VERSION, "policy_id": POLICY_ID,
        "status": "insufficient_data", "reason": reason,
        "target_date": target_date, "baseline_dates": list(baseline_dates),
        "signature_policy": {
            "dual_darkening_max_change_db": {"co": -POLICY["co_pol_change_db"],
                                             "cross": -POLICY["cross_pol_darkening_db"]},
            "co_pol_brightening_min_change_db": POLICY["co_pol_change_db"],
            "interpretation": "Evidence states refer to measured signatures or the explicitly named optical claim.",
        },
        "grid": None, "point": None, "grid_signatures": None,
        "surrounding_comparison": {"status": "insufficient_data", "reason": reason},
        "water_comparison": {"status": "insufficient_data", "reason": "optical_evidence_unavailable"},
        "uncertainty": {
            "observation": {"status": "unavailable", "reason": "Sensor error and speckle uncertainty are unestimated."},
            "spatial_heterogeneity": None,
            "model": {"status": "unavailable", "reason": "Diagnostic thresholds have no fitted probability model."},
        },
        "confounders": [
            {"id": name, "status": "unavailable"} for name in (
                "existing_water", "rain_or_soil_moisture", "freeze_thaw", "wind",
                "incidence_and_track", "speckle_and_noise", "layover_or_shadow",
            )
        ],
        "questions": QUESTIONS,
        "phenomenon_class": None, "phenomenon_probability": None,
        "causal_attribution": None, "displacement_cm": None,
    }


def signature_masks(delta, support):
    return np.stack([
        support & (delta[0] <= -POLICY["co_pol_change_db"]) & (delta[1] <= -POLICY["cross_pol_darkening_db"]),
        support & (delta[0] >= POLICY["co_pol_change_db"]),
    ])


def matched_comparison(baseline, delta, focal, surrounding, *, maximum_candidates=16):
    controls, positives, matches = match_controls(baseline, focal, surrounding, maximum_candidates=maximum_candidates)
    return {
        "matches": matches,
        "channels": [spatial_contrast(channel, positives, controls) for channel in delta],
    }, positives, controls


def differential_darkening(channels):
    limits = [-POLICY["co_pol_change_db"], -POLICY["cross_pol_darkening_db"]]
    intervals = [item.get("spatial_resampling_95_interval_db") for item in channels]
    result = {
        "state": "insufficient_data", "reason": "too_few_spatial_blocks",
        "claim": "Focal-minus-control mean change is at most -3 dB co-pol and -1.5 dB cross-pol.",
    }
    if any(interval is None for interval in intervals):
        return result
    if all(interval[1] <= limit for interval, limit in zip(intervals, limits)):
        return {**result, "state": "supports", "reason": None}
    if any(interval[0] > limit for interval, limit in zip(intervals, limits)):
        return {**result, "state": "absent_on_sampled_support", "reason": None}
    return {**result, "reason": "contrast_intervals_overlap_signature_bounds"}


def surrounding_comparison(cube, features, delta, location):
    """Choose populations geometrically, then match only on pre-target radar levels."""
    result = {
        "status": "insufficient_data", "reason": "point_outside_grid",
        "definition": "Change in a 60 m focal patch minus change in unique baseline-level matched cells 120–300 m away.",
        "focal_radius_m": 60, "comparison_annulus_m": [120, 300],
        "minimum_cells": 5, "minimum_supported_fraction": 0.5, "minimum_matched_fraction": 0.5,
        "independently_stable_controls": False,
    }
    if not location["inside_grid"]:
        return result
    x, y = cube["x"], cube["y"]
    qx, qy = location["x"], location["y"]
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    extent = min(qx - x[0] + dx / 2, x[-1] + dx / 2 - qx,
                 qy - y[-1] + dy / 2, y[0] + dy / 2 - qy)
    if extent < 300:
        return {**result, "reason": "neighborhood_outside_grid"}
    distance = np.hypot(x[None, :] - qx, y[:, None] - qy)
    focal_grid = distance <= 60
    ring_grid = (distance >= 120) & (distance <= 300)
    common = features["maps"]["common_support"]
    focal, ring = focal_grid & common, ring_grid & common
    counts = [int(mask.sum()) for mask in (focal, ring)]
    totals = [int(mask.sum()) for mask in (focal_grid, ring_grid)]
    result.update(focal_cells=counts[0], surrounding_cells=counts[1],
                  focal_grid_cells=totals[0], surrounding_grid_cells=totals[1])
    if min(counts) < 5 or any(count / max(total, 1) < 0.5 for count, total in zip(counts, totals)):
        return {**result, "reason": "insufficient_common_neighborhood_support"}
    compared, positives, controls = matched_comparison(
        features["maps"]["baseline_db"], delta, focal, ring, maximum_candidates=128,
    )
    pols = features["polarizations"]
    result.update(matches=compared["matches"], channels=dict(zip(pols, compared["channels"])),
                  spatial_block_size_m=[POLICY["spatial_block_pixels"] * dx, POLICY["spatial_block_pixels"] * dy])
    if compared["matches"]["matched_positive_pixels"] < 5 or compared["matches"]["matched_fraction"] < 0.5:
        return {**result, "reason": "insufficient_baseline_matches"}
    indices = [*features["before"], *features["after"]]
    db = measured_db(cube)[indices][:, [list(cube["polarizations"]).index(pol) for pol in pols]]
    result["series"] = [
        {
            "date": str(cube["dates"][index]),
            "focal_mean_db": {pol: float(channel[positives].mean()) for pol, channel in zip(pols, values)},
            "control_mean_db": {pol: float(channel[controls].mean()) for pol, channel in zip(pols, values)},
        }
        for index, values in zip(indices, db)
    ]
    # The response answers how the two populations differed. Thresholds do not
    # establish independent quiet conditions in the surrounding population.
    result.update(status="evaluated", reason=None)
    result["differential_darkening"] = differential_darkening(compared["channels"])
    return result


def radar_evaluation(cube, features, delta, target_date, baseline_dates, point=None):
    evaluation = unavailable_evaluation(target_date, baseline_dates, None)
    support = features["maps"]["common_support"]
    masks = signature_masks(delta, support)
    pols = features["polarizations"]
    location = point_location(cube, point)
    evaluation.update(status="evaluated", reason=None, grid={
        "epsg": int(cube["epsg"]), "width": len(cube["x"]), "height": len(cube["y"]),
        "pixel_spacing_m": [float(cube["x"][1] - cube["x"][0]), float(cube["y"][0] - cube["y"][1])],
    })
    evaluation["grid_signatures"] = {
        name: {
            "state": "supports" if mask.any() else "absent_on_sampled_support",
            "signature_cells": int(mask.sum()), "supported_cells": int(support.sum()),
            "fraction": float(mask.sum() / support.sum()),
        }
        for name, mask in zip(SIGNATURES, masks)
    }
    point_result = {
        **location, "status": "insufficient_data", "reason": "point_outside_grid",
        "signature_states": {name: "insufficient_data" for name in SIGNATURES},
        "baseline_db": None, "baseline_scaled_mad_db": None, "change_db": None,
        "cross_minus_co_change_db": None, "observation_bracket": [baseline_dates[-1], target_date],
        "series": [],
    }
    row, col = location["row"], location["column"]
    if location["inside_grid"]:
        point_result["reason"] = "point_lacks_fixed_common_support"
        if support[row, col]:
            maps = features["maps"]
            channels = [list(cube["polarizations"]).index(pol) for pol in pols]
            indices = [*features["before"], *features["after"]]
            db = measured_db(cube)[indices][:, channels, row, col]
            point_result.update(
                status="evaluated", reason=None,
                signature_states={name: "supports" if mask[row, col] else "absent_on_sampled_support"
                                  for name, mask in zip(SIGNATURES, masks)},
                baseline_db={pol: float(value) for pol, value in zip(pols, maps["baseline_db"][:, row, col])},
                baseline_scaled_mad_db={pol: float(value) for pol, value in zip(pols, maps["baseline_mad_db"][:, row, col])},
                change_db={pol: float(value) for pol, value in zip(pols, delta[:, row, col])},
                cross_minus_co_change_db=float(maps["first_ratio_change_db"][row, col]),
                series=[{"date": str(cube["dates"][index]), "db": dict(zip(pols, values.tolist()))}
                        for index, values in zip(indices, db)],
            )
    evaluation["point"] = point_result
    evaluation["surrounding_comparison"] = surrounding_comparison(cube, features, delta, location)
    evaluation["uncertainty"]["spatial_heterogeneity"] = {
        "kind": "cell_distribution", "scope": "common_radar_grid", "units": "dB",
        "cells": int(support.sum()),
        "change_quantiles_25_50_75": {pol: np.quantile(values[support], [.25, .5, .75]).tolist()
                                    for pol, values in zip(pols, delta)},
    }
    return evaluation, np.where(support[None], masks.astype(float), np.nan)


def optical_comparison(cube, features, delta, groups, target_date, baseline_date, epochs, point=None):
    try:
        before = date.fromisoformat(epochs["pre"]["date"])
        after = date.fromisoformat(epochs["post"]["date"])
    except (KeyError, TypeError, ValueError):
        return {"status": "insufficient_data", "reason": "reference_sensing_dates_unavailable"}
    baseline, target = date.fromisoformat(baseline_date), date.fromisoformat(target_date)
    if not before < target <= after or max(abs((before - baseline).days), (after - target).days) > 4:
        return {"status": "insufficient_data", "reason": "reference_outside_temporal_support"}
    support = features["maps"]["common_support"]
    joint = (groups != 255) & support
    if not joint.any() or joint.sum() / support.sum() < 0.25:
        return {"status": "insufficient_data", "reason": "insufficient_joint_optical_coverage"}
    location = point_location(cube, point)
    code = int(groups[location["row"], location["column"]]) if location["inside_grid"] else 255
    if code == 1:
        state = "supports"
    elif code in (0, 2, 3):
        state = "contradicts"
    else:
        state = "insufficient_data"
    compared, _, _ = matched_comparison(features["maps"]["baseline_db"], delta, groups == 1, groups == 0)
    enough = min(int((groups == 1).sum()), int((groups == 0).sum())) >= POLICY["minimum_reference_group_pixels"]
    matched = compared["matches"]["matched_positive_pixels"]
    return {
        "status": "evaluated", "reason": None,
        "claim": "New strict open water between the two optical snapshots in the comparison cell containing the point.",
        "point_new_open_water_state": state, "point_group_code": code if code != 255 else None,
        "point_selection": location["selection"],
        "grid": {
            "epsg": int(cube["epsg"]), "width": len(cube["x"]), "height": len(cube["y"]),
            "pixel_spacing_m": [float(cube["x"][1] - cube["x"][0]), float(cube["y"][0] - cube["y"][1])],
        },
        "matched_new_water_comparison": {
            "status": "evaluated" if enough and matched >= POLICY["minimum_reference_group_pixels"] else "insufficient_data",
            "reason": None if enough and matched >= POLICY["minimum_reference_group_pixels"] else "insufficient_reference_groups_or_matches",
            "matches": compared["matches"],
            "channels": dict(zip(features["polarizations"], compared["channels"])),
        },
    }


def with_evidence(lens, evidence):
    """Publish an atomic optical-stage update without modifying a prior job snapshot."""
    result = {**lens, "evidence": evidence}
    if lens.get("id") != "flood_wetland" or "evaluation" not in lens:
        return result
    water = evidence.get("comparison", {
        "status": "pending" if evidence["status"] == "pending" else "insufficient_data",
        "reason": evidence.get("reason", "optical_evidence_unavailable"),
    })
    water = {**water, "epochs": evidence.get("epochs", {}),
             "groups": evidence.get("groups", {}), "source": evidence.get("source")}
    # Preserve native-resolution radar values. Optical comparisons have their
    # own aggregation, pixel support, dates and comparator.
    confounders = [
        {**item, "status": "observed", "evidence": water.get("point_group_code")}
        if item["id"] == "existing_water" and water.get("point_group_code") in (0, 1, 2, 3) else item
        for item in lens["evaluation"]["confounders"]
    ]
    result["evaluation"] = {**lens["evaluation"], "water_comparison": water, "confounders": confounders}
    return result


def write_signature_layer(output, arrays, cube, href_prefix=""):
    from .engine import write_cog

    if "flood_signature_flags" not in arrays:
        return []
    filename = "flood_signature_flags.tif"
    write_cog(output / filename, arrays["flood_signature_flags"], cube["x"], cube["y"], cube["epsg"],
              "diagnostic flag", list(SIGNATURES), angular=True)
    with (output / filename).open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    return [{
        "role": "flood_signature_flags", "href": href_prefix + filename,
        "units": "diagnostic flag", "bands": list(SIGNATURES),
        "legend": {0: "absent_on_sampled_support", 1: "supports"}, "resampling": "nearest",
        "sha256": digest,
    }]
