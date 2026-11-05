"""Freeze source/input hashes before opening fresh proxy labels or scores."""
from pathlib import Path
import hashlib,json
import numpy as np
from surface_dynamics.io import load_cube
from surface_dynamics.flood import load_reference
from surface_dynamics.representation_probe import extract,fit_knn,predict_knn,score

ROOT=Path(__file__).resolve().parents[1]


def run():
    recipe=json.loads((ROOT/'configs/characterization_v05.json').read_text());protocol=recipe['representation_probe']
    old=json.loads((ROOT/'configs/flood_pilot.json').read_text())['cases']
    cases={c['id']:{**c,'base':ROOT/'products/flood_pilot/cases'/c['id']} for c in old}
    cases.update({c['id']:{**c,'base':ROOT/'products/characterization_v05/cases'/c['id']} for c in recipe['cases']})
    names=protocol['training_cases']+protocol['test_cases']
    files=[ROOT/'configs/characterization_v05.json',ROOT/'src/surface_dynamics/representation_probe.py',
           ROOT/'src/surface_dynamics/temporal_v2.py',ROOT/'src/surface_dynamics/dynamic_view.py',
           ROOT/'src/surface_dynamics/flood_signatures.py',ROOT/'src/surface_dynamics/analysis.py',
           ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt']
    for name in names:files.extend(cases[name]['base']/p for p in ['cube.npz','reference_pair.npz','reference_pair.json'])
    freeze={'protocol':protocol,'scope':'Frozen before fresh optical transition labels, probe comparisons and scores are opened',
            'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}}
    destination=ROOT/'products/characterization_v05/probe_freeze.json'
    if destination.exists():
        if json.loads(destination.read_text())!=freeze:raise ValueError('Frozen representation probe changed; do not overwrite a consumed evaluation')
    else:destination.write_text(json.dumps(freeze,indent=2))
    extracted={};admission=[]
    for name in names:
        c=cases[name];base=c['base']
        if not (base/'cube.npz').exists() or not (base/'reference_pair.npz').exists():
            admission.append({'case_id':name,'status':'missing_independent_input'});continue
        cube=load_cube(base/'cube.npz');dates=cube['dates'].astype(str)
        before=[d for d in dates if c['baseline_start']<=d<=c['baseline_end']]
        after=[d for d in dates if c['assessment_start']<=d<=c['assessment_end']]
        reference,meta=load_reference(base/'reference_pair.npz',cube,before[-1],after[0],c['assessment_start'])
        if reference is None:admission.append({'case_id':name,'status':'temporally_ineligible_reference'});continue
        e=extract(cube,reference,{'start_date':c['baseline_start'],'end_date':c['baseline_end']},
                  {'start_date':c['assessment_start'],'end_date':c['assessment_end']},
                  ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt')
        extracted[name]=e;admission.append({'case_id':name,'status':'evaluated' if len(e['labels']) else 'no_qualified_comparison_cells',**e['support']})
        print('PROBE_SUPPORT',json.dumps(admission[-1]),flush=True)
    rng=np.random.default_rng(protocol['seed']);training={key:[] for key in ['physical','learned','labels']}
    for name in protocol['training_cases']:
        if name not in extracted:continue
        e=extracted[name];selected=[]
        for label in protocol['label_groups']:
            ids=np.flatnonzero(e['labels']==label)
            selected.extend(rng.choice(ids,min(len(ids),protocol['maximum_pixels_per_case_class']),replace=False))
        for key in training:training[key].append(e[key][selected])
    report={'protocol_id':protocol['id'],'scope':protocol['scope'],'admission':admission,'cases':[],
            'status':'insufficient_training_classes','training_class_counts':{},
            'limitations':['Three development scenes supply proxy examples; the four new scenes remain region-level evaluation units.',
              'The frozen temporal encoder was trained on radar elsewhere, without these new scene labels. No new neural weights are fitted.',
              'Complete-support and optical-class filtering can bias the evaluated population. Cloudy/mixed cells remain outside the scored set.',
              'The learned feature uses bidirectional observed context; this is retrospective retrieval, not forecasting.',
              'A weak or absent reference class cannot establish phenomenon absence or global accuracy.']}
    if training['labels']:
        training={key:np.concatenate(value) for key,value in training.items()}
        report['training_class_counts']={str(c):int((training['labels']==c).sum()) for c in protocol['label_groups']}
        if min(report['training_class_counts'].values())>=protocol['minimum_training_class_pixels']:
            report['status']='completed_proxy_evaluation'
            models={key:fit_knn(training[key],training['labels'],protocol['neighbors']) for key in ['physical','learned']}
            for name in protocol['test_cases']:
                if name not in extracted or not len(extracted[name]['labels']):continue
                e=extracted[name];item={'case_id':name,'support':e['support'],'methods':{}}
                for key in models:item['methods'][key]=score(e['labels'],predict_knn(models[key],e[key]))
                report['cases'].append(item);print('PROBE_RESULT',json.dumps(item),flush=True)
    report['training_scaling_scope']='Same development samples; feature center/SD fitted on development only; identical test support and fixed k=5'
    path=ROOT/'products/characterization_v05/representation_probe.json';path.write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


if __name__=='__main__':run()
