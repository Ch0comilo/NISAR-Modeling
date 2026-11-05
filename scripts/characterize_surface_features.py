"""Build measured baseline fingerprints and sampling-matched analog reports."""
from pathlib import Path
import csv
import hashlib
import json
import argparse

import numpy as np
import torch

from surface_dynamics.analysis import measured_db
from surface_dynamics.embedding_atlas import aggregate_hh_hv,case_inventory
from surface_dynamics.io import load_cube
from surface_dynamics.surface_features import fingerprint,pixel_summary,analog_vector
from surface_dynamics.behavior_analogs import matched_window,sampling,retrieve
from surface_dynamics.dynamic_view import constant_control_states
from surface_dynamics.transformer import sequences,collate,model_from_checkpoint
from surface_dynamics.flood import load_reference
from surface_dynamics.flood_signatures import reference_groups,match_controls,spatial_contrast
from surface_dynamics.representation_probe import coarsen

ROOT=Path(__file__).resolve().parents[1]


def write(path,data):
    Path(path).write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf8')


def windows(case,dates,fraction):
    dates=np.array(dates).astype(str)
    if 'baseline_start' in case:
        baseline={'start_date':case['baseline_start'],'end_date':case['baseline_end']}
        assessment={'start_date':case['assessment_start'],'end_date':case['assessment_end']}
        scope='Declared existing characterization windows; context does not supply an event label'
    else:
        cut=min(len(dates)-1,max(3,int(len(dates)*fraction)))
        baseline={'start_date':dates[0],'end_date':dates[cut-1]}
        assessment={'start_date':dates[cut],'end_date':dates[-1]}
        scope='Generic historical prefix comparison; not an independently verified pre-event baseline'
    return baseline,assessment,scope


def reference_summary(original,cube,path,feature,config,assessment_start):
    groups=np.full(cube['power'].shape[-2:],255,dtype='uint8');dates=cube['dates'].astype(str)
    before=feature['before'];after=feature['after']
    if not path.with_name('reference_pair.npz').exists():
        return groups,{'status':'no_water_reference','phenomenon_accuracy':None}
    ref,meta=load_reference(path.with_name('reference_pair.npz'),original,dates[before[-1]],dates[after[0]],assessment_start)
    if ref is None:return groups,{'status':'temporally_ineligible_reference','phenomenon_accuracy':None}
    _,ref=coarsen(original,ref);groups=reference_groups(ref)
    support=feature['maps']['common_support'];groups[~support]=255
    maps=feature['maps'];policy=config['reference_comparison']
    controls,positive,matching=match_controls(maps['baseline_db'],groups==1,groups==0,policy['control_tolerance_db'])
    contrast_policy={'spatial_block_pixels':policy['bootstrap_block_pixels'],'bootstrap_draws':policy['bootstrap_draws'],
        'bootstrap_seed':policy['bootstrap_seed'],'minimum_blocks_per_group':policy['minimum_blocks_per_group']}
    contrasts={}
    for name,value in [('co_change_db',maps['first_change_db'][0]),('cross_change_db',maps['first_change_db'][1]),
                       ('first_ratio_change_db',maps['first_ratio_change_db']),('local_co_contrast_db',maps['local_co_contrast_db'])]:
        contrasts[name]=spatial_contrast(value,positive,controls,contrast_policy)
    stats={}
    for code in [0,1,2,3,4,255]:
        mask=support&(groups==code);n=int(mask.sum())
        stats[str(code)]={'cells':n,'first_co_change_median_db':float(np.median(maps['first_change_db'][0][mask])) if n else None,
            'first_cross_change_median_db':float(np.median(maps['first_change_db'][1][mask])) if n else None,
            'first_baseline_departure_fraction':float((maps['first_scaled_departure'][mask]>config['baseline']['departure_scale_multiple']).mean()) if n and feature['quality']['diagnostic_available'] else None}
    return groups,{'status':'independent_optical_product_comparison','groups':stats,'matching':matching,
        'matched_feature_contrasts':contrasts,'phenomenon_accuracy':None,
        'limitation':'Water product transitions are imperfect independent evidence. Reference-stable nonwater is not verified normal surface dynamics. Contrasts use all eligible 160 m common cells, not the sparse analog sample.'}


def manifest(output):
    references={}
    for case in case_inventory(ROOT):
        directory=(ROOT/case['cube_path']).parent
        for p in [directory/'reference_pair.npz',directory/'reference_pair.json']:
            if p.exists():references[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    ref_provenance={'scope':'Independent optical source snapshot recorded after feature extraction; never used to fit scaling, encoder, candidate gates or distances','files':references}
    if (output/'reference_provenance.json').exists() and json.loads((output/'reference_provenance.json').read_text())!=ref_provenance:
        raise ValueError('Independent reference inputs changed; preserve the earlier comparison')
    write(output/'reference_provenance.json',ref_provenance)
    names=['configs/surface_features.json','scripts/characterize_surface_features.py','scripts/verify_surface_features.py',
        'src/surface_dynamics/surface_features.py','src/surface_dynamics/behavior_analogs.py','src/surface_dynamics/surface_features_render.py']
    write(output/'execution_provenance.json',{'files':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}})
    items=[{'href':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.iterdir())
           if p.is_file() and p.name not in ['artifacts.json','validation.json']]
    write(output/'artifacts.json',items)


def run(output,render_only=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    config=json.loads((ROOT/'configs/surface_features.json').read_text())
    if render_only:
        from surface_dynamics.surface_features_render import render
        render(output);manifest(output);return
    points=json.loads((ROOT/'products/representation_umap/points.json').read_text())
    sources=['configs/surface_features.json','configs/global_v2.json','configs/global_cases.json',
        'configs/flood_pilot.json','configs/characterization_v05.json','src/surface_dynamics/surface_features.py',
        'src/surface_dynamics/behavior_analogs.py','src/surface_dynamics/embedding_atlas.py',
        'src/surface_dynamics/temporal_v2.py','src/surface_dynamics/dynamic_view.py','src/surface_dynamics/transformer.py',
        'src/surface_dynamics/io.py','src/surface_dynamics/analysis.py','products/representation_umap/points.json',
        'models/global_explorer_v2/masked_temporal_transformer.pt']
    sources.extend(c['cube_path'] for c in case_inventory(ROOT))
    freeze={'protocol':config,'scope':'Fixed before this new comparison, using previously consumed exploratory scenes; no fresh evaluation claim',
            'files':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    freeze_path=output/'protocol_freeze.json'
    if freeze_path.exists() and json.loads(freeze_path.read_text())!=freeze:
        raise ValueError('Feature protocol/source/input changed: use a new output directory')
    write(freeze_path,freeze)
    checkpoint=ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt'
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if saved.get('architecture_id')!='visible_normalized_residual_transformer_v2' or saved['posting_m']!=160:
        raise ValueError('Unexpected frozen encoder domain')
    model=model_from_checkpoint(saved).eval();torch.set_num_threads(4)
    rows=[];admission=[];records=[];ap=[];al=[];am=[];observed=[]
    references={}
    for case in case_inventory(ROOT):
        selected=[r for r in points if r['case_id']==case['id']]
        if not selected:continue
        path=ROOT/case['cube_path'];original=load_cube(path);cube=aggregate_hh_hv(original)
        baseline,assessment,scope=windows(case,cube['dates'],config['global_prefix_fraction'])
        feature=fingerprint(cube,baseline,assessment,config['baseline'])
        feature['comparison_scope']=scope;maps=feature['maps']
        np.savez_compressed(output/(case['id']+'_maps.npz'),**maps,x=cube['x'],y=cube['y'],epsg=cube['epsg'],dates=cube['dates'])
        # Attach independent evidence after extracting baseline features. It is
        # never supplied to normalization, encoder, candidate gates or distances.
        groups,ref=reference_summary(original,cube,path,feature,config,assessment['start_date']);references[case['id']]=ref
        np.savez_compressed(output/(case['id']+'_reference.npz'),groups=groups)
        before=np.array(feature['before']);after=np.array(feature['after'])
        ids=np.array([r['row']*cube['power'].shape[-1]+r['column'] for r in selected])
        rr=np.array([r['row'] for r in selected]);cc=np.array([r['column'] for r in selected])
        window=matched_window(cube['dates'],before,after) if 'baseline_start' in case else matched_window(cube['dates'])
        info={'case_id':case['id'],'comparison_scope':scope,'baseline':baseline,'assessment':assessment,
              'quality':feature['quality'],'sampled_points':len(selected),'analog_status':'six_date_window_available' if window is not None else 'insufficient_three_before_three_after',
              'analog_dates':cube['dates'][window].astype(str).tolist() if window is not None else None}
        admission.append(info);records.append({**info,'reference':ref})
        db=measured_db(cube)
        for j,r in enumerate(selected):
            point={**{k:r[k] for k in ['point_id','case_id','row','column','longitude','latitude','role']},
                'comparison_scope':scope,'full_record_dates':cube['dates'].astype(str).tolist(),
                'hh_db':db[:,0,rr[j],cc[j]].tolist(),'hv_db':db[:,1,rr[j],cc[j]].tolist(),
                'features':pixel_summary(feature,rr[j],cc[j]),'water_reference_code':int(groups[rr[j],cc[j]]),
                'analog_index':None}
            rows.append(point)
        if window is not None:
            short={**cube,'power':cube['power'][window],'valid':cube['valid'][window],'dates':cube['dates'][window]}
            b={'start_date':str(short['dates'][0]),'end_date':str(short['dates'][2])}
            a={'start_date':str(short['dates'][3]),'end_date':str(short['dates'][5])}
            short_features=fingerprint(short,b,a,config['baseline']);physical=analog_vector(short_features,rr,cc)
            x,p,t=sequences(short);batch=collate([(x[i],p[i],t) for i in ids],np.zeros(4),np.ones(4))
            with torch.no_grad():
                _,state=model(*batch);controlled=(state-constant_control_states(model,*batch)).numpy()
            learned=controlled[:,3]-controlled[:,2]
            ap.append(physical);al.append(learned);observed.append(db[window][:,:,rr,cc].transpose(2,0,1))
            offset=len(am)
            for j,r in enumerate(selected):
                am.append({**{k:r[k] for k in ['point_id','case_id','longitude','latitude']},'sampling':sampling(short['dates'])})
                rows[len(rows)-len(selected)+j]['analog_index']=offset+j
        print('FEATURE_SUPPORT',json.dumps(info),flush=True)
    physical=np.concatenate(ap);learned=np.concatenate(al);observed=np.concatenate(observed)
    retrieval,scalings,calibration=retrieve(physical,learned,am,config['analog'])
    write(output/'points.json',rows);write(output/'analog_metadata.json',am);write(output/'analogs.json',retrieval)
    write(output/'calibration.json',calibration)
    write(output/'scaling.json',{k:{n:v.tolist() if isinstance(v,np.ndarray) else v for n,v in a.items()} for k,a in scalings.items()})
    np.savez_compressed(output/'analog_features.npz',physical=physical,learned=learned,observed_db=observed)
    write(output/'admission.json',admission);write(output/'cases.json',records)
    summary={'experiment_id':config['experiment_id'],'scene_count':len(admission),'sampled_histories':len(rows),
        'six_date_analog_histories':len(am),'six_date_analog_scenes':len(set(r['case_id'] for r in am)),
        'baseline_states':{s:sum(r['features']['state']==s for r in rows) for s in ['within_observed_baseline_bounds','outside_observed_baseline_bounds','insufficient_baseline','unsupported']},
        'methods':{},'reference_comparisons':references,
        'phenomenon_classifier_trained':False,'normal_probability':None,'checkpoint_unchanged':True,
        'limits':['All scenes were consumed earlier. No new held-out accuracy claim.',
            'Observed baselines and the nine-region reference library are not verified normal or event-free.',
            'Median/MAD bands are transparent diagnostics, not calibrated confidence intervals.',
            'Fixed common cells and old label-blind sparse sampling restrict the population. Pixel counts are correlated.',
            'Six-date learned context is retrospective and includes assessment dates. It never defines the local baseline band.',
            'Geographic, seasonal, terrain/incidence and land-cover differences can remain after timing matching.',
            'Optical-water stability is not all-purpose normality; no fire, landslide, crop or earthquake truth is supplied.']}
    for method in ['physical','learned','hybrid']:
        items=[r['methods'][method] for r in retrieval if method in r['methods']]
        accepted=[i for i in items if i['neighbors']]
        summary['methods'][method]={'states':{s:sum(i['status']==s for i in items) for s in ['within_development_distance_envelope','no_close_cross_region_analog','insufficient_sampling_matched_regions']},
            'median_unrestricted_nearest_span_difference_days':float(np.median([i['unrestricted_span_difference_days'] for i in accepted])) if accepted else None,
            'median_matched_nearest_span_difference_days':float(np.median([i['neighbors'][0]['span_difference_days'] for i in accepted])) if accepted else None,
            'calibration':calibration[method]}
    write(output/'summary.json',summary)
    with (output/'point_features.csv').open('w',newline='',encoding='utf8') as f:
        fields=['point_id','case_id','state','first_hh_change_db','first_hv_change_db','maximum_scaled_departure','departure_fraction','ratio_change_db','local_co_contrast_db','normal_probability']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            v=r['features'];writer.writerow({'point_id':r['point_id'],'case_id':r['case_id'],'state':v['state'],
                'first_hh_change_db':v['first_change_db'][0],'first_hv_change_db':v['first_change_db'][1],
                'maximum_scaled_departure':v['maximum_scaled_departure'],'departure_fraction':v['observed_departure_fraction'],
                'ratio_change_db':v['cross_minus_co_ratio_change_db'],'local_co_contrast_db':v['local_co_contrast_db'],'normal_probability':None})
    from surface_dynamics.surface_features_render import render
    render(output);manifest(output)
    print('SURFACE_FEATURES_COMPLETE',json.dumps({k:summary[k] for k in ['scene_count','sampled_histories','six_date_analog_histories','baseline_states']}),flush=True)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'products/surface_features')
    parser.add_argument('--render-only',action='store_true');args=parser.parse_args();run(args.output,args.render_only)
