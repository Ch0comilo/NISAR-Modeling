"""Static scientific overview and comparisons for the frozen v2 experiment."""
from pathlib import Path
import json,shutil
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from surface_dynamics import render

ROOT=Path(__file__).resolve().parents[1]


def run():
    recipe=json.loads((ROOT/'configs/global_v2.json').read_text())
    names=recipe['training_regions']+recipe['legacy_development_regions']+recipe['locked_test_regions']
    labels={'forest_river':'Forest River · USA','deliblato':'Deliblato · Serbia','chenab':'Chenab · Pakistan','niscemi':'Niscemi · Italy',
            'okavango':'Okavango · Botswana','california':'Sacramento region · USA','mekong':'Mekong region · Vietnam',
            'kafue':'Kafue · Zambia','iceland':'Southern Iceland','amazon':'Amazon · Brazil','murray':'Murray · Australia',
            'hokkaido':'Hokkaido · Japan','pantanal':'Pantanal · Brazil'}
    folder=ROOT/'docs/images';folder.mkdir(parents=True,exist_ok=True)
    render.style();fig,axs=plt.subplots(4,4,figsize=(16,17));fig.subplots_adjust(top=.89,bottom=.05,hspace=.34,wspace=.16)
    fig.suptitle('THIRTEEN REGIONS / ONE FROZEN DYNAMICS REPRESENTATION',x=.06,ha='left',y=.975,fontsize=19,fontweight='bold')
    fig.text(.06,.935,'Actual trajectory minus a constant-trajectory control at the same dates and masks. Fixed training-only RGB axes; no surface classes.',fontsize=10,color=render.MUTED)
    for ax,name in zip(axs.ravel(),names):
        with xr.open_dataset(ROOT/'products/global_v2/runs'/name/'surface_dynamics.nc') as ds:
            ax.imshow(np.nan_to_num(ds.dynamic_rgb_time.values[-1],nan=.035),interpolation='nearest')
            role='new geographic test' if name in recipe['locked_test_regions'] else 'legacy development' if name in recipe['legacy_development_regions'] else 'training geography'
            ax.set_title(labels[name]+'\n'+str(ds.time.values[-1])[:10]+' · '+role,loc='left',fontsize=9)
        ax.set_xticks([]);ax.set_yticks([])
    notes=[('HOW TO READ IT','R / G / B are three learned axes.\nSimilar colors describe model-space similarity,\nnot a shared cause or land-cover class.\n\nThe pure calendar/mask response is removed;\nnonlinear contextual effects remain.'),
           ('WHAT WAS FIT','9 training regions; 2 older development cases;\n2 new geographic tests. 128 accepted dates.\nAll quantitative cubes use 160 m posting.\n\nBlack is unavailable support. The Mekong chip\ncontains valid data west of its query center.'),
           ('WHAT REMAINS EXPERIMENTAL','States use all selected observed dates.\nNeither these colors nor reconstruction error\nare event labels or hazard probabilities.\n\nThe new model improves on v1, but interpolation\nremains slightly better on both new tests.')]
    for ax,(heading,body) in zip(axs.ravel()[13:],notes):
        ax.axis('off');ax.text(.02,.92,heading,fontsize=11,fontweight='bold',va='top');ax.text(.02,.78,body,fontsize=9,va='top',color=render.MUTED,linespacing=1.6)
    render.save(fig,folder/'global_dynamic_atlas_v2.png')

    report=json.loads((ROOT/'models/global_explorer_v2/evaluation_report.json').read_text())
    model_cases={version:{c['case_id']:c for c in report['learned_and_baseline_results'][version]['cases']} for version in ['v1','v2']}
    gp={c['case_id']:c for c in report['gaussian_process_results']}
    fig,axs=plt.subplots(2,2,figsize=(14,10));fig.subplots_adjust(top=.82,bottom=.16,hspace=.5,wspace=.3)
    fig.suptitle('FROZEN COMPARISON / MEASURED-DATE RECONSTRUCTION',x=.07,ha='left',y=.97,fontsize=18,fontweight='bold')
    fig.text(.07,.92,'Each internal acquisition is hidden in turn. Same 160 m data, scored pixels and actual dates for every method; earlier and later context are allowed.',fontsize=9,color=render.MUTED)
    methods=[('v1','V1 Transformer','#a3aabd'),('v2','V2 residual Transformer','#55d8d0'),('interpolation','Actual-time interpolation','#f7b65b'),('mean','Visible temporal mean','#c598f2'),('gp','Matérn GP','#73a9ff')]
    for ax,name in zip(axs.ravel()[:3],['hokkaido','pantanal','murray']):
        values=[model_cases['v1'][name]['pooled']['transformer_rmse_db'],model_cases['v2'][name]['pooled']['transformer_rmse_db'],model_cases['v2'][name]['pooled']['interpolation_rmse_db'],model_cases['v2'][name]['pooled']['temporal_mean_rmse_db'],gp[name]['gp_rmse_db']]
        ax.bar(np.arange(5),values,color=[m[2] for m in methods]);ax.set_xticks(np.arange(5),['V1','V2','Interp.','Mean','GP']);ax.set_ylabel('Reconstruction RMSE (dB)')
        role='NEW GEOGRAPHIC TEST' if name!='murray' else 'LEGACY DEVELOPMENT CASE'
        ax.set_title(labels[name]+' / '+role,loc='left',fontsize=10);ax.set_ylim(0,max(values)*1.28)
        for j,value in enumerate(values):ax.text(j,value+max(values)*.04,f'{value:.3f}',ha='center',fontsize=9)
    ax=axs.ravel()[3];test_names=['amazon','murray','hokkaido','pantanal'];coverage=[gp[n]['conditional_95_interval_empirical_coverage'] for n in test_names]
    ax.bar(np.arange(4),coverage,color=['#a3aabd','#a3aabd','#55d8d0','#55d8d0']);ax.axhline(.95,color='#f7b65b',ls='--',label='nominal 95%')
    ax.set_xticks(np.arange(4),['Amazon','Murray','Hokkaido','Pantanal']);ax.set_ylim(0,1.08);ax.set_ylabel('Empirical coverage fraction');ax.set_title('Conditional GP observation intervals / not calibrated',loc='left',fontsize=10);ax.legend(fontsize=8,loc='upper left')
    for i,value in enumerate(coverage):ax.text(i,value-.075,f'{value:.1%}',ha='center',fontsize=9,color='#0b1220',fontweight='bold')
    handles=[plt.Rectangle((0,0),1,1,color=m[2]) for m in methods]
    fig.legend(handles,[m[1] for m in methods],loc='upper left',bbox_to_anchor=(.065,.88),ncol=5,fontsize=9,frameon=False)
    fig.text(.07,.085,'Only two new geographic test cases. Pixels and overlapping windows are correlated. V2 changes training data and architecture together; this is not an ablation.',fontsize=9,color=render.MUTED)
    fig.text(.07,.045,'Panel y-scales differ. GP coverage is a historical observation-distribution check, not validation of the separate latent-state bands. No future forecasting.',fontsize=9,color=render.MUTED)
    render.save(fig,folder/'global_v2_benchmark.png')
    shutil.copyfile(ROOT/'products/insar_stacks/kumamoto_wide/interval_dynamics.png',folder/'kumamoto_interval_dynamics_wide.png')
    shutil.copyfile(ROOT/'products/insar_stacks/kumamoto/interval_dynamics.png',folder/'kumamoto_interval_dynamics_narrow.png')
    print('GLOBAL_V2_SCIENCE_FIGURES_COMPLETE',flush=True)


if __name__=='__main__':run()
