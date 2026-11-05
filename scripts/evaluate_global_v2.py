"""Freeze first, then compare v1/v2, GP and baselines on the same 160 m data."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
import numpy as np
from surface_dynamics.io import load_cube
from surface_dynamics.transformer import sequences
from surface_dynamics.benchmark import benchmark,interpolation_baseline
from surface_dynamics.stochastic import reconstruct_internal_gp

ROOT=Path(__file__).resolve().parents[1]


def run():
    recipe=json.loads((ROOT/'configs/global_v2.json').read_text());model=ROOT/'models/global_explorer_v2';checkpoint=model/'masked_temporal_transformer.pt'
    names=recipe['legacy_development_regions']+recipe['locked_test_regions']
    if (model/'evaluation_report.json').exists():raise RuntimeError('An evaluation is already recorded; do not silently reuse a geographic test for tuning')
    freeze={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            'recipe_sha256':hashlib.sha256((ROOT/'configs/global_v2.json').read_bytes()).hexdigest(),
            'architecture_source_sha256':hashlib.sha256((ROOT/'src/surface_dynamics/temporal_v2.py').read_bytes()).hexdigest(),
            'fresh_test_regions':recipe['locked_test_regions'],'legacy_development_regions':recipe['legacy_development_regions'],
            'selection_scope':'training-region spatial stripes only; no geographic scores read during training'}
    (model/'evaluation_freeze.json').write_text(json.dumps(freeze,indent=2))
    paths=[ROOT/'products/global_v2/cubes'/f'{name}.npz' for name in names]
    reports={}
    for version in ['v1','v2']:
        reports[version]=benchmark(paths,ROOT/f'models/global_explorer_{version}/masked_temporal_transformer.pt',complete_trajectories_only=True)
    rng=np.random.default_rng(59);gp_cases=[]
    for name,path in zip(names,paths):
        cube=load_cube(path);values,p,t=sequences(cube);ids=np.flatnonzero(p.all(1).any(1))
        if len(ids)>512:ids=np.sort(rng.choice(ids,512,replace=False))
        values=values[ids];p=p[ids];sse=0.;count=0;covered80=covered95=0;nll=0.;per_date=[]
        for j in range(1,len(t)-1):
            result=reconstruct_internal_gp(values,p,t,j);_,support=interpolation_baseline(values,p,t,j)
            if not np.array_equal(np.isfinite(result['mean_db']),support):raise ValueError('GP and learned benchmark target support must be identical')
            error=(result['mean_db']-values[:,j])[support];sd=result['observation_sd_db'][support];n=len(error)
            sse+=float(np.sum(error**2));count+=n
            covered80+=int((abs(error)<=1.2815515655446004*sd).sum());covered95+=int((abs(error)<=1.959963984540054*sd).sum())
            nll+=float(np.sum(.5*np.log(2*np.pi*sd**2)+.5*(error/sd)**2))
            per_date.append({'masked_date':str(cube['dates'][j]),'gp_rmse_db':float(np.sqrt(np.mean(error**2))),'scored_channel_pixels':n,'parameters':result['parameters']})
        gp_cases.append({'case_id':name,'scored_channel_pixels':count,'gp_rmse_db':float(np.sqrt(sse/count)),
                         'conditional_80_interval_empirical_coverage':covered80/count,'conditional_95_interval_empirical_coverage':covered95/count,
                         'conditional_Gaussian_mean_negative_log_density':nll/count,'per_acquisition':per_date})
    report={'evaluation_version':'1.0','freeze':freeze,'posting_m':160,'learned_and_baseline_results':reports,
            'gaussian_process_results':gp_cases,'test_driven_tuning_performed':False,
            'limitations':['Complete-trajectory benchmark; valid but incomplete trajectories are excluded, which can bias toward easier support.',
                           'Only two fresh geographic tests; pixels and overlapping dates are correlated, not independent world cases.',
                           'Version comparison changes training data and architecture together; it is not an architecture ablation.',
                           'GP empirical coverage describes conditional historical observation intervals on these pixels; it is not globally calibrated process/sensor uncertainty.',
                           'No forecast, event class, causal attribution or hazard probability.']}
    (model/'evaluation_report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    for i,name in enumerate(names):
        print(json.dumps({'case':name,'role':'fresh_test' if name in recipe['locked_test_regions'] else 'legacy_development',
                          'v1':reports['v1']['cases'][i]['pooled'],'v2':reports['v2']['cases'][i]['pooled'],
                          'gp_rmse_db':gp_cases[i]['gp_rmse_db'],'gp_95_coverage':gp_cases[i]['conditional_95_interval_empirical_coverage']}),flush=True)
    return report


if __name__=='__main__':run()
