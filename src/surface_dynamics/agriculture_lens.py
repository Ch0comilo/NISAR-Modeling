"""Measured focal contrasts and sampled synchrony, with no management labels."""

from hashlib import sha256

import numpy as np

from .analysis import elapsed_days, measured_db
from .flood_signatures import POLICY, match_controls, spatial_contrast
from .io import local_neighborhood, point_location


VERSION = "1.0"
POLICY_ID = "explore_agriculture_observations_v1"
MINIMUM_STEP_DB = 1.0
SYNCHRONY_FRACTION = 0.6


def unavailable_evaluation(target_date, baseline_dates, reason):
    return {
        "contract_version": VERSION, "policy_id": POLICY_ID,
        "status": "insufficient_data", "reason": reason,
        "target_date": target_date, "baseline_dates": list(baseline_dates), "assessment_dates": [],
        "grid": None, "point": None, "support": None, "channels": [],
        "policy": {
            "focal_radius_m": 60, "comparison_annulus_m": [120, 300],
            "minimum_cells": 5, "minimum_common_fraction": 0.5, "minimum_matched_fraction": 0.5,
            "baseline_match_tolerance_db": 2.0, "maximum_control_candidates": 128,
            "material_contrast_db": MINIMUM_STEP_DB, "cell_step_threshold_db": MINIMUM_STEP_DB,
            "synchrony_fraction": SYNCHRONY_FRACTION, "spatial_block_pixels": 8,
            "minimum_blocks_per_group": 4, "bootstrap_draws": 400,
            "minimum_assessment_intervals_for_synchrony": 2, "step_tie_break": "earliest_observed_interval",
        },
        "neighborhood": None, "matches": None, "series": [], "target_contrasts": {}, "steps": {},
        "uncertainty": {
            "observation": {"status": "unavailable", "reason": "Sensor and speckle uncertainty are unestimated."},
            "spatial": None,
            "model": {"status": "unavailable", "reason": "No crop or management model is fitted."},
        },
        "independent_evidence": {
            "status": "insufficient_data", "reason": "dated_parcel_or_management_reference_not_supplied",
            "needed": ["Dated harvest, tillage or irrigation records for the focal area.",
                       "Parcel boundaries, crop context and dated optical vegetation observations."],
        },
        "confounders": [{"id": name, "status": "unavailable"} for name in (
            "rain_or_soil_moisture", "irrigation", "freeze_thaw", "mixed_parcels",
            "incidence_and_track", "speckle_and_noise", "layover_or_shadow",
        )],
        "questions": [
            "Did the focal patch change differently from baseline-matched surrounding cells?",
            "Which observed interval contains the strongest focal-minus-control step?",
            "Did focal cells change together within that sampled interval?",
        ],
        "phenomenon_class": None, "phenomenon_probability": None,
        "causal_attribution": None, "displacement_cm": None,
    }


def contrast_state(interval):
    if interval is None:
        return {"state": "insufficient_data", "reason": "spatial_interval_unavailable"}
    lower, upper = interval
    if lower >= MINIMUM_STEP_DB or upper <= -MINIMUM_STEP_DB:
        return {"state": "supports", "reason": None}
    if -MINIMUM_STEP_DB < lower and upper < MINIMUM_STEP_DB:
        return {"state": "absent_on_sampled_support", "reason": None}
    return {"state": "insufficient_data", "reason": "interval_overlaps_material_contrast_bound"}


def bootstrap_contrasts(values, positive, control):
    """Resample whole spatial block histories so interval choices stay coupled."""
    size = POLICY["spatial_block_pixels"]
    sums, counts = [], []
    for row in range(0, positive.shape[0], size):
        for col in range(0, positive.shape[1], size):
            p = positive[row:row + size, col:col + size]
            c = control[row:row + size, col:col + size]
            if p.any() or c.any():
                sub = values[:, :, row:row + size, col:col + size]
                sums.append(np.stack([sub[:, :, p].sum(-1), sub[:, :, c].sum(-1)]))
                counts.append([int(p.sum()), int(c.sum())])
    counts = np.asarray(counts)
    metadata = {"positive_blocks": int((counts[:, 0] > 0).sum()),
                "control_blocks": int((counts[:, 1] > 0).sum()), "valid_draws": 0}
    if min(metadata["positive_blocks"], metadata["control_blocks"]) < POLICY["minimum_blocks_per_group"]:
        return None, {**metadata, "status": "insufficient_data", "reason": "too_few_spatial_blocks"}
    rng = np.random.default_rng(POLICY["bootstrap_seed"])
    indices = rng.integers(0, len(counts), size=(POLICY["bootstrap_draws"], len(counts)))
    totals = np.asarray(sums)[indices].sum(1)
    sizes = counts[indices].sum(1)
    valid = (sizes[:, 0] > 0) & (sizes[:, 1] > 0)
    draws = totals[valid, 0] / sizes[valid, 0, None, None] - totals[valid, 1] / sizes[valid, 1, None, None]
    return draws, {**metadata, "status": "available", "reason": None, "valid_draws": int(valid.sum())}


def synchrony(values, positive, assessment_steps, selected_step, focal_step):
    increments = np.diff(values, axis=0)
    chosen = np.argmax(abs(increments[assessment_steps]), axis=0)
    largest = np.take_along_axis(increments[assessment_steps], chosen[None], axis=0)[0]
    selected = assessment_steps[chosen] == selected_step
    active = abs(largest) >= MINIMUM_STEP_DB
    agrees = np.sign(increments[selected_step]) == np.sign(focal_step)
    synchronized = positive & selected & active & agrees & (abs(focal_step) >= MINIMUM_STEP_DB)
    result = {
        "state": "insufficient_data", "reason": "too_few_spatial_blocks",
        "focal_cells": int(positive.sum()), "synchronous_cells": int(synchronized.sum()),
        "fraction": float(synchronized.sum() / positive.sum()),
        "spatial_95_interval": None,
        "conditioning": "Largest focal-mean assessment bracket held fixed. Contrast-step selection sensitivity is reported separately.",
    }
    size = POLICY["spatial_block_pixels"]
    blocks = []
    for row in range(0, positive.shape[0], size):
        for col in range(0, positive.shape[1], size):
            cells = positive[row:row + size, col:col + size]
            if cells.any():
                blocks.append([int(synchronized[row:row + size, col:col + size].sum()), int(cells.sum())])
    result["blocks"] = len(blocks)
    if len(assessment_steps) < 2:
        return {**result, "reason": "fewer_than_two_assessment_intervals"}
    if len(blocks) < POLICY["minimum_blocks_per_group"]:
        return result
    blocks = np.asarray(blocks)
    rng = np.random.default_rng(POLICY["bootstrap_seed"])
    sampled = blocks[rng.integers(0, len(blocks), size=(POLICY["bootstrap_draws"], len(blocks)))].sum(1)
    interval = np.quantile(sampled[:, 0] / sampled[:, 1], [.025, .975]).tolist()
    result["spatial_95_interval"] = interval
    if interval[0] >= SYNCHRONY_FRACTION:
        result.update(state="supports", reason=None)
    elif interval[1] < SYNCHRONY_FRACTION:
        result.update(state="absent_on_sampled_support", reason=None)
    else:
        result["reason"] = "interval_overlaps_synchrony_bound"
    return result


def step_summary(values, dates, baseline_count, positive, control, draws):
    contrast = values[:, positive].mean(1) - values[:, control].mean(1)
    increments = np.diff(contrast)
    focal_increments = np.diff(values[:, positive].mean(1))
    assessment = np.arange(baseline_count - 1, len(dates) - 1)
    selected = int(assessment[np.argmax(abs(increments[assessment]))])
    focal_selected = int(assessment[np.argmax(abs(focal_increments[assessment]))])
    bootstrap_steps = np.diff(draws, axis=1) if draws is not None else None
    choices = np.argmax(abs(bootstrap_steps[:, assessment]), axis=1) if draws is not None else None
    material_draws = np.max(abs(bootstrap_steps[:, assessment]), axis=1) >= MINIMUM_STEP_DB if draws is not None else None
    selection_draws = int(material_draws.sum()) if material_draws is not None else 0
    days = elapsed_days(dates)
    intervals = []
    for i, increment in enumerate(increments):
        bounds = np.quantile(bootstrap_steps[:, i], [.025, .975]).tolist() if draws is not None else None
        intervals.append({
            "code": i + 1, "dates": list(map(str, dates[i:i + 2])),
            "interval_days": float(days[i + 1] - days[i]),
            "ends_in_assessment": i >= baseline_count - 1,
            "focal_minus_control_step_db": float(increment),
            "focal_mean_step_db": float(focal_increments[i]),
            "spatial_95_interval_db": bounds, **contrast_state(bounds),
            "bootstrap_largest_assessment_fraction": float((assessment[choices[material_draws]] == i).mean())
            if selection_draws and i in assessment else None,
        })
    return {
        "intervals": intervals,
        "largest_observed_interval_code": int(np.argmax(abs(increments))) + 1 if max(abs(increments)) >= MINIMUM_STEP_DB else None,
        "largest_assessment_interval_code": selected + 1 if abs(increments[selected]) >= MINIMUM_STEP_DB else None,
        "bootstrap_material_step_draws": selection_draws,
        "synchrony_interval_code": focal_selected + 1 if abs(focal_increments[focal_selected]) >= MINIMUM_STEP_DB else None,
        "exact_event_date_estimated": False,
        "synchrony": synchrony(values, positive, assessment, focal_selected, focal_increments[focal_selected]),
    }


def evaluate_agriculture(cube, features, target_date, point=None):
    before, after = features["before"], features["after"]
    indices = [*before, *after]
    dates = cube["dates"][indices].astype(str)
    pols = features["polarizations"]
    common = features["maps"]["common_support"]
    location = point_location(cube, point)
    report = unavailable_evaluation(target_date, cube["dates"][before].astype(str), None)
    report.update(assessment_dates=cube["dates"][after].astype(str).tolist(),
                  point={**location, "fixed_common_support": bool(location["inside_grid"] and common[location["row"], location["column"]])},
                  grid={"epsg": int(cube["epsg"]), "width": len(cube["x"]), "height": len(cube["y"]),
                        "pixel_spacing_m": [float(cube["x"][1] - cube["x"][0]), float(cube["y"][0] - cube["y"][1])]},
                  support={"dates": dates.tolist(), "common_cells": int(common.sum()),
                           "common_mask_sha256": sha256(common.astype("uint8").tobytes()).hexdigest()})
    neighborhood, focal, ring = local_neighborhood(cube, location, common)
    report["neighborhood"] = neighborhood
    if neighborhood["status"] != "evaluated":
        report["reason"] = neighborhood["reason"]
        return report, {}
    controls, positives, matches = match_controls(features["maps"]["baseline_db"], focal, ring, maximum_candidates=128)
    report["matches"] = matches
    arrays = {"agriculture_populations": np.where(common[None], np.stack([focal, positives, controls]).astype(float), np.nan)}
    if matches["matched_positive_pixels"] < 5 or matches["matched_fraction"] < 0.5:
        report["reason"] = "insufficient_baseline_matches"
        return report, arrays
    channels = [list(cube["polarizations"]).index(pol) for pol in pols]
    measured = measured_db(cube)[indices][:, channels].astype(float)
    values = np.concatenate([measured, (measured[:, 1] - measured[:, 0])[:, None]], axis=1)
    names = [*pols, f"{pols[1]}_over_{pols[0]}"]
    report["channels"] = names
    baseline = np.median(values[:len(before)], axis=0)
    delta = values[-1] - baseline
    draws, metadata = bootstrap_contrasts(values, positives, controls)
    dx, dy = report["grid"]["pixel_spacing_m"]
    report["uncertainty"]["spatial"] = {
        **metadata, "block_size_m": [POLICY["spatial_block_pixels"] * dx, POLICY["spatial_block_pixels"] * dy],
        "scope": "Conditional resampling of fixed spatial blocks with complete channel histories.",
        "limitations": "Block independence, sensor errors, reference errors and selection-adjusted intervals are unestimated.",
    }
    focal_mean, control_mean = values[:, :, positives].mean(-1), values[:, :, controls].mean(-1)
    contrast = focal_mean - control_mean
    contrast_baseline = np.median(contrast[:len(before)], axis=0)
    report["series"] = [
        {"date": str(day), "channels": {
            name: {"focal_mean_db": float(focal_mean[i, j]), "control_mean_db": float(control_mean[i, j]),
                   "focal_minus_control_db": float(contrast[i, j]),
                   "contrast_shift_from_baseline_db": float(contrast[i, j] - contrast_baseline[j])}
            for j, name in enumerate(names)}}
        for i, day in enumerate(dates)
    ]
    for index, name in enumerate(names):
        compared = spatial_contrast(delta[index], positives, controls)
        report["target_contrasts"][name] = {
            **compared, **contrast_state(compared.get("spatial_resampling_95_interval_db")),
            "definition": "Mean cellwise target-minus-baseline change in matched focal cells minus matched controls.",
        }
        report["steps"][name] = step_summary(values[:, index], dates, len(before), positives, controls,
                                            draws[:, :, index] if draws is not None else None)
    assessment_steps = np.arange(len(before) - 1, len(dates) - 1)
    increments = np.diff(values, axis=0)[assessment_steps]
    choice = np.argmax(abs(increments), axis=0)
    peak = np.take_along_axis(increments, choice[None], axis=0)[0]
    codes = np.where(abs(peak) >= MINIMUM_STEP_DB, assessment_steps[choice] + 1, 0)
    arrays["agriculture_step_interval"] = np.where(common[None], codes.astype(float), np.nan)
    arrays["agriculture_step_magnitude_db"] = np.where(common[None], peak, np.nan)
    report.update(status="evaluated", reason=None)
    return report, arrays


def write_agriculture_layers(output, arrays, cube, evaluation, href_prefix=""):
    from .engine import write_cog

    layers = []
    for key in ("agriculture_populations", "agriculture_step_interval", "agriculture_step_magnitude_db"):
        if key not in arrays:
            continue
        categorical = key != "agriculture_step_magnitude_db"
        units = {"agriculture_populations": "population flag", "agriculture_step_interval": "observed interval code"}.get(key, "dB")
        bands = ["focal_supported", "matched_focal", "matched_control"] if key == "agriculture_populations" else evaluation["channels"]
        filename = key + ".tif"
        write_cog(output / filename, arrays[key], cube["x"], cube["y"], cube["epsg"], units, bands, angular=categorical)
        layers.append({
            "role": key, "href": href_prefix + filename, "units": units, "bands": bands,
            "resampling": "nearest" if categorical else "average",
            "sha256": sha256((output / filename).read_bytes()).hexdigest(),
        })
    return layers
