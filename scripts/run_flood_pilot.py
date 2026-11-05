"""Run all measured pilot fixtures without authentication or network access."""
from pathlib import Path
import json
from surface_dynamics.flood import process_flood

ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    recipe=json.loads((ROOT/'configs/flood_pilot.json').read_text());summary=[]
    for case in recipe['cases']:
        base=ROOT/'products/flood_pilot/cases'/case['id']
        request={'contract_version':'1.0','request_id':case['id']+'-flood-evidence',
                 'source':{'mode':'local_cube','cube_path':str(base/'cube.npz')},
                 'baseline':{'start_date':case['baseline_start'],'end_date':case['baseline_end']},
                 'assessment':{'start_date':case['assessment_start'],'end_date':case['assessment_end']},
                 'reference':{'mode':'local_optical_pair','path':str(base/'reference_pair.npz')},
                 'context':case['event_context'],'policy_id':recipe['protocol_version']}
        result=process_flood(request,ROOT/'products/flood_pilot/runs'/case['id'])
        summary.append({'case_id':case['id'],'status':result['status'],'summary':result['signature_summary'],
                        'reference_evidence':{k:v for k,v in result['reference_evidence'].items() if k!='provenance'},
                        'proxy_comparison':result['proxy_comparison'],'matched_controls':result['matched_controls']})
        print('FLOOD_PILOT',json.dumps(summary[-1]),flush=True)
    (ROOT/'products/flood_pilot/pilot_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
