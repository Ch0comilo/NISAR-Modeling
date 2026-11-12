"""Interpretable departures from a measured local baseline, not event labels.

All uncertainty bounds here are diagnostic median/MAD bands, not calibrated
probability intervals. An observed baseline is not assumed to be undisturbed.
"""
import json
from pathlib import Path
import warnings

import numpy as np
from .analysis import measured_db, elapsed_days
from .io import validate_cube

DEFAULT_POLICY = {'minimum_dates': 3, 'scale_floor_db': 1., 'departure_scale_multiple': 3.,
                  'minimum_departure_db': 3., 'neighborhood_pixels': 3, 'minimum_neighbors': 4}


def _neighbor(values, minimum, statistic):
    """Median or MAD of the eight surrounding cells, ignoring gaps; the cell itself is excluded."""
    height, width = values.shape
    padded = np.pad(values.astype(float), 1, constant_values=np.nan)
    ring = np.stack([padded[dy:dy + height, dx:dx + width]
                     for dy in range(3) for dx in range(3) if (dy, dx) != (1, 1)])
    enough = np.isfinite(ring).sum(axis=0) >= minimum
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # windows with no finite neighbor
        median = np.nanmedian(ring, axis=0)
        result = median if statistic == 'median' else 1.4826*np.nanmedian(abs(ring-median), axis=0)
    return np.where(enough, result, np.nan)


def from_db(db, dates, before, after, policy=None):
    """Compute maps on fixed common [date, co/cross, row, column] support."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    db = np.asarray(db, dtype=float); dates = np.array(dates, dtype='datetime64[D]')
    before = np.asarray(before, dtype=int); after = np.asarray(after, dtype=int)
    if db.ndim != 4 or db.shape[1] != 2 or len(dates) != len(db):
        raise ValueError('Expected [actual date, two named co/cross channels, y, x]')
    if not len(before) or not len(after) or len(np.unique(np.r_[before,after])) != len(before)+len(after):
        raise ValueError('Nonempty nonoverlapping baseline and assessment are required')
    if before.min() < 0 or after.max() >= len(db) or not np.all(np.diff(before)>0) or not np.all(np.diff(after)>0) or before[-1] >= after[0]:
        raise ValueError('Dates must be increasing with baseline before assessment')
    if not np.all(np.diff(dates)>np.timedelta64(0,'D')):
        raise ValueError('Unique increasing actual dates are required')
    if policy['neighborhood_pixels'] != 3 or policy['minimum_neighbors'] not in range(1,9):
        raise ValueError('This feature version uses the eight surrounding 3x3 cells')
    if min(policy['scale_floor_db'],policy['minimum_departure_db'],policy['departure_scale_multiple']) <= 0:
        raise ValueError('Positive diagnostic bounds are required')
    chosen = np.r_[before,after]; common = np.isfinite(db[chosen]).all(axis=(0,1))
    h,w = db.shape[-2:]; base = np.full((2,h,w),np.nan); mad = base.copy()
    postmedian = base.copy(); postmad = base.copy()
    base[:,common] = np.median(db[before][:,:,common],axis=0)
    mad[:,common] = 1.4826*np.median(abs(db[before][:,:,common]-base[:,common]),axis=0)
    postmedian[:,common] = np.median(db[after][:,:,common],axis=0)
    postmad[:,common] = 1.4826*np.median(abs(db[after][:,:,common]-postmedian[:,common]),axis=0)
    delta = db-base[None]
    scale = np.maximum(mad,policy['scale_floor_db'])
    band = np.maximum(policy['minimum_departure_db'],policy['departure_scale_multiple']*scale)
    enough = len(before) >= policy['minimum_dates']
    score = np.where(common[None,None] & enough,delta/scale[None],np.nan)
    bounds = np.where(common[None] & enough,band,np.nan)
    outside = (abs(delta[after])>bounds[None]).any(axis=1)&common[None]&enough
    count = np.where(common & enough,outside.sum(0),-1).astype('int16')
    frequency = np.divide(count,len(after),out=np.full((h,w),np.nan),where=count>=0)
    first = delta[after[0]]; assessment = postmedian-base
    ratio = db[:,1]-db[:,0]
    ratio_delta = np.full((h,w),np.nan);first_ratio=ratio_delta.copy()
    ratio_delta[common] = np.median(ratio[after][:,common],axis=0)-np.median(ratio[before][:,common],axis=0)
    first_ratio[common] = ratio[after[0]][common]-np.median(ratio[before][:,common],axis=0)
    steps = np.diff(db[chosen,0],axis=0); travel = np.sum(abs(steps),axis=0)
    concentration = np.divide(np.max(abs(steps),axis=0),travel,out=np.zeros((h,w)),where=travel>0)
    concentration[~common] = np.nan
    neighbor = _neighbor(first[0],policy['minimum_neighbors'],'median')
    local_contrast = first[0]-neighbor
    texture = _neighbor(np.where(common,db[after[0],0],np.nan),policy['minimum_neighbors'],'mad')-_neighbor(base[0],policy['minimum_neighbors'],'mad')
    local_contrast[~common]=np.nan;texture[~common]=np.nan
    maximum = np.full((h,w),np.nan)
    if enough: maximum[common]=np.max(abs(score[after][:,:,common]),axis=(0,1))
    first_max = np.full((h,w),np.nan)
    if enough: first_max[common]=np.max(abs(score[after[0]][:,common]),axis=0)
    state=np.full((h,w),255,dtype='uint8');state[common]=0
    if enough:
        state[common&(count==0)]=1;state[common&(count>0)]=2
    maps={'baseline_db':base,'baseline_mad_db':mad,'diagnostic_half_width_db':bounds,
          'first_change_db':first,'assessment_change_db':assessment,'assessment_mad_db':postmad,
          'standardized_change':score,'first_scaled_departure':first_max,'maximum_scaled_departure':maximum,
          'outside_baseline_count':count,'assessment_departure_mask':outside,'departure_frequency':frequency,'state_code':state,
          'ratio_change_db':ratio_delta,'first_ratio_change_db':first_ratio,'co_step_concentration':concentration,
          'boundary_co_step_db':db[after[0],0]-db[before[-1],0],
          'co_net_change_db':db[chosen[-1],0]-db[chosen[0],0],
          'neighbor_co_change_db':neighbor,'local_co_contrast_db':local_contrast,
          'local_co_spatial_mad_change_db':texture,'common_support':common}
    # Unsupported cells cannot acquire a zero-valued observation or score.
    for name in ['boundary_co_step_db','co_net_change_db']:
        maps[name]=np.where(common,maps[name],np.nan)
    return {'maps':maps,'dates':dates.astype(str).tolist(),'before':before.tolist(),'after':after.tolist(),
            'policy':policy,'quality':{'grid_cells':h*w,'fixed_common_cells':int(common.sum()),
                'baseline_dates':len(before),'assessment_dates':len(after),
                'baseline_span_days':int((dates[before[-1]]-dates[before[0]])/np.timedelta64(1,'D')),
                'diagnostic_available':enough,'seasonal_normality_established':False,
                'bounds':'Baseline median plus/minus max(3 dB, 3 times max(scaled MAD, 1 dB)) under the recorded policy; not a confidence interval'},
            'state_legend':{0:'insufficient_baseline',1:'within_observed_baseline_bounds',2:'outside_observed_baseline_bounds',255:'unsupported'}}


def fingerprint(cube,baseline,assessment,policy=None):
    """Reusable worker core for any compatible measured cube; no neural model."""
    validate_cube(cube); pols=list(map(str,cube['polarizations']))
    pair=['HH','HV'] if {'HH','HV'}<=set(pols) else ['VV','VH'] if {'VV','VH'}<=set(pols) else None
    if pair is None: raise ValueError('Named measured HH/HV or VV/VH channels are required')
    dates=cube['dates'].astype(str)
    before=np.flatnonzero((dates>=baseline['start_date'])&(dates<=baseline['end_date']))
    after=np.flatnonzero((dates>=assessment['start_date'])&(dates<=assessment['end_date']))
    result=from_db(measured_db(cube)[:,[pols.index(p) for p in pair]],dates,before,after,policy)
    result['polarizations']=pair;result['posting_m']=float(cube['x'][1]-cube['x'][0])
    result['quality']['neighborhood_width_m']=3*result['posting_m']
    result['quality']['neighborhood_height_m']=3*float(cube['y'][0]-cube['y'][1])
    return result


def pixel_summary(result,row,column):
    m=result['maps'];y,x=int(row),int(column)
    code=int(m['state_code'][y,x]);value=lambda a:float(a) if np.isfinite(a) else None
    if code==255:return {'state':'unsupported','normal_probability':None,'phenomenon_class':None}
    before=result['before'];after=result['after'];dates=result['dates'];policy=result['policy']
    enough=result['quality']['diagnostic_available']
    flags=[i for j,i in enumerate(after) if enough and m['assessment_departure_mask'][j,y,x]]
    first_interval=None
    if flags:
        first=flags[0];earlier=[i for i in before+after if i<first and
            np.all(abs(m['standardized_change'][i,:,y,x])*np.maximum(m['baseline_mad_db'][:,y,x],policy['scale_floor_db'])<=m['diagnostic_half_width_db'][:,y,x])]
        first_interval={'after':dates[earlier[-1]] if earlier else None,'by':dates[first],'exact_onset_inferred':False}
    return {'state':result['state_legend'][code], 'baseline_reference':'Observed local history, not independently verified normal conditions',
            'baseline_dates':[dates[i] for i in before],'assessment_dates':[dates[i] for i in after],
            'baseline_db':[value(v) for v in m['baseline_db'][:,y,x]],
            'baseline_scaled_mad_db':[value(v) for v in m['baseline_mad_db'][:,y,x]],
            'diagnostic_half_width_db':[value(v) for v in m['diagnostic_half_width_db'][:,y,x]],
            'first_change_db':[value(v) for v in m['first_change_db'][:,y,x]],
            'assessment_median_change_db':[value(v) for v in m['assessment_change_db'][:,y,x]],
            'first_scaled_departure':value(m['first_scaled_departure'][y,x]),
            'maximum_scaled_departure':value(m['maximum_scaled_departure'][y,x]),
            'outside_baseline_observations':int(m['outside_baseline_count'][y,x]) if enough else None,
            'supported_assessment_observations':len(after),'observed_departure_fraction':value(m['departure_frequency'][y,x]),
            'first_departure_interval':first_interval,'continuous_duration_inferred':False,
            'cross_minus_co_ratio_change_db':value(m['ratio_change_db'][y,x]),
            'cross_minus_co_ratio_first_change_db':value(m['first_ratio_change_db'][y,x]),
            'co_step_concentration':value(m['co_step_concentration'][y,x]),
            'local_co_contrast_db':value(m['local_co_contrast_db'][y,x]),
            'local_co_spatial_mad_change_db':value(m['local_co_spatial_mad_change_db'][y,x]),
            'normal_probability':None,'phenomenon_class':None,'causal_attribution':None}


def analog_vector(result,rows,columns):
    """Twelve recorded physical coordinates, in the fixed recipe order."""
    m=result['maps'];y=np.asarray(rows);x=np.asarray(columns)
    return np.stack([m['baseline_db'][0,y,x],m['baseline_db'][1,y,x],
        m['baseline_mad_db'][0,y,x],m['baseline_mad_db'][1,y,x],
        m['assessment_change_db'][0,y,x],m['assessment_change_db'][1,y,x],
        m['ratio_change_db'][y,x],m['boundary_co_step_db'][y,x],
        m['co_step_concentration'][y,x],m['departure_frequency'][y,x],
        m['assessment_mad_db'][0,y,x],m['co_net_change_db'][y,x]],axis=-1)
