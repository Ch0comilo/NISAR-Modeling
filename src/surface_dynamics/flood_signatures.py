"""Interpretable measured signatures and proxy comparisons, no flood classifier."""
from pathlib import Path
import json
import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import label
from .analysis import measured_db,supported_reduction

POLICY=json.loads((Path(__file__).parent/'schemas/flood-policy.json').read_text())


def reference_groups(reference,policy=POLICY):
    pre=reference['pre_open_class_fraction'];post=reference['post_open_class_fraction']
    pp=reference['pre_partial_class_fraction'];pq=reference['post_partial_class_fraction']
    valid=(reference['pre_valid_coverage']>=policy['reference_valid_coverage_min'])&(reference['post_valid_coverage']>=policy['reference_valid_coverage_min'])
    valid &= np.isfinite(pre)&np.isfinite(post)&np.isfinite(pp)&np.isfinite(pq)
    before=pre>=policy['reference_open_class_share_min'];after=post>=policy['reference_open_class_share_min']
    dry_before=(pre<=policy['reference_dry_open_share_max'])&(pp<=policy['reference_dry_partial_share_max'])
    dry_after=(post<=policy['reference_dry_open_share_max'])&(pq<=policy['reference_dry_partial_share_max'])
    groups=np.full(pre.shape,255,dtype='uint8');groups[valid]=4
    groups[valid&dry_before&dry_after]=0
    groups[valid&dry_before&after]=1
    groups[valid&before&after]=2
    groups[valid&before&dry_after]=3
    return groups


def match_controls(baseline_db,positive,dry,tolerance_db=2.):
    """Unique nearest neighbors using *baseline only*, never post-event SAR."""
    features=baseline_db.reshape(baseline_db.shape[0],-1).T
    valid=np.isfinite(features).all(1);positive_ids=np.flatnonzero(positive.ravel()&valid);dry_ids=np.flatnonzero(dry.ravel()&valid)
    matched=np.zeros(positive.shape,bool);matched_positive=np.zeros_like(matched)
    if not len(positive_ids) or not len(dry_ids):return matched,matched_positive,{'positive_pixels':len(positive_ids),'matched_control_pixels':0,'matched_positive_pixels':0,'matched_fraction':0. if len(positive_ids) else None}
    tree=cKDTree(features[dry_ids]);distances,neighbors=tree.query(features[positive_ids],k=min(16,len(dry_ids)))
    if distances.ndim==1:distances=distances[:,None];neighbors=neighbors[:,None]
    used=set();differences=[]
    for row in np.argsort(distances[:,0],kind='stable'):
        for neighbor in neighbors[row]:
            idx=int(dry_ids[int(neighbor)])
            if idx not in used and np.all(abs(features[idx]-features[positive_ids[row]])<=tolerance_db):
                used.add(idx);matched_positive.ravel()[positive_ids[row]]=True
                differences.append(features[positive_ids[row]]-features[idx]);break
    matched.ravel()[list(used)]=True
    return matched,matched_positive,{'positive_pixels':len(positive_ids),'matched_control_pixels':len(used),'matched_positive_pixels':int(matched_positive.sum()),'matched_fraction':len(used)/len(positive_ids),
                    'baseline_mean_positive_minus_control_db_by_channel':np.mean(differences,axis=0).tolist() if differences else None,
                    'criterion':'unique baseline co-/cross-pol levels within declared dB tolerance; at most 16 nearest candidates; contrast uses matched positives only; not causal matching on all confounders'}


def spatial_contrast(delta,positive,control,policy=POLICY):
    valid=np.isfinite(delta);a=positive&valid;b=control&valid
    if not a.any() or not b.any():return {'status':'unavailable','reason':'Both comparison groups require measured support'}
    result={'status':'descriptive_only','positive_pixels':int(a.sum()),'control_pixels':int(b.sum()),
            'positive_mean_db':float(delta[a].mean()),'control_mean_db':float(delta[b].mean()),
            'positive_median_db':float(np.median(delta[a])),'control_median_db':float(np.median(delta[b])),
            'mean_contrast_db':float(delta[a].mean()-delta[b].mean()),'spatial_resampling_95_interval_db':None}
    h,w=delta.shape;size=policy['spatial_block_pixels'];blocks=[]
    for row in range(0,h,size):
        for col in range(0,w,size):
            sub=delta[row:row+size,col:col+size];aa=a[row:row+size,col:col+size];bb=b[row:row+size,col:col+size]
            if aa.any() or bb.any():blocks.append([float(sub[aa].sum()),int(aa.sum()),float(sub[bb].sum()),int(bb.sum())])
    blocks=np.array(blocks);na=int((blocks[:,1]>0).sum());nb=int((blocks[:,3]>0).sum())
    result.update(positive_blocks=na,control_blocks=nb)
    if min(na,nb)<policy['minimum_blocks_per_group']:
        result['reason']='Too few spatial blocks for the declared resampling interval';return result
    rng=np.random.default_rng(policy['bootstrap_seed']);draws=blocks[rng.integers(0,len(blocks),size=(policy['bootstrap_draws'],len(blocks)))].sum(1)
    ok=(draws[:,1]>0)&(draws[:,3]>0)
    contrast=draws[ok,0]/draws[ok,1]-draws[ok,2]/draws[ok,3]
    result.update(status='conditional_spatial_resampling',spatial_resampling_95_interval_db=np.quantile(contrast,[.025,.975]).tolist(),valid_resampling_draws=int(ok.sum()),
                  interval_limit='Conditional resampling of fixed spatial blocks; spatial independence, measurement errors, reference errors and causal identification are not established')
    return result


def proxy_agreement(candidate,groups,support):
    # All unambiguous non-new-water transitions are negatives. Mixed/partial
    # cells remain unknown rather than being silently counted as dry land.
    scored=support&np.isin(groups,[0,1,2,3]);truth=groups==1
    tp=int((scored&candidate&truth).sum());fp=int((scored&candidate&~truth).sum())
    fn=int((scored&~candidate&truth).sum());tn=int((scored&~candidate&~truth).sum())
    ratio=lambda n,d:n/d if d else None
    return {'comparison_kind':'independent_optical_product_agreement_not_ground_truth_accuracy',
            'scored_pixels':int(scored.sum()),'true_positive_proxy_pixels':tp,'false_positive_proxy_pixels':fp,
            'false_negative_proxy_pixels':fn,'true_negative_proxy_pixels':tn,
            'precision_against_proxy':ratio(tp,tp+fp),'recall_against_proxy':ratio(tp,tp+fn),
            'intersection_over_union_against_proxy':ratio(tp,tp+fp+fn),
            'reference_stable_nonwater_radar_flag_rate':ratio(int((support&candidate&(groups==0)).sum()),int((support&(groups==0)).sum())),
            'reference_stable_open_water_radar_flag_rate':ratio(int((support&candidate&(groups==2)).sum()),int((support&(groups==2)).sum())),
            'reference_water_loss_radar_flag_rate':ratio(int((support&candidate&(groups==3)).sum()),int((support&(groups==3)).sum())),
            'unscored_reference_or_mixed_pixels':int((support&~np.isin(groups,[0,1,2,3])).sum())}


def signal_intervals(series,dates,baseline_indices,assessment_indices,direction,policy=POLICY):
    threshold=policy['co_pol_change_db'];assessment=list(assessment_indices)
    changed=[i for i in assessment if np.isfinite(series[i]) and direction*series[i]>=threshold]
    if not changed:return {'first_change_interval':None,'observed_changed_acquisitions':0,'observed_change_span_days':None,'signal_return_interval':None}
    first=changed[0];reference_like=[i for i in range(first) if np.isfinite(series[i]) and abs(series[i])<threshold]
    lower=str(dates[reference_like[-1]]) if reference_like else None
    span=int((np.datetime64(str(dates[changed[-1]]))-np.datetime64(str(dates[first])))/np.timedelta64(1,'D'))
    return_interval=None
    for j,i in enumerate(assessment):
        if i<=first:continue
        confirmed=assessment[j:j+policy['signal_return_confirming_acquisitions']]
        if len(confirmed)==policy['signal_return_confirming_acquisitions'] and all(np.isfinite(series[k]) and abs(series[k])<=policy['signal_return_tolerance_db'] for k in confirmed):
            earlier=[k for k in changed if k<i]
            return_interval={'after':str(dates[earlier[-1]]),'by':str(dates[i]),'confirmation_date':str(dates[confirmed[-1]]),'interpretation':'Observed radar signal return, not confirmed hydrologic or ecosystem recovery'};break
    return {'first_change_interval':{'after':lower,'by':str(dates[first]),'exact_event_date_estimated':False},
            'observed_changed_acquisitions':len(changed),'observed_assessment_acquisitions':int(np.isfinite(series[assessment]).sum()),
            'observed_change_span_days':span,'continuous_duration_inferred':False,'signal_return_interval':return_interval,'return_right_censored':return_interval is None}


def characterize(cube,baseline_start,baseline_end,assessment_start,assessment_end,reference=None,policy=POLICY):
    db=measured_db(cube);dates=np.asarray(cube['dates']).astype(str)
    before=np.flatnonzero((dates>=baseline_start)&(dates<=baseline_end));after=np.flatnonzero((dates>=assessment_start)&(dates<=assessment_end))
    if not len(before) or not len(after):return {'status':'no_data','reason':'Both windows require actual supported acquisitions'}
    pols=list(map(str,cube['polarizations']));co=pols.index('HH') if 'HH' in pols else pols.index('VV') if 'VV' in pols else None
    if co is None:return {'status':'no_data','reason':'A measured co-polarized channel is required'}
    cross_name='HV' if pols[co]=='HH' else 'VH';cross=pols.index(cross_name) if cross_name in pols else None
    target=int(after[0]);baseline=supported_reduction(db[before],np.nanmedian);count=np.isfinite(db[before]).sum(0)
    minimum=min(len(before),max(policy['minimum_robust_baseline_dates'],int(np.ceil(len(before)*policy['baseline_support_fraction']))))
    baseline=np.where(count>=minimum,baseline,np.nan);change=db-baseline[None];delta=change[target]
    support=np.isfinite(delta[co])
    if cross is not None:support &= np.isfinite(delta[cross])
    if support.sum()<8:return {'status':'no_data','reason':'Insufficient common baseline/assessment measured support'}
    robust=supported_reduction(abs(db[before]-baseline),np.nanmedian)*1.4826
    robust=np.where(count>=policy['minimum_robust_baseline_dates'],robust,np.nan)
    scaled=delta/np.maximum(robust,policy['robust_scale_floor_db'])
    dark=support&(delta[co]<=-policy['co_pol_change_db'])
    if cross is not None:dark &= delta[cross]<=-policy['cross_pol_darkening_db']
    bright=support&(delta[co]>=policy['co_pol_change_db'])
    valid_after=np.isfinite(change[after,co]);n_after=valid_after.sum(0)
    frequency=lambda mask:np.divide(mask.sum(0),n_after,out=np.full(n_after.shape,np.nan),where=n_after>0)
    groups=np.full(support.shape,255,dtype='uint8') if reference is None else reference_groups(reference,policy)
    groups[~support]=255;positive=support&(groups==1);dry=support&(groups==0)
    matching_channels=[co]+([cross] if cross is not None else [])
    matched,matched_positive,matching=match_controls(baseline[matching_channels],positive,dry,policy['control_match_tolerance_db'])
    contrast=spatial_contrast(delta[co],matched_positive,matched,policy)
    agreement=proxy_agreement(dark,groups,support)
    agreement['radar_comparator']='first assessment minus median of declared baseline acquisitions'
    last_delta=db[target]-db[before[-1]]
    last_support=np.isfinite(last_delta[co])
    last_dark=last_support&(last_delta[co]<=-policy['co_pol_change_db'])
    if cross is not None:
        last_support &= np.isfinite(last_delta[cross]);last_dark &= last_support&(last_delta[cross]<=-policy['cross_pol_darkening_db'])
    agreement['last_baseline_sensitivity']=proxy_agreement(last_dark,groups,support&last_support)
    agreement['last_baseline_sensitivity']['radar_comparator']='first assessment minus last baseline acquisition; same policy thresholds, not an optimized alternative'
    labels,nobjects=label(dark,np.array([[0,1,0],[1,1,1],[0,1,0]]));counts=np.bincount(labels.ravel())
    ids=[i for i in range(1,len(counts)) if counts[i]>=policy['minimum_object_pixels']]
    ids=sorted(ids,key=lambda i:(-counts[i],i));objects=[]
    for oid in ids[:policy['maximum_reported_objects']]:
        mask=labels==oid;series=np.array([np.median(change[t,co][mask&np.isfinite(change[t,co])]) if (mask&np.isfinite(change[t,co])).mean()>=.8*mask.mean() else np.nan for t in range(len(dates))])
        objects.append({'object_id':int(oid),'analysis_cells':int(mask.sum()),'median_co_pol_change_db':float(np.median(delta[co][mask])),
                        'new_open_water_reference_cells':int((mask&positive).sum()),'reference_classified_cells':int((mask&(groups!=255)).sum()),
                        'trajectory_change_db':[float(x) if np.isfinite(x) else None for x in series],
                        **signal_intervals(series,dates,before,after,-1,policy)})
    series=[]
    for name,mask in [('common_radar_support',support),('reference_new_open_water',positive),('reference_stable_nonwater',dry),('matched_reference_new_open_water',matched_positive),('matched_reference_stable_nonwater',matched)]:
        for i,date in enumerate(dates):
            for c,pol in enumerate(pols):
                values=change[i,c][mask&np.isfinite(change[i,c])]
                series.append({'date':date,'group':name,'polarization':pol,'valid_pixels':len(values),
                               'mean_change_db':float(values.mean()) if len(values) else None,'median_change_db':float(np.median(values)) if len(values) else None})
    new_median=float(np.median(delta[co][positive])) if positive.any() else None
    if reference is None:interpretation='insufficient_independent_reference'
    elif int(positive.sum())<policy['minimum_reference_group_pixels']:interpretation='no_resolved_new_open_water_group_in_reference_snapshots'
    elif new_median<=-policy['co_pol_change_db']:interpretation='radar_darkening_with_optical_open_water_change'
    elif new_median>=policy['co_pol_change_db']:interpretation='radar_brightening_with_optical_open_water_change'
    else:interpretation='optical_open_water_change_with_weak_or_mixed_radar_response'
    return {'status':'characterized','target_index':target,'baseline_indices':before,'assessment_indices':after,
            'baseline_db':baseline,'baseline_valid_count':count,'baseline_mad_db':robust,'change_db':change,'target_change_db':delta,'target_minus_last_baseline_db':last_delta,
            'robust_scaled_target_change':scaled,'comparison_support':support,'candidate_darkening':dark,'co_pol_brightening':bright,
            'darkening_observation_frequency':frequency(valid_after&(change[after,co]<=-policy['co_pol_change_db'])),
            'brightening_observation_frequency':frequency(valid_after&(change[after,co]>=policy['co_pol_change_db'])),
            'co_pol_valid_assessment_count':n_after,
            'reference_group':groups,'matched_control_mask':matched,'matched_positive_mask':matched_positive,'matching':matching,'matched_contrast':contrast,
            'proxy_agreement':agreement,'objects':objects,'object_labels':labels,'total_signature_objects':len(ids),
            'series':series,'interpretation_status':interpretation,'co_polarization':pols[co],
            'cross_polarization':cross_name if cross is not None else None,'co_index':co,'cross_index':cross}
