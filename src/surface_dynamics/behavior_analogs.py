"""Acquisition-matched retrieval with measured and frozen learned features.

The reference bank is a development collection, never a verified normal set.
Distances, envelopes and feature differences are descriptive, not probabilities.
"""
import numpy as np
from scipy.spatial.distance import cdist


def matched_window(dates,before=None,after=None):
    dates=np.asarray(dates)
    if before is None:
        if len(dates)<6:return None
        start=(len(dates)-6)//2
        return np.arange(start,start+6)
    before=np.asarray(before);after=np.asarray(after)
    if min(len(before),len(after))<3:return None
    return np.r_[before[-3:],after[:3]]


def sampling(dates):
    dates=np.array(dates,dtype='datetime64[D]')
    if len(dates)!=6 or not np.all(np.diff(dates)>np.timedelta64(0,'D')):
        raise ValueError('Exactly six increasing actual acquisitions are required')
    days=(dates-dates[0]).astype('timedelta64[D]').astype(float);gaps=np.diff(days)
    return {'span_days':float(days[-1]),'baseline_span_days':float(days[2]),
            'assessment_span_days':float(days[-1]-days[3]),'boundary_gap_days':float(gaps[2]),
            'normalized_gaps':(gaps/days[-1]).tolist(),'dates':dates.astype(str).tolist()}


def distance_km(a,b):
    lon1,lat1=np.radians([a['longitude'],a['latitude']]);lon2,lat2=np.radians([b['longitude'],b['latitude']])
    v=np.sin((lat2-lat1)/2)**2+np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
    return float(6371.0088*2*np.arcsin(np.sqrt(np.clip(v,0,1))))


def eligible(a,b,policy,match=True):
    if a['case_id']==b['case_id'] or distance_km(a,b)<policy['minimum_region_distance_km']:return False
    if not match:return True
    x,y=a['sampling'],b['sampling']
    ratio=lambda p,q:max(p,q)/min(p,q)
    return (ratio(x['span_days'],y['span_days'])<=policy['maximum_span_ratio']+1e-12 and
            ratio(x['baseline_span_days'],y['baseline_span_days'])<=policy['maximum_phase_span_ratio']+1e-12 and
            ratio(x['assessment_span_days'],y['assessment_span_days'])<=policy['maximum_phase_span_ratio']+1e-12 and
            ratio(x['boundary_gap_days'],y['boundary_gap_days'])<=policy['maximum_boundary_gap_ratio']+1e-12 and
            np.sum(abs(np.array(x['normalized_gaps'])-y['normalized_gaps']))<=policy['maximum_normalized_gap_l1']+1e-12)


def fit_scaling(features,metadata,reference_regions,floors):
    ids=np.array([i for i,r in enumerate(metadata) if r['case_id'] in reference_regions],dtype=int)
    if not len(ids):raise ValueError('No development reference features')
    cases=np.array([metadata[i]['case_id'] for i in ids]);names,counts=np.unique(cases,return_counts=True)
    weight=np.array([1/counts[np.flatnonzero(names==c)[0]] for c in cases]);weight/=weight.sum()
    x=np.asarray(features,dtype=float)[ids]
    if not np.isfinite(x).all():raise ValueError('Reference distance features must be finite')
    center=np.sum(x*weight[:,None],axis=0)
    scale=np.maximum(np.sqrt(np.sum((x-center)**2*weight[:,None],axis=0)),floors)
    return {'center':center,'scale':scale,'reference_indices':ids,'reference_regions':names.tolist(),
            'fit_scope':'Only sampled cells from nine previously trained development geographies; each scene has equal total scaling weight. Not a clean normal reference.'}


def weighted_quantile(values,cases,quantile):
    values=np.asarray(values);cases=np.asarray(cases);names,counts=np.unique(cases,return_counts=True)
    weight=np.array([1/counts[np.flatnonzero(names==c)[0]] for c in cases]);order=np.argsort(values,kind='stable')
    return float(np.interp(quantile,np.cumsum(weight[order])/weight.sum(),values[order]))


def retrieve(physical,learned,metadata,policy):
    """Same eligible library, normalization scope and units for all three methods."""
    if len(physical)!=len(learned) or len(metadata)!=len(physical):raise ValueError('Row support mismatch')
    scalings={'physical':fit_scaling(physical,metadata,policy['reference_regions'],policy['physical_scale_floors']),
              'learned':fit_scaling(learned,metadata,policy['reference_regions'],policy['learned_scale_floor'])}
    p=(physical-scalings['physical']['center'])/scalings['physical']['scale']
    l=(learned-scalings['learned']['center'])/scalings['learned']['scale']
    reference=scalings['physical']['reference_indices'];assert np.array_equal(reference,scalings['learned']['reference_indices'])
    wp,wl=policy['physical_weight'],policy['learned_weight']
    if min(wp,wl)<0 or not np.isclose(wp+wl,1):raise ValueError('Hybrid block weights must be nonnegative and sum to one')
    cases=list(dict.fromkeys(r['case_id'] for r in metadata))
    regional={c:next(r for r in metadata if r['case_id']==c) for c in cases}
    ref_cases=np.array([metadata[i]['case_id'] for i in reference])
    result=[{'point_id':r['point_id'],'methods':{}} for r in metadata]
    for case in cases:
        queries=np.array([i for i,r in enumerate(metadata) if r['case_id']==case])
        possible=[c for c in set(ref_cases) if eligible(regional[case],regional[c],policy)]
        unrestricted=[c for c in set(ref_cases) if eligible(regional[case],regional[c],policy,False)]
        target=reference[np.isin(ref_cases,unrestricted)]
        if not len(target):continue
        pd=cdist(p[queries],p[target])/np.sqrt(p.shape[1]);ld=cdist(l[queries],l[target])/np.sqrt(l.shape[1])
        hd=np.sqrt(wp*pd**2+wl*ld**2)
        candidates=np.array([metadata[i]['case_id'] for i in target])
        for method,dist in [('physical',pd),('learned',ld),('hybrid',hd)]:
            for q,i in enumerate(queries):
                raw=int(target[np.argmin(dist[q])])
                item={'eligible_reference_regions':sorted(possible),'eligible_reference_cells':int(np.isin(candidates,possible).sum()),
                      'unrestricted_nearest_id':metadata[raw]['point_id'],
                      'unrestricted_span_difference_days':abs(metadata[i]['sampling']['span_days']-metadata[raw]['sampling']['span_days']),
                      'status':'insufficient_sampling_matched_regions','neighbors':[]}
                if len(possible)>=policy['minimum_candidate_regions']:
                    best=[]
                    for region in sorted(possible):
                        local=np.flatnonzero(candidates==region);j=int(local[np.argmin(dist[q,local])])
                        best.append((float(dist[q,j]),int(target[j])))
                    best.sort(key=lambda t:(t[0],metadata[t[1]]['point_id']))
                    for value,j in best[:3]:
                        diff=p[i]-p[j];squared=diff**2/len(diff)
                        top=np.argsort(-squared,kind='stable')[:3]
                        physical_share=wp*np.mean(diff**2)/(wp*np.mean(diff**2)+wl*np.mean((l[i]-l[j])**2)) if method=='hybrid' and value>0 else None
                        item['neighbors'].append({'point_id':metadata[j]['point_id'],'case_id':metadata[j]['case_id'],
                            'distance':value,'physical_rms_distance':float(np.linalg.norm(diff)/np.sqrt(len(diff))),
                            'learned_rms_distance':float(np.linalg.norm(l[i]-l[j])/np.sqrt(l.shape[1])),
                            'region_distance_km':distance_km(metadata[i],metadata[j]),
                            'span_difference_days':abs(metadata[i]['sampling']['span_days']-metadata[j]['sampling']['span_days']),
                            'hybrid_squared_distance_physical_fraction':float(physical_share) if physical_share is not None else None,
                            'largest_physical_differences':[{'feature':policy['physical_features'][k],
                                'query_value':float(physical[i,k]),'reference_value':float(physical[j,k]),
                                'standardized_difference':float(diff[k]),'physical_squared_rms_contribution':float(squared[k])} for k in top]})
                    item['status']='distance_available'
                result[i]['methods'][method]=item
    calibration={}
    for method in ['physical','learned','hybrid']:
        admitted=[i for i in reference if result[i]['methods'].get(method,{}).get('neighbors')]
        if admitted:
            values=[result[i]['methods'][method]['neighbors'][0]['distance'] for i in admitted]
            cutoff=weighted_quantile(values,[metadata[i]['case_id'] for i in admitted],policy['distance_envelope_quantile'])
        else:cutoff=None
        calibration[method]={'distance_envelope':cutoff,'quantile':policy['distance_envelope_quantile'],
            'development_cells':len(admitted),'development_scenes':sorted(set(metadata[i]['case_id'] for i in admitted)),
            'scope':'Leave-own-region-out nearest distances in the development library; scene-balanced descriptive envelope, not a calibrated normality probability'}
        for r in result:
            item=r['methods'].get(method)
            if item is None or not item['neighbors']:continue
            item['distance_envelope']=cutoff
            item['status']='no_close_cross_region_analog' if cutoff is None or item['neighbors'][0]['distance']>cutoff else 'within_development_distance_envelope'
    return result,scalings,calibration
