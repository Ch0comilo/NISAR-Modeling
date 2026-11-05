"""Derive common-posting cubes; download only quantitative new region chips."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,json,subprocess,sys
import numpy as np
from rasterio.warp import transform
from surface_dynamics.catalog import search,group_entries,choose_series
from surface_dynamics.io import load_cube,save_cube,fetch_chip,cube_from_chips,exclude_empty_acquisitions

ROOT=Path(__file__).resolve().parents[1]


def coarsen(cube,target):
    spacing=float(cube['x'][1]-cube['x'][0]);factor=int(round(target/spacing))
    if factor<1 or not np.isclose(spacing*factor,target):raise ValueError('Target posting must be a whole native block multiple; no upsampling')
    power=cube['power'];T,C,H,W=power.shape
    if H%factor or W%factor:raise ValueError('Cube extent must contain complete aggregation blocks')
    mask=cube['valid'].astype(bool)&np.isfinite(power)&(power>0)
    shape=(T,C,H//factor,factor,W//factor,factor)
    valid=mask.reshape(shape).all(axis=(3,5))
    averaged=np.where(mask,power,0).reshape(shape).mean(axis=(3,5))
    result={**cube,'power':np.where(valid,averaged,np.nan).astype('float32'),'valid':valid,
            'x':cube['x'].reshape(-1,factor).mean(1),'y':cube['y'].reshape(-1,factor).mean(1),
            'processing_metadata_json':np.array(json.dumps({'posting_m':target,'aggregation':'arithmetic linear power before dB; strict per-channel block support','source_posting_m':spacing,'source_block_factor':factor}))}
    return result


def prepare(download=False,only_region=None):
    recipe=json.loads((ROOT/'configs/global_v2.json').read_text())
    folder=ROOT/'products/global_v2';(folder/'cubes').mkdir(parents=True,exist_ok=True);(folder/'catalog').mkdir(exist_ok=True)
    old=['forest_river','deliblato','chenab','niscemi','okavango','amazon','murray']
    summary=[]
    for name in ([] if only_region else old):
        cube=coarsen(load_cube(ROOT/'products/cubes'/f'{name}.npz'),recipe['target_posting_m'])
        save_cube(cube,folder/'cubes'/f'{name}.npz');summary.append({'case_id':name,'dates':len(cube['dates']),'shape':list(cube['power'].shape),'posting_m':recipe['target_posting_m']})
    if download:
        def region(case):
            name=case['id'];path=folder/'catalog'/f'{name}.json'
            if path.exists():catalog=json.loads(path.read_text())
            else:
                catalog=search(case['longitude'],case['latitude'],start='2026-01-01T00:00:00Z',end='2026-10-31T23:59:59Z');path.write_text(json.dumps(catalog,indent=2))
            key,entries=choose_series(group_entries(catalog['entries']),max_dates=recipe['max_dates'])
            if len(entries)<8:raise ValueError(f'{name}: insufficient compatible date support')
            paths=[]
            for e in entries:
                dest=folder/'raw'/name/(e['time_start'][:10]+'.npz')
                cached=dest.exists() and dest.with_suffix('.json').exists()
                fetch_chip(e,case['longitude'],case['latitude'],dest,pixels=recipe['native_chip_pixels']);paths.append(dest)
                if not cached:print('MEASURED_CHIP',name,e['time_start'][:10],flush=True)
            cube=coarsen(cube_from_chips(paths,1),recipe['target_posting_m'])
            cube,excluded=exclude_empty_acquisitions(cube)
            if len(cube['dates'])<8:raise ValueError('Fewer than eight dates have measured support after excluding wholly empty acquisitions')
            if list(map(str,cube['polarizations']))!=['HH','HV']:raise ValueError('Recipe requires measured HH/HV, not substituted channels')
            xx,yy=transform('EPSG:4326',f'EPSG:{int(cube["epsg"])}',[case['longitude']],[case['latitude']])
            half=recipe['target_posting_m']/2
            assert cube['x'][0]-half<=xx[0]<=cube['x'][-1]+half and cube['y'][-1]-half<=yy[0]<=cube['y'][0]+half,'Point outside accepted quantitative cube'
            if np.isfinite(cube['power']).all(axis=(0,1)).mean()<.2:raise ValueError('Recipe minimum complete support not met')
            save_cube(cube,folder/'cubes'/f'{name}.npz')
            row=int(np.argmin(abs(cube['y']-yy[0])));col=int(np.argmin(abs(cube['x']-xx[0])))
            item={'case_id':name,'series_key':key,'dates':len(cube['dates']),'shape':list(cube['power'].shape),'posting_m':recipe['target_posting_m'],'center_point_verified':True,
                  'center_observed_date_count':int(cube['valid'][:,:,row,col].any(1).sum()),'complete_spatial_support_fraction':float(cube['valid'].all(axis=(0,1)).mean()),
                  'excluded_empty_acquisitions':excluded}
            (folder/'catalog'/f'{name}_accepted.json').write_text(json.dumps(item,indent=2))
            print('PREPARED',json.dumps(item),flush=True);return item
        if only_region:
            selected=[case for case in recipe['new_regions'] if case['id']==only_region]
            if not selected:raise ValueError('Unknown configured region')
            summary.extend(region(case) for case in selected)
        else:
            # h5py serializes calls within one interpreter even across files.
            # Separate bounded subprocesses permit independent archive reads.
            def child(case):
                subprocess.run([sys.executable,str(Path(__file__).resolve()),'--download','--region',case['id']],check=True)
                return json.loads((folder/'catalog'/f'{case["id"]}_accepted.json').read_text())
            with ThreadPoolExecutor(max_workers=4) as pool:summary.extend(pool.map(child,recipe['new_regions']))
    if not only_region:(folder/'data_inventory.json').write_text(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true');parser.add_argument('--region');args=parser.parse_args();print(json.dumps(prepare(args.download,args.region),indent=2))
