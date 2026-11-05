"""Emit full backend artifacts for the frozen candidate in thirteen regions."""
from pathlib import Path
import json
from surface_dynamics.engine import analyze
ROOT=Path(__file__).resolve().parents[1]
recipe=json.loads((ROOT/'configs/global_v2.json').read_text());summary=[]
for name in recipe['training_regions']+recipe['legacy_development_regions']+recipe['locked_test_regions']:
    request={'contract_version':'1.0','request_id':name+'-v2','theme':'explore',
             'source':{'mode':'local_cube','cube_path':str(ROOT/'products/global_v2/cubes'/f'{name}.npz')},
             'analyses':['physical','spectral','stochastic','transformer'],
             'transformer_checkpoint':str(ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt'),'stochastic_draws':256}
    result=analyze(request,ROOT/'products/global_v2/runs'/name)
    summary.append({'case_id':name,'status':result['status'],'dates':len(result['observations']['dates']),
                    'artifacts':len(result['artifacts']),'role':'training' if name in recipe['training_regions'] else 'fresh_test' if name in recipe['locked_test_regions'] else 'legacy_development'})
    print('GLOBAL_V2_JOB',json.dumps(summary[-1]),flush=True)
(ROOT/'products/global_v2/job_inventory.json').write_text(json.dumps(summary,indent=2))
from surface_dynamics.insar_stack import process_stack
for name,request_id in [('kumamoto','kumamoto-interval-dynamics'),('kumamoto_wide','kumamoto-wider-reference-sensitivity')]:
    output=ROOT/'products/insar_stacks'/name
    request={'contract_version':'1.0','request_id':request_id,'source':{'mode':'local_pairs','pair_paths':[str(output/'cache'/f'pair_{i}.npz') for i in range(3)]},'coherence_threshold':.35}
    result=process_stack(request,output)
    print('GLOBAL_V2_INSAR_STACK',name,result['status'],flush=True)
