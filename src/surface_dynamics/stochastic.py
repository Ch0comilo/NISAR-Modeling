"""Empirical-Bayes Matérn-3/2 GP smoothing of observed states, no extrapolation."""
import numpy as np
from scipy.linalg import cho_factor,cho_solve
from scipy.optimize import minimize

def matern(t,ell,amplitude):
    distance=np.sqrt(3)*abs(t[:,None]-t[None,:])/ell
    return amplitude**2*(1+distance)*np.exp(-distance)

def smooth_observations(db, t, draws=256, seed=31):
    rng = np.random.default_rng(seed)
    dates, channels, _, _ = db.shape
    t = np.asarray(t, dtype=float)
    if (len(t) != dates or dates < 3 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0)):
        raise ValueError("GP smoothing requires at least three matching finite, increasing observation times.")
    means = np.full(db.shape, np.nan, dtype=float)
    sd = np.full_like(means, np.nan)
    roi_draws = np.full((draws, channels, dates), np.nan)
    observed_means = np.full((channels, dates), np.nan)
    parameters = []
    cadence, span = float(np.median(np.diff(t))), float(t[-1] - t[0])
    eye = np.eye(dates)
    bounds = np.log([[cadence / 2, span * 2], [.05, 20], [.05, 10]])
    for channel in range(channels):
        flat = db[:, channel].reshape(dates, -1)
        complete = np.isfinite(flat).all(axis=0)
        values = flat[:, complete].astype(float)
        count = values.shape[1]
        if count:
            observed_means[channel] = values.mean(axis=1)
        if count < 8:
            parameters.append({"status": "insufficient_support", "supported_cells": count})
            continue
        offset = values.mean(axis=0)
        centered = values - offset
        if np.max(np.ptp(values, axis=0)) < 1e-6:
            parameters.append({"status": "constant_history", "supported_cells": count})
            continue
        sample = centered[:, np.linspace(0, count - 1, min(64, count), dtype=int)]
        amplitude = max(float(np.sqrt(np.mean(sample ** 2))), .1)

        def loss(logp):
            length, signal_sd, noise = np.exp(logp)
            covariance = matern(t, length, signal_sd) + eye * (noise ** 2 + 1e-6)
            factor = cho_factor(covariance, lower=True)
            solved = cho_solve(factor, sample)
            return float(.5 * np.sum(sample * solved) / sample.shape[1] + np.log(np.diag(factor[0])).sum())

        initial = np.log([max(cadence, span / 5), amplitude, max(.1, amplitude / 3)])
        fit = minimize(loss, initial, method="L-BFGS-B", bounds=bounds, options={"maxiter": 80})
        if not fit.success or not np.isfinite(fit.x).all():
            parameters.append({"status": "fit_unavailable", "optimizer_success": False,
                               "supported_cells": count})
            continue
        length, signal_sd, noise = np.exp(fit.x)
        signal = matern(t, length, signal_sd)
        factor = cho_factor(signal + eye * (noise ** 2 + 1e-6), lower=True)
        weights = cho_solve(factor, signal).T
        covariance = signal - weights @ signal
        covariance = (covariance + covariance.T) / 2
        eigenvalues, vectors = np.linalg.eigh(covariance)
        lower = vectors * np.sqrt(np.maximum(eigenvalues, 0))
        fitted = offset + weights @ centered
        means[:, channel].reshape(dates, -1)[:, complete] = fitted
        sd[:, channel].reshape(dates, -1)[:, complete] = np.sqrt(np.maximum(np.diag(covariance), 0))[:, None]
        roi_draws[:, channel] = fitted.mean(axis=1) + rng.standard_normal((draws, dates)) @ lower.T
        parameters.append({"status": "fit_returned", "optimizer_success": True, "length_days": float(length),
                           "signal_sd_db": float(signal_sd), "residual_sd_db": float(noise),
                           "residual_at_lower_bound": bool(noise < .0501), "fit_pixels": sample.shape[1],
                           "supported_cells": count, "kernel": "Matern32",
                           "covariance_min_eigenvalue": float(eigenvalues.min())})
    return {"mean_db": means, "sd_db": sd, "region_draws_db": roi_draws, "observed_mean_db": observed_means,
            "parameters": parameters,
            "interpretation": "Conditional empirical-Bayes latent-state distribution at observed dates. The representative regional process uses the fitted pixel covariance. Bands omit parameter and model uncertainty. Residual scale is not measured sensor noise."}


def historical_series(result, dates, polarizations):
    """Machine-readable measured means and conditional latent bands on the fit support."""
    records = {}
    for index, pol in enumerate(polarizations):
        parameters = result["parameters"][index]
        measured = result["observed_mean_db"][index]
        quantiles = None
        if parameters["status"] == "fit_returned":
            quantiles = np.quantile(result["region_draws_db"][:, index], [.025, .1, .5, .9, .975], axis=0)
        records[str(pol)] = {"status": parameters["status"], "supported_cells": parameters["supported_cells"],
                             "series": [{"date": str(day), "observed_mean_db": float(measured[i]),
                                         "latent_median_db": float(quantiles[2, i]) if quantiles is not None else None,
                                         "conditional_80_db": quantiles[[1, 3], i].tolist() if quantiles is not None else None,
                                         "conditional_95_db": quantiles[[0, 4], i].tolist() if quantiles is not None else None}
                                        for i, day in enumerate(dates) if np.isfinite(measured[i])]}
    return records


def reconstruct_internal_gp(values,present,t,hidden):
    """Empirical-Bayes historical-date observation distribution, hidden data excluded.

    Inputs are [pixel,time,channel]. Complete visible trajectories share channel
    covariance parameters, fitted to at most 64 pixels. This benchmark function
    does not alter measured cubes or fit on the hidden acquisition.
    """
    N,T,C=values.shape
    if not 0<hidden<T-1:raise ValueError('GP reconstruction requires an internal observed date, not extrapolation')
    visible=np.arange(T)!=hidden;tv=np.asarray(t)[visible]
    mean=np.full((N,C),np.nan);sd=np.full_like(mean,np.nan);parameters=[]
    for c in range(C):
        eligible=present[:,visible,c].all(1)&present[:,hidden,c]
        ids=np.flatnonzero(eligible)
        if len(ids)<8 or len(tv)<3:
            parameters.append({'status':'insufficient_complete_visible_support'});continue
        y=values[ids][:,visible,c].T.astype('float64');offset=y.mean(0);z=y-offset
        sample=z[:,np.linspace(0,len(ids)-1,min(64,len(ids)),dtype=int)]
        cadence=float(np.median(np.diff(tv)));span=float(tv[-1]-tv[0]);amplitude=max(float(np.sqrt(np.mean(sample**2))),.1)
        def loss(logp):
            ell,a,noise=np.exp(logp);K=matern(tv,ell,a)+np.eye(len(tv))*(noise**2+1e-6)
            cf=cho_factor(K,lower=True);sol=cho_solve(cf,sample)
            return float(.5*np.sum(sample*sol)/sample.shape[1]+np.log(np.diag(cf[0])).sum())
        fit=minimize(loss,np.log([max(cadence,span/5),amplitude,max(.1,amplitude/3)]),method='L-BFGS-B',
                     bounds=np.log([[cadence/2,span*2],[.05,20],[.05,10]]),options={'maxiter':80})
        ell,a,noise=np.exp(fit.x);K=matern(tv,ell,a)+np.eye(len(tv))*(noise**2+1e-6);cf=cho_factor(K,lower=True)
        distance=np.sqrt(3)*abs(tv-float(t[hidden]))/ell;k=a*a*(1+distance)*np.exp(-distance)
        prediction=offset+k@cho_solve(cf,z);latent_variance=max(float(a*a-k@cho_solve(cf,k)),0.)
        mean[ids,c]=prediction;sd[ids,c]=np.sqrt(latent_variance+noise**2)
        parameters.append({'status':'fit_returned','optimizer_success':bool(fit.success),'length_days':float(ell),
                           'signal_sd_db':float(a),'residual_sd_db':float(noise),'fit_visible_pixels':sample.shape[1],
                           'interval_kind':'conditional_Gaussian_observation_distribution; parameter/mean uncertainty omitted'})
    return {'mean_db':mean,'observation_sd_db':sd,'parameters':parameters}
