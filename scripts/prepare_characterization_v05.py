"""Freeze a declared protocol, then fetch compatible bounded measured series."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,hashlib,json,subprocess,sys
import numpy as np
from surface_dynamics.catalog import search,group_entries
from surface_dynamics.io import fetch_chip,cube_from_chips,exclude_empty_acquisitions,save_cube

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'configs/characterization_v05.json'
BASE=ROOT/'products/characterization_v05'


def freeze():
    files=[CONFIG,ROOT/'src/surface_dynamics/flood_signatures.py',ROOT/'contracts/flood-policy.json',
           ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt',ROOT/'src/surface_dynamics/temporal_v2.py']
    record={'protocol_id':'surface_characterization_v05_fixed_v1',
            'scope':'freeze before fresh radar/reference agreement is inspected',
            'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    BASE.mkdir(parents=True,exist_ok=True);path=BASE/'protocol_freeze.json'
    if path.exists():
        if json.loads(path.read_text())!=record:raise ValueError('Frozen protocol or predictor changed; create a new experiment rather than overwrite')
    else:path.write_text(json.dumps(record,indent=2))
    return record


def prepare(case,recipe):
    out=BASE/'cases'/case['id'];out.mkdir(parents=True,exist_ok=True);path=out/'catalog.json'
    if path.exists():catalog=json.loads(path.read_text())
    else:
        catalog=search(case['longitude'],case['latitude'],start=case['baseline_start']+'T00:00:00Z',end=case['assessment_end']+'T23:59:59Z',collection=case['collection'])
        path.write_text(json.dumps(catalog,indent=2))
    options=[]
    for key,entries in group_entries(catalog['entries']).items():
        pre=[e for e in entries if case['baseline_start']<=e['time_start'][:10]<=case['baseline_end']]
        post=[e for e in entries if case['assessment_start']<=e['time_start'][:10]<=case['assessment_end']]
        if pre and post:
            lag=int((np.datetime64(post[0]['time_start'][:10])-np.datetime64(case['assessment_start']))/np.timedelta64(1,'D'))
            options.append(((len(pre)>=3,-lag,min(len(pre),6),len(post),key),pre,post))
    report={'case_id':case['id'],'role':case['role'],'status':'no_compatible_bracketing_data','catalog_hits':catalog['hits']}
    if options:
        rank,pre,post=max(options,key=lambda a:a[0]);cap=recipe['max_dates']
        npre=min(len(pre),cap//2);npost=min(len(post),cap-npre)
        entries=[pre[i] for i in np.linspace(0,len(pre)-1,npre,dtype=int)]+[post[i] for i in np.linspace(0,len(post)-1,npost,dtype=int)]
        paths=[]
        for e in entries:
            p=out/'raw'/(e['time_start'][:10]+'.npz')
            fetch_chip(e,case['longitude'],case['latitude'],p,recipe['native_chip_pixels']);paths.append(p)
            print('V05_MEASUREMENT',case['id'],e['time_start'][:10],flush=True)
        with np.load(paths[0],allow_pickle=False) as h:spacing=float(h['x'][1]-h['x'][0])
        factor=int(round(recipe['target_posting_m']/spacing))
        if factor<1 or not np.isclose(factor*spacing,recipe['target_posting_m']):raise ValueError('Posting must be an integer native block')
        cube=cube_from_chips(paths,factor);cube,excluded=exclude_empty_acquisitions(cube)
        cube['processing_metadata_json']=np.array(json.dumps({'collection':case['collection'],'target_posting_m':recipe['target_posting_m'],
            'aggregation':'arithmetic linear power before dB; strict native block support','excluded_empty_acquisitions':excluded,
            'selection_policy':'baseline_then_event_latency_v1','role':case['role']}))
        save_cube(cube,out/'cube.npz')
        before=[str(d) for d in cube['dates'] if case['baseline_start']<=str(d)<=case['baseline_end']]
        after=[str(d) for d in cube['dates'] if case['assessment_start']<=str(d)<=case['assessment_end']]
        report.update(status='admitted' if before and after else 'insufficient_supported_bracketing',series_key=rank[-1],
                      baseline_dates=before,assessment_dates=after,shape=list(cube['power'].shape),cube_sha256=hashlib.sha256((out/'cube.npz').read_bytes()).hexdigest())
    (out/'admission.json').write_text(json.dumps(report,indent=2));print('V05_ADMISSION',json.dumps(report),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case');p.add_argument('--references',action='store_true');args=p.parse_args()
    freeze();recipe=json.loads(CONFIG.read_text())
    if args.references:
        from surface_dynamics.io import load_cube
        from surface_dynamics.optical_water import prepare_reference_pair
        def reference(case):
            out=BASE/'cases'/case['id']
            if not (out/'admission.json').exists():return
            admission=json.loads((out/'admission.json').read_text())
            if admission['status']!='admitted':return
            cube=load_cube(out/'cube.npz')
            if case['role']=='wildfire_development':
                from surface_dynamics.disturbance import prepare_disturbance
                result=prepare_disturbance(cube,case['longitude'],case['latitude'],
                    {'start_date':case['assessment_start'],'end_date':case['assessment_end']},
                    admission['assessment_dates'][-1],out/'disturbance_reference.npz')
                print('V05_DISTURBANCE_REFERENCE',case['id'],result['status'],flush=True);return
            result=prepare_reference_pair(cube,case['longitude'],case['latitude'],admission['baseline_dates'][-1],
                admission['assessment_dates'][0],out/'reference_pair.npz',recipe['optical_max_offset_days'],
                recipe['optical_candidate_cap'],boundary_date=case['assessment_start'])
            (out/'reference_admission.json').write_text(json.dumps(result,indent=2));print('V05_REFERENCE',case['id'],json.dumps(result),flush=True)
        cases=[c for c in recipe['cases'] if not args.case or c['id']==args.case]
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(reference,cases))
    elif args.case:prepare(next(c for c in recipe['cases'] if c['id']==args.case),recipe)
    else:
        def child(case):
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--case',case['id']],check=True)
            return json.loads((BASE/'cases'/case['id']/'admission.json').read_text())
        with ThreadPoolExecutor(max_workers=3) as pool:reports=list(pool.map(child,recipe['cases']))
        (BASE/'admission.json').write_text(json.dumps(reports,indent=2))
