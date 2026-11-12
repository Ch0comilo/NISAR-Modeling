"""Actual-time spectral maps and permutation diagnostics; no invented observations."""
import numpy as np

def basis_projectors(t,frequencies):
    Q=[]
    for f in frequencies:
        design=np.stack([np.ones(len(t)),np.cos(2*np.pi*f*t),np.sin(2*np.pi*f*t)],1)
        Q.append(design@np.linalg.pinv(design))
    return np.stack(Q)

def periodogram(y,t,frequencies):
    y=np.asarray(y);one=y.ndim==1
    if one:y=y[:,None]
    centered=y-y.mean(0);energy=np.sum(centered**2,axis=0)
    explained=[]
    for f in frequencies:
        design=np.stack([np.ones(len(t)),np.cos(2*np.pi*f*t),np.sin(2*np.pi*f*t)],1)
        coeff=np.linalg.pinv(design)@centered
        explained.append(np.sum(coeff*((design.T@design)@coeff),axis=0))
    explained=np.stack(explained)
    power=np.clip(explained/np.maximum(energy,1e-12),0,1)
    power[:,energy<1e-10]=0
    return power[:,0] if one else power

MINIMUM_SPECTRAL_DATES = 6


def spectral_analysis(db, t, permutations=199, seed=41):
    """Compare trend-only and trend-plus-sinusoid fits on actual acquisition times."""
    t = np.asarray(t, dtype=float)
    dates, channels, height, width = db.shape
    gaps = np.diff(t)
    if len(t) != dates or not np.isfinite(t).all() or np.any(gaps <= 0):
        raise ValueError("Spectral analysis requires matching finite, increasing observation times.")
    if dates < MINIMUM_SPECTRAL_DATES:
        return {"status": "insufficient_temporal_support", "reason": "Need at least six observed dates"}
    span, cadence = float(t[-1] - t[0]), float(np.median(gaps))
    fmin, fmax = 2 / span, .5 / cadence
    if fmin >= fmax:
        return {"status": "insufficient_temporal_support", "reason": "Need room for two cycles in the display band"}
    times = t - t.mean()
    trend, _ = np.linalg.qr(np.column_stack([np.ones(dates), times]))
    residualize = np.eye(dates) - trend @ trend.T
    frequencies = np.linspace(fmin, fmax, 64, endpoint=False)
    angle = 2 * np.pi * frequencies[:, None] * times
    designs = np.stack([np.cos(angle), np.sin(angle)], axis=-1)
    basis, singular, _ = np.linalg.svd(residualize @ designs, full_matrices=False)
    usable = singular[:, 1] > 1e-8
    frequencies, basis = frequencies[usable], basis[usable]
    if not len(frequencies):
        return {"status": "insufficient_temporal_support", "reason": "No identifiable sinusoidal design"}
    periods = 1 / frequencies
    project = basis.transpose(0, 2, 1)

    def power(values):
        energy = np.sum(values ** 2, axis=0)
        projected = np.sum((project @ values) ** 2, axis=1)
        return np.clip(projected / np.maximum(energy, 1e-10), 0, 1)

    maps = np.full((channels, height, width), np.nan)
    strength = np.full_like(maps, np.nan)
    roi_spectra = np.full((channels, len(frequencies)), np.nan)
    summaries = []
    rng = np.random.default_rng(seed)
    for index in range(channels):
        flat = db[:, index].reshape(dates, -1)
        pixels = np.flatnonzero(np.isfinite(flat).all(axis=0))
        detrended = residualize @ flat[:, pixels]
        active = np.sum(detrended ** 2, axis=0) > 1e-10
        summary = {"supported_cells": int(len(pixels)), "nonconstant_cells": int(active.sum()),
                   "candidate_period_days": None, "normalized_power": None,
                   "max_statistic_permutation_p": None, "tentative": True}
        if active.sum() < 8:
            summaries.append({**summary, "status": "insufficient_nonconstant_complete_histories"})
            continue
        scores = power(detrended[:, active])
        peaks = scores.argmax(axis=0)
        maps[index].ravel()[pixels[active]] = periods[peaks]
        strength[index].ravel()[pixels[active]] = scores[peaks, np.arange(len(peaks))]
        roi = detrended.mean(axis=1)
        if float(roi @ roi) <= 1e-10:
            summaries.append({**summary, "status": "regional_residual_variation_unresolved"})
            continue
        spectrum = power(roi[:, None])[:, 0]
        permutations_of_roi = np.column_stack([rng.permutation(roi) for _ in range(permutations)])
        null = power(residualize @ permutations_of_roi).max(axis=0)
        peak = float(spectrum.max())
        p = (1 + int((null >= peak).sum())) / (permutations + 1)
        roi_spectra[index] = spectrum
        summaries.append({**summary, "status": "available", "candidate_period_days": float(periods[spectrum.argmax()]),
                          "normalized_power": peak, "max_statistic_permutation_p": p,
                          "null": "Exchangeable detrended residuals; invalid under arbitrary serial dependence",
                          "independent_frequency_resolution_cycles_day": 1 / span,
                          "tentative": bool(dates < 16 or p > .05)})
    if not np.isfinite(maps).any():
        return {"status": "insufficient_temporal_support", "reason": "Need eight nonconstant complete histories in a channel",
                "channels": summaries}
    sampling_window = abs(np.exp(2j * np.pi * frequencies[:, None] * times).mean(axis=1)) ** 2
    return {"status": "available", "frequencies_cycles_day": frequencies, "periods_days": periods,
            "candidate_period_days": maps, "peak_normalized_power": strength, "roi_spectra": roi_spectra,
            "sampling_window": sampling_window, "channels": summaries,
            "interpretation": "A bounded candidate period after controlling for a linear trend. Sampling aliases and short histories limit interpretation."}

def local_time_frequency(series,t,minimum_dates=12):
    """Locally weighted actual-time sinusoidal analysis with effective-support masks."""
    if len(t)<minimum_dates:return {'status':'insufficient_temporal_support'}
    cadence=np.median(np.diff(t));span=t[-1]-t[0]
    if span/2<=2*cadence or not np.isfinite(series).all():return {'status':'insufficient_temporal_support'}
    periods=np.geomspace(2*cadence,span/2,24)
    power=np.full((len(periods),len(t)),np.nan)
    for i,period in enumerate(periods):
        for j,center in enumerate(t):
            weights=np.exp(-.5*((t-center)/(period/2))**2)
            neff=weights.sum()**2/np.sum(weights**2)
            if neff<6 or center-t[0]<period/2 or t[-1]-center<period/2:continue
            X=np.stack([np.ones(len(t)),np.cos(2*np.pi*(t-center)/period),np.sin(2*np.pi*(t-center)/period)],1)
            WX=X*np.sqrt(weights[:,None]);target=series*np.sqrt(weights)
            coeff=np.linalg.lstsq(WX,target,rcond=None)[0]
            if np.linalg.matrix_rank(WX)<3:continue
            total=np.sum(weights*(series-np.average(series,weights=weights))**2)
            residual=np.sum(weights*(series-X@coeff)**2)
            power[i,j]=np.clip(1-residual/max(total,1e-12),0,1)
    return {'status':'available' if np.isfinite(power).any() else 'insufficient_local_support','periods_days':periods,'power':power,'method':'Gaussian-window weighted sinusoid at actual sample times; effective n>=6; edge mask period/2. Descriptive, not a conventional regularly sampled CWT.'}
