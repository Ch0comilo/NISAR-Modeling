"""Frozen-checkpoint, all-internal-date reconstruction against honest baselines."""
from pathlib import Path
import hashlib
import numpy as np
import torch

from .io import load_cube
from .transformer import MaskedTemporalTransformer, collate, sequences


def interpolation_baseline(values, present, times, hidden):
    """Nearest valid observation on each side, separately per pixel/channel."""
    n, length, channels = values.shape
    left = np.full((n, channels), -1, dtype=int)
    right = np.full((n, channels), -1, dtype=int)
    for i in range(hidden):
        left = np.where(present[:, i], i, left)
    for i in range(length-1, hidden, -1):
        right = np.where(present[:, i], i, right)
    rows = np.arange(n)[:, None]; cols = np.arange(channels)[None]
    li = np.maximum(left, 0); ri = np.maximum(right, 0)
    support = (left >= 0) & (right >= 0) & present[:, hidden]
    alpha = (times[hidden]-times[li])/np.maximum(times[ri]-times[li], 1e-9)
    prediction = values[rows, li, cols]*(1-alpha) + values[rows, ri, cols]*alpha
    return np.where(support, prediction, np.nan), support


def benchmark(cube_paths, checkpoint, max_pixels=512, seed=59):
    if not 1 <= max_pixels <= 2048:raise ValueError('max_pixels must be 1..2048')
    torch.set_num_threads(4)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
    model = MaskedTemporalTransformer(saved['width'], saved['layers'])
    model.load_state_dict(saved['state_dict']); model.eval()
    mean=saved['mean'].numpy();scale=saved['scale'].numpy()
    results=[];rng=np.random.default_rng(seed)
    for path in cube_paths:
        cube=load_cube(path);name=Path(path).stem
        if len(cube['dates'])<3:raise ValueError('Benchmark requires three or more dates')
        pols=list(map(str,cube['polarizations']))
        if not set(pols)<=set(saved['supported_polarizations']) or str(cube['radar_band']) not in saved['radar_bands']:raise ValueError('Benchmark cube outside trained polarization/band domain')
        x,p,t=sequences(cube);ids=np.flatnonzero(p.sum(axis=(1,2))>=3)
        if len(ids)>max_pixels:ids=np.sort(rng.choice(ids,max_pixels,replace=False))
        if not len(ids):
            results.append({'case_id':name,'status':'no_data','reason':'No measured trajectories with three observations'});continue
        values=x[ids];present=p[ids]
        a,b,tt,pad=collate([(x[i],p[i],t) for i in ids],mean,scale)
        totals={key:[0.,0] for key in ['transformer','temporal_mean','interpolation']};dates=[]
        with torch.no_grad():
            for hidden in range(1,len(t)-1):
                interpolated,support=interpolation_baseline(values,present,t,hidden)
                if not support.any():continue
                mask=torch.zeros(a.shape[:2],dtype=torch.bool);mask[:,hidden]=True
                output,_=model(a,b,tt,pad,mask);pred=output[:,hidden].numpy()*scale+mean
                visible=present.copy();visible[:,hidden]=False
                temporal_mean=np.sum(np.where(visible,values,0),axis=1)/np.maximum(visible.sum(axis=1),1)
                target=values[:,hidden];scores={}
                for key,prediction in [('transformer',pred),('temporal_mean',temporal_mean),('interpolation',interpolated)]:
                    residual=(prediction-target)[support];sse=float(np.sum(residual.astype('float64')**2));n=int(support.sum())
                    totals[key][0]+=sse;totals[key][1]+=n;scores[key+'_rmse_db']=float(np.sqrt(sse/n))
                dates.append({'masked_date':str(cube['dates'][hidden]),'scored_channel_pixels':int(support.sum()),**scores})
        scores={key+'_rmse_db':float(np.sqrt(sse/n)) if n else None for key,(sse,n) in totals.items()}
        results.append({'case_id':name,'status':'complete' if dates else 'no_data',
                        'geographic_role':'training_region' if name in saved['training_cases'] else 'unseen_case_id_not_independent_geography_proof',
                        'sampled_spatially_correlated_pixels':len(ids),'scored_acquisitions':len(dates),
                        'scored_channel_pixels':totals['transformer'][1], 'per_acquisition':dates,'pooled':scores})
    return {'benchmark_version':'1.0','task':'all_internal_observed_acquisition_reconstruction',
            'checkpoint_sha256':hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
            'seed':seed,'cases':results,'checkpoint_updated':False,
            'interpretation':[
                'Each score hides a complete acquisition from the bidirectional Transformer; all other selected dates remain visible.',
                'All methods are scored on exactly the same finite target pixels/channels with valid observations on both sides.',
                'Interpolation uses the nearest valid neighbor on each side and actual acquisition times; temporal mean excludes the target.',
                'Pixels and overlapping temporal windows are correlated; row/sample counts are not independent geographic replication.',
                'Case IDs are a convenience check; spatial provenance must establish an independent geographic holdout.',
                'This benchmark neither forecasts nor retrains/selects the model on held-out regions.'
            ]}
