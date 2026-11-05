"""Offline layout QA fixes, global atlas, benchmark figure and artifact rehash."""
from pathlib import Path
import json,hashlib
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from surface_dynamics import render
from surface_dynamics.io import load_cube
from surface_dynamics.analysis import measured_db,elapsed_days

ROOT=Path(__file__).resolve().parents[1]
cases=json.loads((ROOT/'products/global_examples.json').read_text())
for item in cases:
    case=item['case_id'];out=ROOT/'products/runs'/case;cube=load_cube(ROOT/'products/cubes'/(case+'.npz'));db=measured_db(cube)
    with xr.open_dataset(out/'surface_dynamics.nc') as ds:
        stat={k:ds[k].values for k in ['median_db','mad_db','slope_db_per_day','maximum_step_db']}
        render.physical_figure(cube,db,stat,{'spatial_anisotropy':ds.spatial_anisotropy.values},out/'physical.png',case.replace('_',' '))
        result=json.loads((out/'result.json').read_text())
        if (out/'spectral_arrays.npz').exists():
            with np.load(out/'spectral_arrays.npz') as a:
                spectral={'candidate_period_days':ds.candidate_period_days.values,'peak_normalized_power':ds.peak_normalized_power.values,'periods_days':1/a['frequencies_cycles_day'],'roi_spectra':a['roi_spectra'],'sampling_window':a['sampling_window'],'channels':result['capabilities']['spectral']['metadata']['channels']}
                local={'status':'unavailable'}
                render.spectral_figure(cube,spectral,local,out/'spectral.png',case.replace('_',' '))
        if 'learned_rgb' in ds:render.learned_figure(cube,db,ds.learned_rgb.values,ds.dropout_embedding_sd.values,ds.embedding_standardized_distance.values,out/'learned.png',case.replace('_',' '))
    for a in result['artifacts']:a['sha256']=hashlib.sha256((out/a['href']).read_bytes()).hexdigest()
    (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))

render.style();fig,axs=plt.subplots(2,4,figsize=(16,9));fig.subplots_adjust(top=.85,bottom=.12,wspace=.12,hspace=.30)
fig.suptitle('ONE SHARED TRANSFORMER  /  SIX CONTINENTS OF REAL NISAR DATA',x=.06,ha='left',fontsize=19,fontweight='bold',y=.97)
fig.text(.06,.90,'A fixed training-only color projection of multi-date latent states. Regional examples, not a globally validated event model.',color=render.MUTED,fontsize=11)
for ax,item in zip(axs.ravel(),cases):
    out=ROOT/'products/runs'/item['case_id']
    with xr.open_dataset(out/'surface_dynamics.nc') as ds:
        ax.imshow(np.nan_to_num(ds.learned_rgb.values,nan=.04))
        domain='geographic holdout' if item['case_id'] in ['amazon','murray'] else 'training geography'
        ax.set_title(item['name']+'\n'+str(item['dates'])+' measured dates · '+domain,loc='left',fontsize=10)
    ax.set_xticks([]);ax.set_yticks([])
axs.ravel()[-1].axis('off');axs.ravel()[-1].text(.02,.9,'WHAT THE COLORS MEAN\n\nR / G / B = three fixed axes\nof a learned radar representation.\n\nSimilar hue is a descriptive similarity,\nnot proof of a shared phenomenon.\n\nCore measurements and all quality\nflags remain available separately.',va='top',fontsize=11,color=render.FG,linespacing=1.5)
fig.text(.06,.055,'Same 41,716-parameter model across locations. Five training regions, two geographic holdouts; no event labels or future forecasting.',fontsize=10,color=render.MUTED)
render.save(fig,ROOT/'products/global_surface_atlas.png')

report=json.loads((ROOT/'models/global_explorer_v1/training_report.json').read_text());held=report['heldout_case_results'];names=list(held)
fig,ax=plt.subplots(figsize=(9,5.5));x=np.arange(len(names));w=.24
for j,(key,label,color) in enumerate([('transformer_rmse_db','Masked Transformer','#55d8d0'),('visible_temporal_mean_rmse_db','Visible temporal mean','#c598f2'),('linear_interpolation_rmse_db','Actual-time interpolation','#f7b65b')]):ax.bar(x+(j-1)*w,[held[n][key] for n in names],width=w,color=color,label=label)
ax.set_xticks(x,['Amazon · Brazil','Murray Basin · Australia']);ax.set_ylabel('Masked acquisition reconstruction RMSE (dB)');ax.set_ylim(0,2.15);ax.legend(fontsize=9,ncol=3,loc='lower left',bbox_to_anchor=(0,1.02));ax.set_title('A deeper model must be compared with simple baselines',loc='left',pad=48)
fig.subplots_adjust(top=.76,bottom=.24);fig.text(.08,.10,'One internal observed acquisition withheld per unseen region; 512 spatially correlated pixels per case.\nOnly two independent held-out geographies. This is reconstruction, not future forecasting or event accuracy.',fontsize=9,color=render.MUTED)
render.save(fig,ROOT/'products/transformer_validation.png')
print('ATLAS_AND_LAYOUT_QA_COMPLETE',flush=True)
