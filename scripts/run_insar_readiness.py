"""Audit the existing measured 5 km and 15 km windows without new downloads."""
from pathlib import Path
import json
from surface_dynamics.insar_readiness import process_readiness
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    for case in ['kumamoto','kumamoto_wide']:
        request={'contract_version':'1.0','request_id':case+'-correction-readiness','source':{'mode':'local_pairs','pair_paths':[str(ROOT/'products/insar_stacks'/case/'cache'/f'pair_{i}.npz') for i in range(3)]},'coherence_threshold':.35}
        result=process_readiness(request,ROOT/'products/insar_readiness'/case)
        print(json.dumps({'case':case,'status':result['status'],'quality':result['quality'],'sensitivity':result['threshold_sensitivity']}),flush=True)
