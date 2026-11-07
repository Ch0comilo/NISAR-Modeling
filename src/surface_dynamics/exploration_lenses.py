"""Read the same radar measurements through an explicit evidence lens."""

from datetime import date, timedelta
from hashlib import file_digest
import json
from pathlib import Path
from time import monotonic

import numpy as np
import requests

from . import disturbance, optical_water
from .analysis import measured_db
from .agriculture_lens import evaluate_agriculture, unavailable_evaluation as unavailable_agriculture_evaluation
from .flood_signatures import POLICY, reference_groups, spatial_contrast
from .flood_lens import optical_comparison, radar_evaluation, unavailable_evaluation, with_evidence, write_signature_layer
from .io import load_cube, local_neighborhood, point_location
from .surface_features import DEFAULT_POLICY, fingerprint


FLOOD_WETLAND = {
    "id": "flood_wetland",
    "name": "Flood and wetland dynamics",
    "question": "Did the radar response change in a way that could be consistent with changing surface water?",
    "expected_signals": [
        "New smooth open water can darken both polarizations.",
        "Standing water among emergent stems can brighten co-polarized returns.",
    ],
    "alternatives": ["Existing water", "Wind-roughened water", "Soil moisture", "Crops", "Terrain shadow"],
    "independent_reference": "Date-matched optical open water, gauges and terrain connectivity",
    "evaluation_contract_version": "1.0",
    "evaluation_schema": "flood-lens-evaluation.schema.json",
}
VEGETATION_DISTURBANCE = {
    "id": "vegetation_disturbance",
    "name": "Vegetation disturbance",
    "question": "Did the scattering response shift in cells with an independent optical record of vegetation loss?",
    "expected_signals": [
        "Canopy loss can reduce cross-polarized scattering, with co-polarized changes depending on soil and debris.",
        "Compare matched cells at the same acquisition dates to check whether the response is specific to the mapped loss group.",
    ],
    "alternatives": ["Phenology", "Harvest or clearing", "Soil moisture", "Storm damage", "Viewing and terrain effects"],
    "independent_reference": "Recent OPERA DIST-ALERT-HLS and baseline-matched optically non-disturbed cells",
}
WILDFIRE = {
    "id": "wildfire",
    "name": "Wildfire",
    "question": "Did cross-polarized radar scattering decline after the baseline, and did it remain low on later acquisitions?",
    "expected_signals": [
        "Canopy disturbance can reduce cross-polarized scattering. The direction and size can vary by vegetation, moisture and geometry.",
        "Check repeat measurements and compare optical loss cells with matched comparison cells.",
    ],
    "alternatives": ["Harvest or clearing", "Seasonal vegetation change", "Moisture", "Storm damage", "Terrain shadow"],
    "independent_reference": "Recent OPERA DIST-ALERT-HLS and separately sourced fire-event context when available",
}
AGRICULTURE = {
    "id": "agriculture",
    "name": "Agriculture",
    "question": "Did the selected patch change with its surroundings, or did its radar history diverge?",
    "expected_signals": [
        "Crop growth and canopy structure can change cross-polarized scattering over several acquisitions.",
        "Harvest, tillage or wetting can produce an abrupt shift. Compare neighboring patches on the same dates.",
    ],
    "alternatives": ["Soil moisture", "Irrigation", "Tillage", "Mixed fields", "Viewing geometry"],
    "independent_reference": "Parcel or crop context and dated optical greenness where available",
    "evaluation_contract_version": "1.0",
    "evaluation_schema": "agriculture-lens-evaluation.schema.json",
}
LENSES = {lens["id"]: lens for lens in (FLOOD_WETLAND, VEGETATION_DISTURBANCE, WILDFIRE, AGRICULTURE)}
TEMPORAL_LENSES = {WILDFIRE["id"], AGRICULTURE["id"]}
GROUPS = {
    0: "stable_nonwater", 1: "new_open_water", 2: "stable_open_water",
    3: "open_water_loss", 4: "mixed_or_partial",
}
VEGETATION_GROUPS = {
    0: "optically_non_disturbed", 1: "confirmed_loss_in_window", 2: "provisional_loss_in_window",
    3: "confirmed_loss_outside_window", 4: "mixed_or_other_supported",
}
WILDFIRE_PATTERNS = {
    0: "no_sampled_decline",
    1: "decline_on_other_sampled_dates",
    2: "decline_on_last_two_assessments",
    3: "return_within_band_after_decline",
}
CO_DARKENING = POLICY["co_pol_change_db"]
CROSS_DARKENING = POLICY["cross_pol_darkening_db"]
OPTICAL_MAX_OFFSET_DAYS = 4
OPTICAL_MIN_JOINT_FRACTION = .25
OPTICAL_MAX_CANDIDATES_PER_EPOCH = 3


def empty_lens(target_date, baseline_dates, reason, lens_id=FLOOD_WETLAND["id"]):
    report = {
        "id": lens_id, "version": "1.0", "status": "insufficient_data",
        "reason": reason, "target_date": target_date, "baseline_dates": list(baseline_dates),
        "radar": None, "evidence": {"status": "unavailable"}, "point": None,
    }
    if lens_id == FLOOD_WETLAND["id"]:
        report["evaluation"] = unavailable_evaluation(target_date, baseline_dates, reason)
    if lens_id == AGRICULTURE["id"]:
        report["evaluation"] = unavailable_agriculture_evaluation(target_date, baseline_dates, reason)
    return report


def evaluate_flood_wetland(cube, target_date, reference=None, *, point=None, reference_epochs=None):
    return evaluate_lens(cube, target_date, FLOOD_WETLAND["id"], reference=reference,
                         point=point, reference_epochs=reference_epochs)


def evaluate_lens(cube, target_date, lens_id, *, baseline_end=None, reference=None, point=None, reference_epochs=None):
    """Return the lens report and its arrays on one measured, compatible grid."""
    dates = cube["dates"].astype(str)
    prior = dates[(dates < target_date) & (dates <= (baseline_end or target_date))].tolist()
    report = empty_lens(target_date, prior, None, lens_id)
    if target_date not in dates:
        return empty_lens(target_date, prior, "target_unavailable", lens_id), {}
    if len(prior) < DEFAULT_POLICY["minimum_dates"]:
        return empty_lens(target_date, prior, "fewer_than_three_prior_dates", lens_id), {}
    polarizations = set(cube["polarizations"])
    if not ({"HH", "HV"} <= polarizations or {"VV", "VH"} <= polarizations):
        return empty_lens(target_date, prior, "paired_polarizations_unavailable", lens_id), {}

    fire = lens_id == WILDFIRE["id"]
    temporal = lens_id in TEMPORAL_LENSES
    assessment = dates[(dates > prior[-1]) & (dates <= target_date)] if temporal else [target_date]
    features = fingerprint(cube, {"start_date": prior[0], "end_date": prior[-1]},
                           {"start_date": assessment[0], "end_date": target_date})
    maps = features["maps"]
    support = maps["common_support"]
    count = int(support.sum())
    if count == 0:
        return empty_lens(target_date, prior, "no_fixed_common_support", lens_id), {}
    pols = features["polarizations"]
    if temporal:
        pols_in_cube = list(cube["polarizations"])
        db = measured_db(cube)[:, [pols_in_cube.index(pol) for pol in pols]]
        changes = np.where(support, db[features["after"]] - maps["baseline_db"], np.nan)
        delta = changes[-1]
    else:
        delta = np.where(support, maps["first_change_db"], np.nan)
    departure = maps["assessment_departure_mask"][-1]
    water = lens_id == FLOOD_WETLAND["id"]
    if water:
        darkening = support & (delta[0] <= -CO_DARKENING) & (delta[1] <= -CROSS_DARKENING)
        brightening = support & (delta[0] >= CO_DARKENING)

    def summarize(mask):
        cells = int(mask.sum())
        summary = {
            "cells": cells,
            "median_target_minus_baseline_db": {
                pol: float(np.median(band[mask])) if cells else None for pol, band in zip(pols, delta)
            },
            "mad_departure_cells": int((mask & departure).sum()),
        }
        if water:
            summary.update(dual_darkening_cells=int((mask & darkening).sum()),
                           co_pol_brightening_cells=int((mask & brightening).sum()))
        return summary

    report.update(
        status="evaluated",
        observation_bracket=[prior[-1], target_date],
        radar={
            "polarizations": pols, "posting_m": features["posting_m"],
            "grid_cells": int(support.size), "common_cells": count,
            "comparator": "Target minus median of the prior measured dates",
            "diagnostic_half_width_db": "max(3, 3 × max(1.4826 × MAD, 1))",
            **summarize(support),
        },
    )
    if water:
        report["radar"].update(
            dual_darkening_max_delta_db={pols[0]: -CO_DARKENING, pols[1]: -CROSS_DARKENING},
            co_pol_brightening_min_delta_db=CO_DARKENING,
        )
    arrays = {"target_change_db": delta, "diagnostic_half_width_db": maps["diagnostic_half_width_db"]}
    if water:
        report["evaluation"], arrays["flood_signature_flags"] = radar_evaluation(
            cube, features, delta, target_date, prior, point,
        )
    if lens_id == AGRICULTURE["id"]:
        report["evaluation"], agriculture_arrays = evaluate_agriculture(cube, features, target_date, point)
        arrays.update(agriculture_arrays)
    if temporal:
        report["assessment_dates"] = assessment.tolist()
    if fire:
        report["fire_context"] = {"status": "unavailable"}
        report["radar"]["cross_pol_history"], temporal = wildfire_pattern(
            changes[:, 1], maps["diagnostic_half_width_db"][1], report["assessment_dates"], pols[1],
        )
        arrays.update(temporal)
        arrays["_cross_pol_assessment_db"] = changes[:, 1]
    if water and reference is not None:
        groups = reference_groups(reference)
        if groups.shape != support.shape:
            raise ValueError("The optical reference must be aligned to the measured radar grid.")
        if any(key in reference and not np.array_equal(reference[key], cube[key]) for key in ("x", "y", "epsg")):
            raise ValueError("The optical reference must be aligned to the measured radar grid.")
        groups = np.where(support, groups, 255)
        joint = int((groups != 255).sum())
        report["evidence"] = {
            "status": ("unavailable" if joint == 0 else
                       "limited_support" if joint / count < OPTICAL_MIN_JOINT_FRACTION else "optical_comparison_available"),
            "source": "OPERA DSWx-HLS optical surface water",
            "joint_usable_cells": joint, "radar_denominator_cells": count,
            "joint_usable_fraction": joint / count,
            "groups": {name: summarize(groups == code) for code, name in GROUPS.items()},
            "resolved_groups": [GROUPS[i] for i in (0, 1, 2, 3)],
            "interpretation": "Groups compare optical water classes at two snapshots. Mixed cells are descriptive.",
        }
        arrays["optical_groups"] = np.where(groups == 255, np.nan, groups)
        report["evidence"]["epochs"] = reference_epochs or {}
        report["evidence"]["comparison"] = optical_comparison(
            cube, features, delta, groups, target_date, prior[-1], report["evidence"]["epochs"], point,
        )
        report = with_evidence(report, report["evidence"])
    return report, arrays


def agriculture_history(cube, report, common, longitude, latitude):
    """Compare fixed geometric neighborhoods using measured dB on identical dates."""
    location = point_location(cube, (longitude, latitude))
    history, focal, ring = local_neighborhood(cube, location, common)
    if history["status"] != "evaluated":
        return history
    selected = [focal, ring]

    pols = report["radar"]["polarizations"]
    channels = [list(cube["polarizations"]).index(pol) for pol in pols]
    days = report["baseline_dates"] + report["assessment_dates"]
    dates = cube["dates"].astype(str).tolist()
    db = measured_db(cube)[[dates.index(day) for day in days]][:, channels]
    focal_median, ring_median = [np.median(db[:, :, mask], axis=-1) for mask in selected]
    difference = focal_median - ring_median
    prior = np.median(difference[:len(report["baseline_dates"])], axis=0)
    shift = difference - prior
    history.update(
        status="evaluated", comparison="Local annulus on the same dates and fixed measured support",
        series=[
            {"date": day, "polarization": pol, "focal_median_db": float(focal_median[i, j]),
             "nearby_median_db": float(ring_median[i, j]), "focal_minus_nearby_db": float(difference[i, j]),
             "contrast_shift_from_baseline_db": float(shift[i, j])}
            for i, day in enumerate(days) for j, pol in enumerate(pols)
        ],
        largest_contrast_steps=[
            {"polarization": pol, "dates": days[k:k+2], "change_db": float(difference[k+1, j] - difference[k, j])}
            for j, pol in enumerate(pols) for k in [int(np.argmax(np.abs(np.diff(difference[:, j]))))]
        ],
    )
    return history


def wildfire_pattern(cross_change, bound, dates, pol):
    """Report a directional radar pattern at sampled dates on fixed support."""
    common = np.isfinite(cross_change).all(axis=0) & np.isfinite(bound)
    below = common & (cross_change < -bound)
    count = below.sum(axis=0)
    report = {
        "polarization": pol, "status": "evaluated" if len(dates) >= 2 else "insufficient_assessment_dates",
        "rule": "Cross-polarized change below the negative median/MAD diagnostic bound",
        "samples": [{"date": day, "decline_cells": int(mask.sum()),
                     "median_change_db": float(np.median(change[common]))}
                    for day, mask, change in zip(dates, below, cross_change)],
    }
    arrays = {"cross_pol_decline_count": np.where(common, count, np.nan)}
    if len(dates) >= 2:
        persistent = below[-2] & below[-1]
        returned = below[:-1].any(axis=0) & (np.abs(cross_change[-1]) <= bound)
        pattern = np.select([persistent, returned, count > 0], [2, 3, 1], default=0)
        arrays["cross_pol_pattern"] = np.where(common, pattern, np.nan)
        report["decline_on_last_two_cells"] = int(persistent.sum())
        report["return_within_band_cells"] = int(returned.sum())
        report["last_two_assessment_dates"] = dates[-2:]
    return report, arrays


def write_wildfire_layers(output, arrays, cube, *, href_prefix=""):
    from .engine import write_cog

    layers = []
    for key, units in (("cross_pol_decline_count", "observations"), ("cross_pol_pattern", "pattern code")):
        if key not in arrays:
            continue
        filename = f"{key}.tif"
        write_cog(output / filename, arrays[key], cube["x"], cube["y"], cube["epsg"], units, [key], angular=True)
        layer = {"role": key, "href": href_prefix + filename, "units": units, "resampling": "nearest"}
        if key == "cross_pol_pattern":
            layer["legend"] = WILDFIRE_PATTERNS
        layers.append(layer)
    return layers


def find_optical_reference(cube, longitude, latitude, radar_dates, common, cache):
    """Choose two independent optical epochs using timing and coverage alone."""
    timeout = (3, 8)
    deadline = monotonic() + 50
    target = date.fromisoformat(radar_dates[1])
    reference, epochs = {}, {}
    evidence = {
        "status": "unavailable", "source": "OPERA DSWx-HLS optical surface water",
        "epochs": epochs, "max_offset_days": OPTICAL_MAX_OFFSET_DAYS,
        "min_joint_usable_fraction": OPTICAL_MIN_JOINT_FRACTION,
    }
    for phase, day in zip(("pre", "post"), radar_dates):
        anchor = date.fromisoformat(day)

        def eligible(sensing_day):
            observed = date.fromisoformat(sensing_day)
            same_side = observed < target if phase == "pre" else observed >= target
            return same_side and abs((observed - anchor).days) <= OPTICAL_MAX_OFFSET_DAYS

        if monotonic() >= deadline:
            return None, {**evidence, "reason": "optical_deadline_elapsed"}
        try:
            catalog = optical_water.discover(longitude, latitude, day, OPTICAL_MAX_OFFSET_DAYS, timeout=timeout)
            catalog["entries"] = [entry for entry in catalog["entries"] if eligible(entry["time_start"][:10])]
        except (requests.RequestException, ValueError, KeyError):
            return None, {**evidence, "reason": "optical_catalog_unavailable"}
        if not catalog["entries"]:
            epochs[phase] = {"radar_date": day, "status": "no_candidate"}
            return None, {**evidence, "reason": "no_optical_candidate_in_window"}
        aligned, selection = optical_water.select_reference(
            catalog, cube, day, cache, max_candidates=OPTICAL_MAX_CANDIDATES_PER_EPOCH,
            min_coverage=POLICY["reference_valid_coverage_min"], support=common,
            accept_sensing_day=eligible, timeout=timeout, deadline=deadline,
        )
        if aligned is None:
            epochs[phase] = {"radar_date": day, "status": "unavailable"}
            if monotonic() >= deadline:
                reason = "optical_deadline_elapsed"
            elif selection["candidate_errors"]:
                reason = "optical_assets_unavailable"
            else:
                reason = "no_optical_candidate_in_window"
            return None, {**evidence, "reason": reason}
        source = selection["source"]
        epochs[phase] = {
            "status": "selected", "radar_date": day, "date": source["observation_time"][:10],
            "source_id": source["source_id"],
            "absolute_date_offset_days": selection["absolute_date_offset_days"],
            "usable_fraction_of_common_radar_cells": selection["valid_analysis_cell_fraction"],
        }
        reference.update({f"{phase}_{key}": value for key, value in aligned.items()})
    return reference, evidence


def find_vegetation_reference(cube, longitude, latitude, radar_dates, common, cache):
    """Fetch a bounded recent DIST-ALERT observation for the radar bracket."""
    _, target = radar_dates
    max_days = 7
    timeout = (3, 8)
    deadline = monotonic() + 50
    assessment = {
        "start_date": (date.fromisoformat(radar_dates[0]) + timedelta(days=1)).isoformat(),
        "end_date": target,
    }
    evidence = {"status": "unavailable", "source": "OPERA DIST-ALERT-HLS vegetation disturbance",
                "assessment": assessment, "max_optical_offset_days": max_days}
    try:
        catalog = disturbance.discover(longitude, latitude, target, max_days, timeout=timeout)
        last_day = (date.fromisoformat(target) + timedelta(days=max_days)).isoformat()
        catalog["entries"] = [
            entry for entry in catalog["entries"] if target <= entry["time_start"][:10] <= last_day
        ]
    except (requests.RequestException, ValueError, KeyError):
        return None, {**evidence, "reason": "optical_catalog_unavailable"}
    if not catalog["entries"]:
        return None, {**evidence, "reason": "no_optical_candidate_in_window"}
    reference, selected = disturbance.select_reference(
        catalog, cube, assessment, target, cache, max_candidates=2, support=common,
        timeout=timeout, deadline=deadline,
    )
    if reference is None:
        reason = "optical_deadline_elapsed" if monotonic() >= deadline else "optical_assets_unavailable"
        return None, {**evidence, "reason": reason}
    return reference, {**evidence, "source_id": selected["source_id"]}


def evaluate_vegetation_evidence(cube, lens, arrays, reference, metadata):
    """Compare dated optical groups with radar on the same fixed support."""
    delta = arrays["target_change_db"]
    common = np.isfinite(delta).all(axis=0)
    baseline = {"start_date": lens["baseline_dates"][0], "end_date": lens["baseline_dates"][-1]}
    pols = lens["radar"]["polarizations"]
    channels = [list(cube["polarizations"]).index(pol) for pol in pols]
    paired_cube = {**cube, "polarizations": pols, "power": cube["power"][:, channels],
                   "valid": cube["valid"][:, channels]}
    result, masks = disturbance.compare_disturbance(reference, paired_cube, common, baseline, metadata["assessment"])
    count = result["recent_optical_supported_cells"]
    fraction = result["fraction_of_fixed_radar_support"]
    report = {
        **metadata, "optical_date": result["optical_date"], "radar_date": result["comparison_sar_date"],
        "optical_offset_days": result["optical_offset_days"], "usable_cells": count,
        "radar_denominator_cells": int(common.sum()), "usable_fraction": fraction,
        "comparison_control": "Optically non-disturbed cells matched on baseline HH/HV or VV/VH levels",
    }
    if count < 8 or fraction < OPTICAL_MIN_JOINT_FRACTION:
        return {**report, "status": "limited_support" if count else "unavailable",
                "reason": "insufficient_recent_optical_coverage"}, None

    groups = np.full(common.shape, np.nan)
    groups[masks["valid"]] = 4
    groups[masks["valid"] & (reference["confirmed_outside_window_fraction"] >= .8)] = 3
    groups[masks["valid"] & (reference["provisional_loss_fraction"] >= .8)] = 2
    groups[masks["loss"]] = 1
    groups[masks["control"]] = 0
    contrasts = {pols[0]: result["matched_contrast"],
                 pols[1]: spatial_contrast(delta[1], masks["matched_loss"], masks["matched_control"], POLICY)}
    interval_states = {}
    for pol, contrast in contrasts.items():
        interval = contrast.get("spatial_resampling_95_interval_db")
        interval_states[pol] = ("interval_unavailable" if interval is None else
                                "interval_includes_zero" if interval[0] <= 0 <= interval[1] else
                                "interval_excludes_zero")
    report.update(
        status="optical_comparison_available",
        confirmed_loss_cells=result["confirmed_in_window_loss_cells"],
        optically_non_disturbed_cells=result["reference_non_disturbed_cells"],
        matched_controls=result["matched_controls"], matched_contrasts_db=contrasts,
        matched_interval_state_by_pol=interval_states,
        comparison_state=("no_confirmed_loss_on_supported_cells" if not masks["loss"].any() else
                          "no_matched_controls" if not masks["matched_control"].any() else "matched_contrast_available"),
        interpretation="Recent optical classes describe mapped vegetation change. The cause of the change remains open.",
    )
    if "_cross_pol_assessment_db" in arrays and masks["matched_loss"].any():
        report["matched_cross_pol_history"] = {
            "polarization": pols[1],
            "matched_pairs": int(masks["matched_loss"].sum()),
            "samples": [
                {"date": day, "loss_mean_change_db": float(change[masks["matched_loss"]].mean()),
                 "control_mean_change_db": float(change[masks["matched_control"]].mean())}
                for day, change in zip(lens["assessment_dates"], arrays["_cross_pol_assessment_db"])
            ],
        }
    return report, groups


def prepare_flood_lens_case(case_dir, output):
    """Recompute the live lens with a checksum-verified optical case bundle."""
    case_dir, output = Path(case_dir), Path(output)
    admission = json.loads((case_dir / "admission.json").read_text(encoding="utf-8"))
    for name in ("cube.npz", "reference.npz"):
        with (case_dir / name).open("rb") as file:
            if file_digest(file, "sha256").hexdigest() != admission["input_sha256"][name]:
                raise ValueError(f"Measured case checksum mismatch: {name}")
    cube = load_cube(case_dir / "cube.npz")
    target = admission["metadata"]["target_date"]
    epochs = {
        phase: {"source_id": epoch["source"]["source_id"], "date": epoch["source"]["observation_time"][:10]}
        for phase, epoch in admission["reference_epochs"].items() if epoch.get("source")
    }
    with np.load(case_dir / "reference.npz", allow_pickle=False) as reference:
        report, arrays = evaluate_flood_wetland(cube, target, reference, reference_epochs=epochs)
    report["case_id"] = admission["case_id"]
    report["series_key"] = admission["metadata"]["series_key"]
    report["granule_titles"] = admission["metadata"]["selected_radar"]
    _write_case(report, arrays, cube, output, GROUPS)
    return report


def prepare_vegetation_lens_case(request_path, output, *, lens_id=VEGETATION_DISTURBANCE["id"]):
    """Use the existing measured characterization request and aligned optical reference."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    if request["source"]["mode"] != "local_cube":
        raise ValueError("A measured local cube is required.")
    cube = load_cube(request["source"]["cube_path"])
    baseline, assessment = request["baseline"], request["assessment"]
    reference, source = disturbance.load_reference(request["disturbance_reference"], cube, assessment)
    target = str(reference["sar_date"])
    # The saved reference can target a later observation inside the assessment window.
    dates = cube["dates"].astype(str)
    keep = (dates >= baseline["start_date"]) & (dates <= baseline["end_date"])
    if lens_id == WILDFIRE["id"]:
        keep |= (dates >= assessment["start_date"]) & (dates <= target)
    else:
        keep |= dates == target
    cube = {**cube, **{key: cube[key][keep] for key in ("power", "valid", "dates")}}
    report, arrays = evaluate_lens(cube, target, lens_id, baseline_end=baseline["end_date"])
    report["case_id"] = request["request_id"]
    if lens_id == WILDFIRE["id"] and request.get("context"):
        report["fire_context"] = {
            "status": "documented_regional_event",
            **{key: request["context"][key] for key in ("start_date", "end_date", "source_url", "description")
               if key in request["context"]},
        }
    if arrays:
        report["evidence"], groups = evaluate_vegetation_evidence(cube, report, arrays, reference, {
            "source": "OPERA DIST-ALERT-HLS vegetation disturbance", "source_id": source["source_id"],
            "assessment": assessment,
        })
        if groups is not None:
            arrays["optical_groups"] = groups
    _write_case(report, arrays, cube, Path(output), VEGETATION_GROUPS)
    return report


def _write_case(report, arrays, cube, output, legend):
    from .engine import write_cog

    report["grid"] = {
        "epsg": int(cube["epsg"]), "width": len(cube["x"]), "height": len(cube["y"]),
        "pixel_spacing_m": [float(cube["x"][1] - cube["x"][0]), float(cube["y"][0] - cube["y"][1])],
    }
    output.mkdir(parents=True, exist_ok=True)
    report["layers"] = write_wildfire_layers(output, arrays, cube) + write_signature_layer(output, arrays, cube)
    for key, data in arrays.items():
        if key.startswith("_") or key in {"cross_pol_decline_count", "cross_pol_pattern", "flood_signature_flags"}:
            continue
        category = key == "optical_groups"
        filename = f"{key}.tif"
        write_cog(output / filename, data, cube["x"], cube["y"], cube["epsg"],
                  "optical class code" if category else "dB",
                  ["optical_group"] if category else report["radar"]["polarizations"], angular=category)
        layer = {"role": key, "href": filename, "units": "optical class code" if category else "dB"}
        if category:
            layer["legend"] = legend
        report["layers"].append(layer)
    (output / "lens.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
