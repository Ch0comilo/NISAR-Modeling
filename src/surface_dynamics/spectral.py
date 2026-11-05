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

def spectral_analysis(db,t,permutations=199,seed=41):
    T,C,H,W=db.shape;span=float(t[-1]-t[0]);cadence=float(np.median(np.diff(t)))
    if any(np.isfinite(db[:,c]).all(0).sum()<8 for c in range(C)):return {'status':'insufficient_temporal_support','reason':'Fewer than eight complete finite pixel trajectories in at least one channel'}
    # A conservative display band, not a universal Nyquist theorem for irregular samples.
    fmin=2/span;fmax=.5/cadence
    if T<6 or fmin>=fmax:return {'status':'insufficient_temporal_support','reason':'Need >=6 dates and room for at least two cycles in the selected display band'}
    frequencies=np.linspace(fmin,fmax,64);periods=1/frequencies
    maps=np.full((C,H,W),np.nan);strength=np.full_like(maps,np.nan);summaries=[];roi_spectra=[]
    rng=np.random.default_rng(seed)
    for c in range(C):
        flat=db[:,c].reshape(T,-1);valid=np.isfinite(flat).all(0);z=flat[:,valid]
        # Remove a fitted linear trend before oscillatory comparison; retain actual timestamps.
        X=np.stack([np.ones(T),t-t.mean()],1);detrended=z-X@np.linalg.lstsq(X,z,rcond=None)[0]
        power=periodogram(detrended,t,frequencies);idx=np.argmax(power,axis=0)
        maps[c].ravel()[valid]=periods[idx];strength[c].ravel()[valid]=power[idx,np.arange(len(idx))]
        roi=detrended.mean(1);spec=periodogram(roi,t,frequencies);peak=float(spec.max())
        null=np.array([periodogram(rng.permutation(roi),t,frequencies).max() for _ in range(permutations)])
        p=(1+int(np.sum(null>=peak)))/(permutations+1)
        roi_spectra.append(spec)
        summaries.append({'candidate_period_days':float(periods[np.argmax(spec)]),'normalized_power':peak,'max_statistic_permutation_p':p,'null':'exchangeable detrended residuals; not valid for arbitrary serial dependence','independent_frequency_resolution_cycles_day':1/span,'tentative':bool(T<16 or p>.05)})
    sampling_window=abs(np.exp(2j*np.pi*frequencies[:,None]*t).mean(1))**2
    return {'status':'available','frequencies_cycles_day':frequencies,'periods_days':periods,'candidate_period_days':maps,'peak_normalized_power':strength,'roi_spectra':np.stack(roi_spectra),'sampling_window':sampling_window,'channels':summaries,'interpretation':'Strongest candidate in a bounded search band, not proof of seasonality or a phenomenon. Irregular sampling aliases remain; no per-pixel significance is asserted.'}

def local_time_frequency(series,t,minimum_dates=12):
    """Locally weighted actual-time sinusoidal analysis with effective-support masks."""
    if len(t)<minimum_dates:return {'status':'insufficient_temporal_support'}
    cadence=np.median(np.diff(t));span=t[-1]-t[0]
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
