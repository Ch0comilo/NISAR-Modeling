"""Frozen descriptive retrieval against an imperfect optical transition proxy."""
import numpy as np
import torch
from scipy.spatial import cKDTree
from .analysis import measured_db,physical_statistics,elapsed_days
from .transformer import infer
from .dynamic_view import controlled_view
from .flood_signatures import reference_groups


def coarsen(cube,reference,posting=160):
    spacing=float(cube['x'][1]-cube['x'][0]);factor=int(round(posting/spacing))
    if factor<1 or not np.isclose(factor*spacing,posting) or not np.isclose(cube['y'][0]-cube['y'][1],spacing):raise ValueError('Explicit integer square-power aggregation is required')
    pols=list(map(str,cube['polarizations']))
    if not {'HH','HV'}<=set(pols):raise ValueError('Frozen representation probe requires measured HH/HV')
    ids=[pols.index('HH'),pols.index('HV')];T=cube['power'].shape[0];H,W=cube['power'].shape[-2:]
    if H%factor or W%factor:raise ValueError('No silent edge cropping in representation comparison')
    h,w=H//factor,W//factor
    def blocks(a):return a.reshape(*a.shape[:-2],h,factor,w,factor)
    power=blocks(cube['power'][:,ids]).mean(axis=(-3,-1))
    valid=blocks(cube['valid'][:,ids].astype(bool)).all(axis=(-3,-1))
    coarse={**cube,'power':power,'valid':valid,'polarizations':np.array(['HH','HV']),
            'x':cube['x'].reshape(w,factor).mean(1),'y':cube['y'].reshape(h,factor).mean(1)}
    r={}
    for epoch in ['pre','post']:
        coverage=reference[epoch+'_valid_coverage']
        coarse_coverage=blocks(coverage).mean(axis=(-3,-1));r[epoch+'_valid_coverage']=coarse_coverage
        for key in ['open_class_fraction','partial_class_fraction']:
            value=reference[epoch+'_'+key]
            numerator=blocks(np.where((coverage>0)&np.isfinite(value),value*coverage,0)).mean(axis=(-3,-1))
            r[epoch+'_'+key]=np.divide(numerator,coarse_coverage,out=np.full((h,w),np.nan),where=coarse_coverage>0)
    return coarse,r


def extract(cube,reference,baseline,assessment,checkpoint):
    coarse,r=coarsen(cube,reference);db=measured_db(coarse);dates=coarse['dates'].astype(str)
    before=np.flatnonzero((dates>=baseline['start_date'])&(dates<=baseline['end_date']))
    after=np.flatnonzero((dates>=assessment['start_date'])&(dates<=assessment['end_date']))
    if not len(before) or not len(after):raise ValueError('Probe needs observed acquisitions in both windows')
    full=np.isfinite(db).all(axis=(0,1));labels=reference_groups(r);labels[~full]=255
    # Work only on complete trajectories to make both methods' support identical.
    coarse['valid']=coarse['valid']&full[None,None]
    base=np.full(db.shape[1:],np.nan);mad=np.full_like(base,np.nan)
    base[:,full]=np.median(db[before][:,:,full],axis=0)
    mad[:,full]=1.4826*np.median(abs(db[before][:,:,full]-base[:,full]),axis=0)
    delta=db[after[0]]-base;stat=physical_statistics(db,elapsed_days(dates))
    physical=np.stack([base[0],base[1],delta[0],delta[1],mad[0],mad[1],
                       stat['slope_db_per_day'][0],stat['slope_db_per_day'][1]],axis=-1)
    learned=infer(coarse,checkpoint)
    if learned['status']!='available':raise ValueError('Probe checkpoint domain is unsupported: '+learned['reason'])
    control=controlled_view(coarse,checkpoint,learned['state'])
    feature=control['state'][after[0]]-control['state'][before[-1]]
    valid=np.isin(labels,[0,1,2])&np.isfinite(physical).all(-1)&np.isfinite(feature).all(-1)
    return {'physical':physical[valid],'learned':feature[valid],'labels':labels[valid],
            'support':{'grid_cells':int(full.size),'complete_measured_cells':int(full.sum()),
                       'joint_unambiguous_reference_cells':int(valid.sum()),
                       'class_counts':{str(k):int((labels[valid]==k).sum()) for k in [0,1,2]},
                       'baseline_dates':len(before),'assessment_dates':len(after),
                       'posting_m':160,'support_scope':'Identical complete HH/HV trajectories and strict optical transition groups for both methods'}}


def fit_knn(features,labels,k=5):
    center=np.mean(features,axis=0);scale=np.maximum(np.std(features,axis=0),.0001)
    return {'center':center,'scale':scale,'tree':cKDTree((features-center)/scale),
            'labels':np.asarray(labels),'k':min(k,len(labels))}


def predict_knn(model,features):
    _,indices=model['tree'].query((features-model['center'])/model['scale'],k=model['k'])
    if indices.ndim==1:indices=indices[:,None]
    neighbors=model['labels'][indices]
    # A fixed smallest-code tie rule; no threshold or label fitting on tests.
    votes=np.stack([(neighbors==code).sum(1) for code in [0,1,2]],axis=1)
    return votes.argmax(1)


def score(labels,prediction):
    counts=np.zeros((3,3),dtype=int)
    for truth,pred in zip(labels,prediction):counts[int(truth),int(pred)]+=1
    recalls=[float(counts[i,i]/counts[i].sum()) if counts[i].sum() else None for i in range(3)]
    precisions=[float(counts[i,i]/counts[:,i].sum()) if counts[:,i].sum() else None for i in range(3)]
    available=[x for x in recalls if x is not None]
    return {'confusion_matrix_true_rows_predicted_columns':counts.tolist(),'classes':[0,1,2],
            'recall_by_class':recalls,'precision_by_class':precisions,
            'mean_recall_over_present_reference_classes':float(np.mean(available)) if available else None,
            'present_reference_classes':int(len(available)),
            'reference_nonwater_predicted_new_water_fraction':float(counts[0,1]/counts[0].sum()) if counts[0].sum() else None,
            'limitation':'Optical-product transition agreement on selected complete support; not validated event accuracy, physical-behavior truth or a calibrated probability'}
