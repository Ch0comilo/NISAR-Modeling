"""A new measured earthquake-context candidate, without a displacement claim."""
from pathlib import Path
import hashlib,json
from surface_dynamics.catalog import search,group_entries
from surface_dynamics.network import acquisition_dates,build_network
from surface_dynamics.insar import fetch_pair
from surface_dynamics.insar_readiness import process_readiness
from surface_dynamics.insar_stack import process_stack

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'products/characterization_v05/insar_candidate'


def run():
    OUT.mkdir(parents=True,exist_ok=True);catalog_path=OUT/'catalog.json'
    if catalog_path.exists():catalog=json.loads(catalog_path.read_text())
    else:
        catalog=search(-68.32,10.40,'GUNW',start='2026-06-01T00:00:00Z',end='2026-08-31T23:59:59Z')
        catalog_path.write_text(json.dumps(catalog,indent=2))
    groups=group_entries(catalog['entries'],'GUNW');key='162_A_007_4000_SH'
    desired=[('2026-06-01','2026-06-13'),('2026-06-13','2026-06-25'),('2026-06-25','2026-07-07')]
    selected=[next(e for e in groups[key] if acquisition_dates(e['title'])==pair) for pair in desired]
    selection={'case_id':'venezuela_june2026','longitude':-68.32,'latitude':10.40,'extent_km':15,
               'event_context_date':'2026-06-24','series_key':key,'coherence_threshold':.35,
               'context_source':'https://www.paho.org/sites/default/files/2026/06/ven-earthquakessitrep124jun2026v2-1.pdf',
               'selection_policy':'A priori epicentral candidate, 15 km window and three contiguous before/bracketing/after intervals; no phase-pattern or displacement-based selection',
               'source_ids':[e['title'] for e in selected],
               'catalog_sha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
    path=OUT/'selection_freeze.json'
    if path.exists():
        if json.loads(path.read_text())!=selection:raise ValueError('Candidate selection changed after admission')
    else:path.write_text(json.dumps(selection,indent=2))
    paths=[]
    for i,entry in enumerate(selected):
        path=fetch_pair(entry,-68.32,10.40,OUT/'cache'/f'pair_{i}.npz',15,unwrapped_only=True)
        paths.append(str(path));print('V05_INSAR_PAIR',i,flush=True)
    request={'contract_version':'1.0','request_id':'venezuela-correction-readiness',
             'source':{'mode':'local_pairs','pair_paths':paths},'coherence_threshold':.35}
    readiness=process_readiness(request,OUT/'readiness');stack=process_stack(request,OUT/'intervals')
    networks=build_network(catalog['entries'],event_date='2026-06-24')
    report={'selection':selection,'readiness_status':readiness['status'],'quality':readiness['quality'],
            'pair_quality':readiness['pair_quality'],'network_cycle_ranks':{n['series_key']:n['cycle_rank'] for n in networks},
            'independent_stable_reference_supplied':False,'independent_geodetic_validation_supplied':False,
            'corrected_los_displacement_cm':None,'stack_status':stack['status']}
    (OUT/'candidate_report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print('V05_INSAR',json.dumps({k:v for k,v in report.items() if k not in ['selection','pair_quality']}),flush=True)


if __name__=='__main__':run()
