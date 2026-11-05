"""Empirical-Bayes Matérn-3/2 GP smoothing of observed states, no extrapolation."""
import numpy as np
from scipy.linalg import cho_factor,cho_solve
from scipy.optimize import minimize

def matern(t,ell,amplitude):
    distance=np.sqrt(3)*abs(t[:,None]-t[None,:])/ell
    return amplitude**2*(1+distance)*np.exp(-distance)

def smooth_observations(db,t,draws=256,seed=31):
    rng=np.random.default_rng(seed);T,C,H,W=db.shape
    means=np.full_like(db,np.nan);sd=np.full_like(db,np.nan);roi_draws=[];parameters=[]
    for c in range(C):
        flat=db[:,c].reshape(T,-1);complete=np.isfinite(flat).all(0)
        values=flat[:,complete]
        if values.shape[1]<8:
            roi_draws.append(np.full((draws,T),np.nan));parameters.append({'status':'insufficient_support'});continue
        offset=values.mean(0);z=values-offset
        sample=z[:,np.linspace(0,z.shape[1]-1,min(64,z.shape[1]),dtype=int)]
        cadence=float(np.median(np.diff(t)));span=float(t[-1]-t[0]);amp=max(float(np.sqrt(np.mean(sample**2))),.1)
        def loss(logp):
            ell,a,noise=np.exp(logp);K=matern(t,ell,a)+np.eye(T)*(noise**2+1e-6)
            cf=cho_factor(K,lower=True);sol=cho_solve(cf,sample)
            return float(.5*np.sum(sample*sol)/sample.shape[1]+np.log(np.diag(cf[0])).sum())
        fit=minimize(loss,np.log([max(cadence,span/5),amp,max(.1,amp/3)]),method='L-BFGS-B',bounds=np.log([[cadence/2,span*2],[.05,20],[.05,10]]),options={'maxiter':80})
        ell,a,noise=np.exp(fit.x);signal=matern(t,ell,a);K=signal+np.eye(T)*(noise**2+1e-6)
        cf=cho_factor(K,lower=True);A=signal@cho_solve(cf,np.eye(T))
        cov=signal-A@signal;cov=(cov+cov.T)/2
        eig,V=np.linalg.eigh(cov);L=V@np.diag(np.sqrt(np.maximum(eig,0)))
        out=offset+A@z
        means[:,c].reshape(T,-1)[:,complete]=out
        sd[:,c].reshape(T,-1)[:,complete]=np.sqrt(np.maximum(np.diag(cov),0))[:,None]
        # Representative ROI posterior uses the same fitted covariance; it is not a sampling-error CI.
        roi=out.mean(1);roi_draws.append(roi[None]+rng.standard_normal((draws,T))@L.T)
        parameters.append({'status':'fit_returned','optimizer_success':bool(fit.success),'length_days':float(ell),'signal_sd_db':float(a),'residual_sd_db':float(noise),'residual_at_lower_bound':bool(noise<.0501),'fit_pixels':sample.shape[1],'kernel':'Matern32','covariance_min_eigenvalue':float(eig.min())})
    return {'mean_db':means,'sd_db':sd,'region_draws_db':np.stack(roi_draws,axis=1),'parameters':parameters,'interpretation':'Conditional empirical-Bayes latent-state distribution at observed dates. Regional mean is treated as one scalar process with the shared fitted pixel covariance; its variance is not reduced by pixel count. Hyperparameter, mean and model uncertainty are omitted; residual variance is not identified speckle noise. Not calibrated sensor uncertainty.'}


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
