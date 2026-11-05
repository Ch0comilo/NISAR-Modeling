"""Live public-catalog acceptance checks; no archive token or raster reads."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from surface_dynamics.network import plan_network

ROOT=Path(__file__).resolve().parents[1]
cases=json.loads((ROOT/'configs/global_cases.json').read_text())


def run(case):
    request={'contract_version':'1.0','request_id':case['id']+'-network',
             'source':{'mode':'catalog_point','longitude':case['longitude'],'latitude':case['latitude'],
                       'start_date':'2026-06-01','end_date':'2026-10-31'},'max_baseline_days':48}
    if case.get('context_date'):request['event_date']=case['context_date']
    result=plan_network(request,ROOT/'products/networks'/case['id'])
    return {'case_id':case['id'],'status':result['status'],
            'catalog_hits':result['catalog'].get('hits'),
            'compatible_networks':len(result['networks']),
            'edges':sum(len(n['edges']) for n in result['networks']),
            'independent_cycles':sum(n['cycle_rank'] for n in result['networks']),
            'quantitative_phase_reads':0}


if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,cases))
    output=ROOT/'docs/results/global_network_checks.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'scope':'eight fixed real point queries, not universal coverage','cases':results},indent=2))
    print(json.dumps(results,indent=2))
