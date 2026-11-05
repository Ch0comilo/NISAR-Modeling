"""Generate the shared reports and the separate unchanged flood-policy test."""
from pathlib import Path
import json
from surface_dynamics.characterization import process_characterization
from surface_dynamics.flood import process_flood

ROOT=Path(__file__).resolve().parents[1]


def request_for(case,base):
    request={'contract_version':'1.0','request_id':case['id']+'-surface-behavior',
             'theme':case.get('theme','floods'),'source':{'mode':'local_cube','cube_path':str(base/'cube.npz')},
             'baseline':{'start_date':case['baseline_start'],'end_date':case['baseline_end']},
             'assessment':{'start_date':case['assessment_start'],'end_date':case['assessment_end']}}
    context=case.get('context',case.get('event_context'))
    if context:request['context']=context
    if (base/'reference_pair.npz').exists():request['water_reference']={'mode':'local_optical_pair','path':str(base/'reference_pair.npz')}
    if (base/'disturbance_reference.npz').exists():request['disturbance_reference']=str(base/'disturbance_reference.npz')
    return request


def run():
    recipe=json.loads((ROOT/'configs/characterization_v05.json').read_text());old=json.loads((ROOT/'configs/flood_pilot.json').read_text())
    cases=[(c,ROOT/'products/flood_pilot/cases'/c['id'],'development') for c in old['cases']]
    cases.extend((c,ROOT/'products/characterization_v05/cases'/c['id'],c['role']) for c in recipe['cases'])
    inventory=[];benchmark=[]
    for case,base,role in cases:
        if not (base/'cube.npz').exists():
            inventory.append({'case_id':case['id'],'role':role,'status':'no_admitted_cube'});continue
        request=request_for(case,base)
        r=process_characterization(request,ROOT/'products/characterization_v05/runs'/case['id'])
        item={'case_id':case['id'],'role':role,'status':r['status'],'quality':r['quality'],
              'behavior':r['behavior'],'what_changed':r['what_changed'],'evidence':r['evidence']['status'],
              'artifacts':len(r['artifacts'])};inventory.append(item)
        print('CHARACTERIZATION_JOB',json.dumps(item),flush=True)
        if role in ['fresh_flood_context_test','candidate_nonwater_control']:
            flood={k:v for k,v in request.items() if k in ['contract_version','request_id','source','baseline','assessment','context']}
            if request.get('water_reference'):flood['reference']=request['water_reference']
            result=process_flood(flood,ROOT/'products/characterization_v05/flood_validation'/case['id'])
            benchmark.append({'case_id':case['id'],'role':role,'status':result['status'],
                 'signature_summary':result['signature_summary'],
                 'reference_evidence':{k:v for k,v in result['reference_evidence'].items() if k!='provenance'},
                 'proxy_comparison':result['proxy_comparison'],'matched_controls':result['matched_controls']})
            print('FRESH_FLOOD_POLICY',json.dumps(benchmark[-1]),flush=True)
    (ROOT/'products/characterization_v05/report_inventory.json').write_text(json.dumps(inventory,indent=2,allow_nan=False))
    (ROOT/'products/characterization_v05/flood_validation.json').write_text(json.dumps(benchmark,indent=2,allow_nan=False))


if __name__=='__main__':run()
