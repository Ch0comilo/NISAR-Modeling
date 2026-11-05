"""Fixed descriptive patterns of observed radar trajectories, never event labels."""
from pathlib import Path
import json
import numpy as np
from .analysis import measured_db
from .flood_signatures import signal_intervals,POLICY as FLOOD_POLICY

POLICY=json.loads((Path(__file__).parent/'schemas/behavior-policy.json').read_text())
LEGEND={0:'insufficient_temporal_support',1:'no_observed_threshold_crossing',
        2:'repeated_observed_darkening',3:'repeated_observed_brightening',
        4:'intermittent_observed_crossings',5:'mixed_observed_change',
        6:'gradual_observed_darkening',7:'gradual_observed_brightening',255:'unsupported'}


def classify_trajectories(change,days,before,after,policy=POLICY):
    """Each column is one fixed-support trajectory, with actual time gaps."""
    shape=change.shape[1:];a=np.asarray(change).reshape(len(days),-1)
    valid=np.isfinite(a).all(0);labels=np.full(a.shape[1],255,dtype='uint8')
    if len(before)<policy['minimum_baseline_dates'] or len(after)<policy['minimum_assessment_dates']:
        labels[valid]=0;return labels.reshape(shape)
    threshold=policy['co_pol_change_db'];post=a[after]
    dark=(post<=-threshold).sum(0);bright=(post>=threshold).sum(0)
    changed=dark+bright;minimum=max(policy['repeated_minimum_count'],int(np.ceil(len(after)*policy['repeated_fraction'])))
    labels[valid]=5;labels[valid&(changed==0)]=1
    labels[valid&(changed>0)&(changed<len(after))&(len(after)>=3)]=4
    labels[valid&(dark>=minimum)]=2;labels[valid&(bright>=minimum)]=3
    if len(days)>=policy['gradual_minimum_dates'] and np.max(np.diff(days))<=2*np.median(np.diff(days)):
        step=np.diff(a,axis=0);net=a[-1]-a[0]
        smooth=np.max(abs(step),axis=0)<=policy['gradual_maximum_step_fraction']*abs(net)
        fraction=policy['gradual_directional_fraction']
        labels[valid&smooth&(net<=-threshold)&((step<=0).mean(0)>=fraction)]=6
        labels[valid&smooth&(net>=threshold)&((step>=0).mean(0)>=fraction)]=7
    return labels.reshape(shape)


def summarize(cube,baseline,assessment,selection=None,policy=POLICY):
    dates=cube['dates'].astype(str)
    before=np.flatnonzero((dates>=baseline['start_date'])&(dates<=baseline['end_date']))
    after=np.flatnonzero((dates>=assessment['start_date'])&(dates<=assessment['end_date']))
    if not len(before) or not len(after):return {'status':'no_data','reason':'Both declared windows need actual acquisitions'}
    chosen=np.sort(np.concatenate([before,after]));db=measured_db(cube)
    pols=list(map(str,cube['polarizations']));co=pols.index('HH') if 'HH' in pols else pols.index('VV') if 'VV' in pols else None
    if co is None:return {'status':'no_data','reason':'A measured co-polarized channel is required'}
    cross_name='HV' if pols[co]=='HH' else 'VH';cross=pols.index(cross_name) if cross_name in pols else None
    channels=[co]+([cross] if cross is not None else [])
    selected=np.ones(db.shape[-2:],bool) if selection is None else selection.astype(bool)
    common=selected&np.isfinite(db[chosen][:,channels]).all(axis=(0,1))
    quality={'selected_grid_cells':int(selected.sum()),'fixed_common_support_cells':int(common.sum()),
             'fixed_fraction_of_selection':float(common.sum()/selected.sum()) if selected.any() else None,
             'per_date_channel_supported_cells':np.isfinite(db[:,:,selected]).sum(-1).tolist(),
             'support_policy':'Same cells observed in every chosen baseline/assessment date and selected co-/cross-pol channel; no changing-population trajectory'}
    if not common.any():return {'status':'no_data','reason':'No fixed common measured support across the declared windows','quality':quality}
    base=np.median(db[before][:,:,common],axis=0)
    full_base=np.full(db.shape[1:],np.nan);full_base[:,common]=base
    change=np.where(common[None,None],db-full_base[None],np.nan)
    target=int(after[0]);days=(np.array(dates,dtype='datetime64[D]')-np.datetime64(dates[0])).astype('timedelta64[D]').astype(float)
    local_before=np.flatnonzero(np.isin(chosen,before));local_after=np.flatnonzero(np.isin(chosen,after))
    pattern=classify_trajectories(change[chosen,co],days[chosen],local_before,local_after,policy)
    regional=np.median(change[:,:,common],axis=-1)
    regional_pattern=int(classify_trajectories(regional[chosen,co,None],days[chosen],local_before,local_after,policy)[0])
    post=change[after,co];count=np.isfinite(post).sum(0)
    frequency=np.divide((abs(post)>=policy['co_pol_change_db']).sum(0),count,
                        out=np.full(count.shape,np.nan),where=count>0)
    channel_summary=[];series=[]
    for c in channels:
        delta=change[target,c][common];values=db[chosen,c][:,common]
        mad=1.4826*np.median(abs(values-np.median(values,axis=0)),axis=0)
        channel_summary.append({'polarization':pols[c],'first_assessment_change_db':{
            'mean':float(delta.mean()),'median':float(np.median(delta)),
            'p05':float(np.quantile(delta,.05)),'p25':float(np.quantile(delta,.25)),
            'p75':float(np.quantile(delta,.75)),'p95':float(np.quantile(delta,.95))},
            'median_temporal_scaled_mad_db':float(np.median(mad)),
            'first_assessment_darkening_fraction':float((delta<=-policy['co_pol_change_db']).mean()),
            'first_assessment_brightening_fraction':float((delta>=policy['co_pol_change_db']).mean())})
        for i in chosen:
            value=change[i,c][common]
            series.append({'date':dates[i],'polarization':pols[c],'fixed_cells':int(common.sum()),
                           'mean_change_db':float(value.mean()),'median_change_db':float(np.median(value)),
                           'p25_change_db':float(np.quantile(value,.25)),'p75_change_db':float(np.quantile(value,.75))})
    paired_ratio=None
    if cross is not None:
        ratio=change[target,cross][common]-change[target,co][common]
        paired_ratio={'definition':cross_name+' minus '+pols[co]+' dB change, equivalent to change in log power ratio',
                      'mean_db':float(ratio.mean()),'median_db':float(np.median(ratio)),
                      'independent_measurement':False}
    regional_chosen=regional[chosen,co]
    crossings=np.flatnonzero(abs(regional_chosen[local_after])>=policy['co_pol_change_db'])
    direction=int(np.sign(regional_chosen[local_after[crossings[0]]])) if len(crossings) else 1
    timing=signal_intervals(regional_chosen,dates[chosen],local_before,local_after,direction,
                            {**FLOOD_POLICY,'co_pol_change_db':policy['co_pol_change_db']})
    timing['statistic']='Median co-pol change on fixed common spatial support'
    counts={LEGEND[int(code)]:int((pattern==code).sum()) for code in np.unique(pattern[common])}
    return {'status':'characterized','quality':quality,'before':before,'after':after,'chosen':chosen,
            'baseline_db':full_base,'change_db':change,'target_change_db':change[target],
            'pattern_code':pattern,'comparison_support':common,'selection_mask':selected,
            'observed_change_frequency':frequency,'valid_assessment_count':count,
            'regional_pattern':LEGEND[regional_pattern],'pattern_counts':counts,
            'co_index':co,'cross_index':cross,'co_polarization':pols[co],
            'cross_polarization':cross_name if cross is not None else None,
            'channel_summary':channel_summary,'polarization_ratio_change':paired_ratio,
            'series':series,'timing':timing}
