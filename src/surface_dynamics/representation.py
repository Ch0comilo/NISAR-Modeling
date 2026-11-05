"""Frozen, shared projections: colors must never be refitted per date or location."""
from pathlib import Path
import numpy as np
import torch
from .analysis import measured_db,physical_statistics,elapsed_days
from .transformer import sequences,collate

FEATURE_NAMES=['HH_median_db','HV_median_db','HH_MAD_db','HV_MAD_db','HH_slope_db_day','HV_slope_db_day']

def descriptor_features(cube):
    pols=list(map(str,cube['polarizations']))
    if 'HH' not in pols or 'HV' not in pols:return None
    stat=physical_statistics(measured_db(cube),elapsed_days(cube['dates']));hh,hv=pols.index('HH'),pols.index('HV')
    return np.stack([stat['median_db'][hh],stat['median_db'][hv],stat['mad_db'][hh],stat['mad_db'][hv],stat['slope_db_per_day'][hh],stat['slope_db_per_day'][hv]],-1)

def fit_basis(values,path,names=None):
    values=np.asarray(values,dtype=float);center=values.mean(0);scale=np.maximum(values.std(0),1e-5)
    z=(values-center)/scale;_,s,vh=np.linalg.svd(z,full_matrices=False);components=vh[:3].T
    score=z@components;low,high=np.percentile(score,[2,98],axis=0)
    np.savez_compressed(path,center=center,scale=scale,components=components,low=low,high=high,variance_fraction=s[:3]**2/np.sum(s*s),feature_names=np.array(names or []))

def apply_basis(features,path):
    with np.load(path,allow_pickle=False) as f:
        z=(features-f['center'])/f['scale'];score=z@f['components'];rgb=np.clip((score-f['low'])/np.maximum(f['high']-f['low'],1e-6),0,1)
        return rgb, np.sqrt(np.nanmean(z*z,axis=-1))

def fit_projections(model,cases,mean,scale,output):
    from .transformer import POLS
    rng=np.random.default_rng(83);learned=[];physical=[];model.eval()
    with torch.no_grad():
        for cube in cases.values():
            x,p,t=sequences(cube);H,W=cube['power'].shape[-2:];yy,xx=np.indices((H,W))
            ids=np.flatnonzero((xx.ravel()<int(W*.75))&p.all(1).any(1))
            ids=rng.choice(ids,min(512,len(ids)),replace=False)
            a,b,tt,pad=collate([(x[i],p[i],t) for i in ids],mean,scale)
            _,z=model(a,b,tt,pad);learned.append(z.mean(1).numpy())
            features=descriptor_features(cube)
            if features is not None:
                f=features.reshape(-1,6)[ids];physical.append(f[np.isfinite(f).all(1)])
    output=Path(output)
    fit_basis(np.concatenate(learned),output/'embedding_projection.npz')
    if physical:fit_basis(np.concatenate(physical),output/'physical_projection.npz',FEATURE_NAMES)
