"""Independent source/band/neighbor checks for the surface feature report."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from surface_dynamics.io import load_cube
from surface_dynamics.embedding_atlas import aggregate_hh_hv,case_inventory
from surface_dynamics.analysis import measured_db
from surface_dynamics.behavior_analogs import eligible,distance_km
from surface_dynamics.transformer import infer
from surface_dynamics.dynamic_view import controlled_view

ROOT=Path(__file__).resolve().parents[1]


def run(output):
    output=Path(output);freeze=json.loads((output/'protocol_freeze.json').read_text());policy=freeze['protocol']
    for name,digest in freeze['files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    for name,digest in json.loads((output/'execution_provenance.json').read_text())['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    for name,digest in json.loads((output/'reference_provenance.json').read_text())['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    previous=ROOT/'products/characterization_v05/probe_freeze.json'
    for name,digest in json.loads(previous.read_text())['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    rows=json.loads((output/'points.json').read_text());admission=json.loads((output/'admission.json').read_text())
    metadata=json.loads((output/'analog_metadata.json').read_text());analogs=json.loads((output/'analogs.json').read_text())
    features=dict(np.load(output/'analog_features.npz',allow_pickle=False));scaling=json.loads((output/'scaling.json').read_text())
    caseinfo={c['case_id']:c for c in admission};checks=[];neural_checks=[]
    for case in case_inventory(ROOT):
        selected=[r for r in rows if r['case_id']==case['id']]
        if not selected:continue
        cube=aggregate_hh_hv(load_cube(ROOT/case['cube_path']));db=measured_db(cube).astype(float);dates=cube['dates'].astype(str)
        info=caseinfo[case['id']];b=info['baseline'];a=info['assessment']
        before=np.flatnonzero((dates>=b['start_date'])&(dates<=b['end_date']));after=np.flatnonzero((dates>=a['start_date'])&(dates<=a['end_date']))
        maps=dict(np.load(output/(case['id']+'_maps.npz'),allow_pickle=False))
        for r in selected:
            y,x=r['row'],r['column'];v=r['features'];values=db[:,:,y,x]
            base=np.median(values[before],axis=0);mad=1.4826*np.median(abs(values[before]-base),axis=0)
            np.testing.assert_allclose(v['baseline_db'],base,atol=2e-6)
            np.testing.assert_allclose(v['first_change_db'],values[after[0]]-base,atol=2e-6)
            assert v['normal_probability'] is v['phenomenon_class'] is v['causal_attribution'] is None
            enough=len(before)>=policy['baseline']['minimum_dates']
            if enough:
                half=np.maximum(policy['baseline']['minimum_departure_db'],policy['baseline']['departure_scale_multiple']*np.maximum(mad,policy['baseline']['scale_floor_db']))
                flag=(abs(values[after]-base)>half).any(1)
                assert v['outside_baseline_observations']==int(flag.sum())
                np.testing.assert_allclose(v['diagnostic_half_width_db'],half,atol=2e-6)
                assert abs(v['observed_departure_fraction']-flag.mean())<1e-12
            else:assert v['state']=='insufficient_baseline' and v['maximum_scaled_departure'] is None
            np.testing.assert_allclose(r['hh_db'],values[:,0],atol=2e-6)
            if r['analog_index'] is not None:
                i=r['analog_index'];assert metadata[i]['point_id']==r['point_id']
                ids=[list(dates).index(d) for d in metadata[i]['sampling']['dates']]
                np.testing.assert_allclose(features['observed_db'][i],values[ids],atol=2e-6)
        # Independently check neighbor medians on one interior sparse point.
        interior=[r for r in selected if 0<r['row']<len(cube['y'])-1 and 0<r['column']<len(cube['x'])-1]
        if interior:
            r=interior[len(interior)//2];y,x=r['row'],r['column']
            patch=maps['first_change_db'][0,y-1:y+2,x-1:x+2].copy();patch[1,1]=np.nan
            if np.isfinite(patch).sum()>=4:
                np.testing.assert_allclose(maps['local_co_contrast_db'][y,x],maps['first_change_db'][0,y,x]-np.nanmedian(patch),atol=1e-12)
        analog_rows=[r for r in selected if r['analog_index'] is not None]
        if analog_rows:
            r=analog_rows[len(analog_rows)//2];i=r['analog_index'];y,x=r['row'],r['column']
            indices=[list(dates).index(d) for d in metadata[i]['sampling']['dates']]
            a=db[indices,:,y,x];base=np.median(a[:3],axis=0);mad=1.4826*np.median(abs(a[:3]-base),axis=0)
            change=np.median(a[3:],axis=0)-base;ratio=a[:,1]-a[:,0];steps=abs(np.diff(a[:,0]))
            band=np.maximum(3,3*np.maximum(mad,1));fraction=(abs(a[3:]-base)>band).any(1).mean()
            vector=np.r_[base,mad,change,np.median(ratio[3:])-np.median(ratio[:3]),a[3,0]-a[2,0],
                         steps.max()/steps.sum() if steps.sum() else 0,fraction,
                         1.4826*np.median(abs(a[3:,0]-np.median(a[3:,0]))),a[-1,0]-a[0,0]]
            np.testing.assert_allclose(features['physical'][i],vector,atol=2e-6)
            h,w=cube['power'].shape[-2:];y0=min(y,h-2);x0=min(x,w-2)
            crop={**cube,'power':cube['power'][indices,:,y0:y0+2,x0:x0+2],
                  'valid':cube['valid'][indices,:,y0:y0+2,x0:x0+2],'dates':cube['dates'][indices],
                  'x':cube['x'][x0:x0+2],'y':cube['y'][y0:y0+2]}
            checkpoint=ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt'
            raw=infer(crop,checkpoint,dropout_draws=1)
            state=controlled_view(crop,checkpoint,raw['state'])['state'][:,y-y0,x-x0]
            np.testing.assert_allclose(features['learned'][i],state[3]-state[2],atol=3e-6,rtol=3e-6)
            neural_checks.append(case['id'])
        checks.append(case['id'])
    look={r['point_id']:i for i,r in enumerate(metadata)}
    p=(features['physical']-scaling['physical']['center'])/scaling['physical']['scale']
    l=(features['learned']-scaling['learned']['center'])/scaling['learned']['scale']
    for name,floors in [('physical',policy['analog']['physical_scale_floors']),('learned',policy['analog']['learned_scale_floor'])]:
        ref=policy['analog']['reference_regions'];values=features[name].astype(float)
        selected=[[i for i,r in enumerate(metadata) if r['case_id']==region] for region in ref]
        selected=[ids for ids in selected if ids]
        center=np.mean([values[ids].mean(0) for ids in selected],axis=0)
        std=np.sqrt(np.mean([((values[ids]-center)**2).mean(0) for ids in selected],axis=0))
        np.testing.assert_allclose(scaling[name]['center'],center,atol=1e-10)
        np.testing.assert_allclose(scaling[name]['scale'],np.maximum(std,floors),atol=1e-10)
    counts=0
    for i,result in enumerate(analogs):
        assert result['point_id']==metadata[i]['point_id']
        for method,item in result['methods'].items():
            for neighbor in item['neighbors']:
                j=look[neighbor['point_id']]
                assert metadata[j]['case_id'] in policy['analog']['reference_regions']
                assert eligible(metadata[i],metadata[j],policy['analog'])
                pd=float(np.sqrt(np.mean((p[i]-p[j])**2)));ld=float(np.sqrt(np.mean((l[i]-l[j])**2)))
                value={'physical':pd,'learned':ld,'hybrid':np.sqrt(.5*pd**2+.5*ld**2)}[method]
                np.testing.assert_allclose(neighbor['distance'],value,atol=1e-10)
                assert distance_km(metadata[i],metadata[j])>=policy['analog']['minimum_region_distance_km']
                counts+=1
            if item['neighbors']:assert len(set(n['case_id'] for n in item['neighbors']))==3
    artifacts=json.loads((output/'artifacts.json').read_text())
    for a in artifacts:
        path=output/a['href'];assert path.resolve().parent==output.resolve()
        assert path.stat().st_size==a['bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==a['sha256']
    result={'status':'share_with_caveats','source_scenes_checked':checks,'points_checked':len(rows),
        'six_date_physical_and_neural_sources_spot_checked':neural_checks,
        'analog_distances_and_gates_checked':counts,'artifacts_verified':len(artifacts),'previous_probe_freeze_unchanged':True,
        'scope':'Source measurements, baseline-only bands, observed counts, local contrast, all returned analog distances/gates and file hashes; no normal/phenomenon accuracy validation'}
    (output/'validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'products/surface_features');run(parser.parse_args().output)
