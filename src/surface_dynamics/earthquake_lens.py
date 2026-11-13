"""Observed GUNW phase support and correction readiness for earthquake analysis."""
from datetime import date

import numpy as np

from .network import acquisition_dates


def report(status, *, reason=None, intervals=()):
    return {
        "id": "earthquake",
        "version": "1.0",
        "status": status,
        "reason": reason,
        "intervals": list(intervals),
        "los_displacement_cm": None,
        "causal_attribution": None,
    }


def pair_support(pair_id, coherence, phase, threshold):
    reference, secondary = acquisition_dates(pair_id)
    interval = {
        "source_id": pair_id, "reference_date": reference, "secondary_date": secondary,
        "interval_days": abs((date.fromisoformat(secondary) - date.fromisoformat(reference)).days),
    }
    focused = np.isfinite(coherence)
    phase_available = np.isfinite(phase)
    above_threshold = focused & (coherence >= threshold)
    bins = np.linspace(0, 1, 11)
    supported = int(focused.sum())
    status, reason = "evaluated", None
    if not supported:
        status, reason = "insufficient_data", "no_focused_coherence"
    elif not phase_available.any():
        status, reason = "partial", "no_wrapped_phase_at_threshold"
    lens = report(status, reason=reason, intervals=[interval])
    lens["support"] = {
        "coherence_threshold": threshold,
        "grid_cells": int(coherence.size),
        "coherence_cells": supported,
        "coherence_at_or_above_threshold_cells": int(above_threshold.sum()),
        "wrapped_phase_cells": int(phase_available.sum()),
        "wrapped_phase_fraction_of_coherence": float(phase_available.sum() / supported) if supported else None,
        "coherence_histogram_bin_edges": bins.tolist(),
        "coherence_histogram_cells": np.histogram(coherence[focused], bins=bins)[0].tolist(),
    }
    return lens, np.where(focused, phase_available, np.nan)


def stack_support(stack, readiness=None):
    quality = stack.get("quality", {})
    if not quality:
        return report("insufficient_data", reason=stack.get("reason"))
    phase_cells = quality["common_phase_support_pixels"]
    lens = report("evaluated" if quality["shared_reference"] else "partial",
                  reason=None if quality["shared_reference"] else quality["phase_comparison_status"],
                  intervals=stack["observations"])
    lens["support"] = {
        "coherence_threshold": quality["coherence_threshold"],
        "grid_cells": stack["grid"]["width"] * stack["grid"]["height"],
        "common_phase_cells": phase_cells,
        "common_coherence_fraction_of_grid": quality["common_coherence_support_fraction"],
        "coherence_histogram_bin_edges": quality["coherence_histogram_bin_edges"],
    }
    if readiness is not None:
        correction = readiness["quality"]
        lens["readiness"] = {
            "status": readiness["status"],
            "common_correction_input_cells": correction["common_correction_input_pixels"],
            "common_correction_input_fraction_of_common_phase":
                correction["common_correction_input_fraction_of_common_phase_support"],
            "required_gates": readiness["required_gates"],
        }
    return lens
