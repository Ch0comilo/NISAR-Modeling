"""Regenerate the new measured InSAR audit entirely from bundled fixtures."""
from pathlib import Path
import hashlib,json
from surface_dynamics.insar_readiness import process_readiness
from surface_dynamics.insar_stack import process_stack
from surface_dynamics.network import build_network

ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    out=ROOT/'products/characterization_v05/insar_candidate'
    selection=json.loads((out/'selection_freeze.json').read_text())
    catalog_path=out/'catalog.json'
    if hashlib.sha256(catalog_path.read_bytes()).hexdigest()!=selection['catalog_sha256']:raise ValueError('InSAR candidate catalog changed')
    paths=[out/'cache'/f'pair_{i}.npz' for i in range(3)]
    for path,title in zip(paths,selection['source_ids']):
        if json.loads(path.with_suffix('.json').read_text())['title']!=title:raise ValueError('InSAR pair identity does not match admission')
    request={'contract_version':'1.0','request_id':'venezuela-correction-readiness',
             'source':{'mode':'local_pairs','pair_paths':list(map(str,paths))},'coherence_threshold':.35}
    ready=process_readiness(request,out/'readiness');stack=process_stack(request,out/'intervals')
    networks=build_network(json.loads(catalog_path.read_text())['entries'],event_date='2026-06-24')
    report={'selection':selection,'readiness_status':ready['status'],'quality':ready['quality'],
            'pair_quality':ready['pair_quality'],'network_cycle_ranks':{n['series_key']:n['cycle_rank'] for n in networks},
            'independent_stable_reference_supplied':False,'independent_geodetic_validation_supplied':False,
            'corrected_los_displacement_cm':None,'stack_status':stack['status']}
    (out/'candidate_report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps({'status':ready['status'],'common_phase_cells':ready['quality']['common_phase_support_pixels'],
                      'common_correction_input_cells':ready['quality']['common_correction_input_pixels']}))
