"""Quantitative inputs for a declared development pilot; no image-derived SAR."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,json,subprocess,sys
import numpy as np
import requests
from surface_dynamics.catalog import group_entries
from surface_dynamics.io import fetch_chip,cube_from_chips,save_cube,exclude_empty_acquisitions

ROOT=Path(__file__).resolve().parents[1]


def region(case,recipe):
    folder=ROOT/'products/flood_pilot/cases'/case['id'];folder.mkdir(parents=True,exist_ok=True)
    catalog_path=folder/'catalog.json'
    if catalog_path.exists():catalog=json.loads(catalog_path.read_text())
    else:
        response=requests.get('https://cmr.earthdata.nasa.gov/search/granules.json',params={'short_name':case['collection'],'point':f'{case["longitude"]},{case["latitude"]}','temporal':case['start_date']+'T00:00:00Z,'+case['end_date']+'T23:59:59Z','page_size':200,'sort_key':'start_date'},timeout=45);response.raise_for_status()
        if int(response.headers.get('CMR-Hits','0'))>200:raise ValueError('Narrow the pilot dates; catalog is not silently truncated')
        catalog={'query_url':response.url,'entries':response.json()['feed']['entry'],'collection':case['collection']};catalog_path.write_text(json.dumps(catalog,indent=2))
    groups=group_entries(catalog['entries']);admissible={}
    for key,entries in groups.items():
        before=[e for e in entries if case['baseline_start']<=e['time_start'][:10]<=case['baseline_end']]
        after=[e for e in entries if case['assessment_start']<=e['time_start'][:10]<=case['assessment_end']]
        if before and after:admissible[key]=entries
    if admissible:
        def rank(item):
            key,entries=item
            before=[e for e in entries if case['baseline_start']<=e['time_start'][:10]<=case['baseline_end']]
            after=[e for e in entries if case['assessment_start']<=e['time_start'][:10]<=case['assessment_end']]
            delay=int((np.datetime64(after[0]['time_start'][:10])-np.datetime64(case['assessment_start']))/np.timedelta64(1,'D'))
            return (len(before)>=3,-delay,min(len(before),6),len(after),key)
        key,entries=max(admissible.items(),key=rank)
        pre=[e for e in entries if case['baseline_start']<=e['time_start'][:10]<=case['baseline_end']]
        post=[e for e in entries if case['assessment_start']<=e['time_start'][:10]<=case['assessment_end']]
        cap=recipe['max_dates'];npre=min(len(pre),cap//2);npost=min(len(post),cap-npre)
        entries=[pre[i] for i in np.linspace(0,len(pre)-1,npre,dtype=int)]+[post[i] for i in np.linspace(0,len(post)-1,npost,dtype=int)]
    else:key,entries=None,[]
    if not entries:
        report={'case_id':case['id'],'status':'no_compatible_bracketing_data'};(folder/'admission.json').write_text(json.dumps(report,indent=2));return report
    paths=[]
    for e in entries:
        path=folder/'raw'/(e['time_start'][:10]+'.npz');cached=path.exists() and path.with_suffix('.json').exists()
        fetch_chip(e,case['longitude'],case['latitude'],path,pixels=recipe['native_chip_pixels']);paths.append(path)
        if not cached:print('PILOT_MEASUREMENT',case['id'],e['time_start'][:10],flush=True)
    with np.load(paths[0],allow_pickle=False) as measured:spacing=float(measured['x'][1]-measured['x'][0])
    factor=int(round(recipe['target_posting_m']/spacing))
    if factor<1 or not np.isclose(factor*spacing,recipe['target_posting_m']):raise ValueError('No implicit power interpolation to pilot posting')
    cube=cube_from_chips(paths,factor);cube,excluded=exclude_empty_acquisitions(cube)
    cube['processing_metadata_json']=np.array(json.dumps({'collection':case['collection'],'target_posting_m':recipe['target_posting_m'],'aggregation':'arithmetic linear gamma0 power, strict block support','excluded_empty_acquisitions':excluded,'calibration_context':'beta data not fully calibrated' if 'BETA' in case['collection'] else 'provisional product caveats apply'}))
    save_cube(cube,folder/'cube.npz')
    before=[str(d) for d in cube['dates'] if case['baseline_start']<=str(d)<=case['baseline_end']]
    after=[str(d) for d in cube['dates'] if case['assessment_start']<=str(d)<=case['assessment_end']]
    report={'case_id':case['id'],'status':'admitted' if before and after else 'insufficient_supported_bracketing','series_key':key,'baseline_dates':before,'assessment_dates':after,'collection':case['collection'],'shape':list(cube['power'].shape),'series_selection_policy':'baseline_then_event_latency_v1'}
    (folder/'admission.json').write_text(json.dumps(report,indent=2));print('PILOT_ADMISSION',json.dumps(report),flush=True);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case');args=parser.parse_args();recipe=json.loads((ROOT/'configs/flood_pilot.json').read_text())
    if args.case:region(next(c for c in recipe['cases'] if c['id']==args.case),recipe)
    else:
        def child(case):
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--case',case['id']],check=True)
            return json.loads((ROOT/'products/flood_pilot/cases'/case['id']/'admission.json').read_text())
        with ThreadPoolExecutor(max_workers=3) as pool:reports=list(pool.map(child,recipe['cases']))
        (ROOT/'products/flood_pilot/admission.json').write_text(json.dumps(reports,indent=2));print(json.dumps(reports))
