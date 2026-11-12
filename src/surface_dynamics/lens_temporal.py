"""Historical diagnostics on the dates and fixed cells already used by a lens."""

import numpy as np

from .analysis import elapsed_days, measured_db
from .spectral import spectral_analysis
from .stochastic import historical_series, smooth_observations


def temporal_analysis(cube, report, support):
    dates = report["baseline_dates"] + report.get("assessment_dates", [report["target_date"]])
    cube_dates = cube["dates"].astype(str).tolist()
    date_indices = [cube_dates.index(day) for day in dates]
    names = report["radar"]["polarizations"]
    channel_indices = [list(cube["polarizations"]).index(pol) for pol in names]
    db = np.where(support, measured_db(cube)[date_indices][:, channel_indices], np.nan)
    times = elapsed_days(dates)
    spectral = spectral_analysis(db, times)
    spectral_summary = {key: spectral[key] for key in ("status", "reason") if key in spectral}
    if "channels" in spectral:
        spectral_summary["channels"] = dict(zip(names, spectral["channels"]))
    if len(dates) >= 5:
        gp = smooth_observations(db, times, draws=128)
        histories = historical_series(gp, dates, names)
        fit_available = any(item["status"] == "fit_returned" for item in histories.values())
        stochastic = {"status": "experimental" if fit_available else "insufficient_data",
                      "channels": histories, "calibrated": False, "interpretation": gp["interpretation"]}
    else:
        stochastic = {"status": "insufficient_data", "reason": "Need five measured dates for a lens GP",
                      "calibrated": False}
    return {"dates": dates, "elapsed_days": times.tolist(), "acquisition_gaps_days": np.diff(times).tolist(),
            "common_cells": int(support.sum()), "spectral": spectral_summary, "stochastic": stochastic,
            "observation_noise_db": None}
