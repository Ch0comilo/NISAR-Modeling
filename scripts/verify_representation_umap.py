"""Recalculate plotted measurements, frozen neural states and neighborhood metrics."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import os
import numpy as np
from scipy.spatial.distance import cdist
from surface_dynamics.analysis import measured_db
from surface_dynamics.embedding_atlas import aggregate_hh_hv, case_inventory, standardize
from surface_dynamics.io import load_cube
from surface_dynamics.transformer import infer
from surface_dynamics.dynamic_view import controlled_view

ROOT = Path(__file__).resolve().parents[1]


def verify(output, refit=False):
    output = Path(output)
    freeze = json.loads((output/'extraction_freeze.json').read_text())
    for name, digest in freeze['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
    for manifest in ['reference_provenance.json', 'execution_provenance.json']:
        for name,digest in json.loads((output/manifest).read_text())['files'].items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest, (manifest,name)
    # Existing scientific evaluations must remain byte-for-byte frozen.
    for path in ['models/global_explorer_v2/evaluation_freeze.json', 'products/characterization_v05/probe_freeze.json']:
        previous = json.loads((ROOT/path).read_text())
        for name, digest in previous.get('files', {}).items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
    prior=json.loads((ROOT/'models/global_explorer_v2/evaluation_freeze.json').read_text())
    for name,key in [('models/global_explorer_v2/masked_temporal_transformer.pt','checkpoint_sha256'),
                     ('configs/global_v2.json','recipe_sha256'),
                     ('src/surface_dynamics/temporal_v2.py','architecture_source_sha256')]:
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==prior[key]
    rows = json.loads((output/'points.json').read_text())
    features = dict(np.load(output/'features.npz', allow_pickle=False))
    positions = dict(np.load(output/'positions.npz', allow_pickle=False))
    report = json.loads((output/'summary.json').read_text())
    assert len(rows) == len(features['trajectory']) == report['points']
    assert len(set(r['point_id'] for r in rows)) == len(rows)
    assert report['neural_training_performed'] is report['reference_labels_used_for_fit'] is False
    checked = []
    checkpoint = ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt'
    for case in case_inventory(ROOT):
        ids = [i for i,r in enumerate(rows) if r['case_id'] == case['id']]
        if not ids: continue
        cube = aggregate_hh_hv(load_cube(ROOT/case['cube_path']))
        # Independently infer a 2x2 crop: the temporal encoder has no spatial
        # mixing, so the selected trajectory must agree with sampled extraction.
        i = ids[len(ids)//2]; r = rows[i]; y,x = r['row'],r['column']
        db = measured_db(cube)[:,:,y,x]
        np.testing.assert_allclose(features['observed_db'][i,:len(db)], db, rtol=0,atol=1e-6)
        assert r['dates'] == cube['dates'].astype(str).tolist()
        assert r['x_m'] == cube['x'][x] and r['y_m'] == cube['y'][y]
        assert np.isfinite(db).all()
        np.testing.assert_allclose([r['median_hh_db'],r['median_hv_db']],np.median(db,axis=0),atol=1e-6)
        # Each crop keeps the complete actual acquisition history.
        h,w = cube['power'].shape[-2:]; y0=min(y,h-2); x0=min(x,w-2)
        crop={**cube,'power':cube['power'][...,y0:y0+2,x0:x0+2],
              'valid':cube['valid'][...,y0:y0+2,x0:x0+2],
              'x':cube['x'][x0:x0+2],'y':cube['y'][y0:y0+2]}
        raw=infer(crop,checkpoint,dropout_draws=1)
        control=controlled_view(crop,checkpoint,raw['state'])['state'][:,y-y0,x-x0]
        np.testing.assert_allclose(features['trajectory'][i],control.mean(0),atol=3e-6,rtol=3e-6)
        if r['baseline_last_date']:
            dates=r['dates']; a=dates.index(r['assessment_first_date']); b=dates.index(r['baseline_last_date'])
            np.testing.assert_allclose(features['change'][i],control[a]-control[b],atol=3e-6,rtol=3e-6)
        seen=set()
        for j in ids:
            rr,cc=rows[j]['row'],rows[j]['column']; key=(rr//2,cc//2)
            assert key not in seen;seen.add(key)
            assert np.isfinite(measured_db(cube)[:,:,rr,cc]).all()
        checked.append(case['id'])
    for name in ['trajectory','change','physical']:
        mask=positions[name+'_mask'];xy=positions[name]
        assert mask.sum()==len(xy) and np.isfinite(xy).all()
        z,_,_=standardize(features[name][mask])
        k=report['maps'][name]['neighborhood_k']
        overlap=[];same=[]
        subset=[r for r,keep in zip(rows,mask) if keep]
        for start in range(0,len(z),64):
            ii=np.arange(start,min(start+64,len(z)))
            hd=cdist(z[ii],z);ld=cdist(xy[ii],xy)
            hd[np.arange(len(ii)),ii]=np.inf;ld[np.arange(len(ii)),ii]=np.inf
            aa=np.argsort(hd,axis=1)[:,:k];bb=np.argsort(ld,axis=1)[:,:k]
            for i,a,b in zip(ii,aa,bb):
                overlap.append(len(set(a)&set(b))/k)
                same.append(np.mean([subset[q]['case_id']==subset[i]['case_id'] for q in a]))
        np.testing.assert_allclose(np.mean(overlap),report['maps'][name]['original_neighbor_recall_in_2d'],atol=1e-12)
        np.testing.assert_allclose(np.mean(same),report['maps'][name]['original_same_scene_neighbor_fraction'],atol=1e-12)
        if name != 'physical':
            from sklearn.cluster import HDBSCAN
            params={k:v for k,v in freeze['experiment']['clustering'].items() if k!='method'}
            repeated=HDBSCAN(**params,n_jobs=1,copy=True).fit_predict(z)
            np.testing.assert_array_equal(repeated,positions[name+'_clusters'])
    if refit:
        os.environ.setdefault('NUMBA_NUM_THREADS','1')
        import umap
        z,_,_=standardize(features['trajectory'])
        repeated=umap.UMAP(**freeze['experiment']['umap']).fit_transform(z)
        np.testing.assert_array_equal(repeated,positions['trajectory'])
    examples=json.loads((output/'examples.json').read_text())
    z,_,_=standardize(features['trajectory'])
    for group in examples:
        anchor=group['anchor_index'];distance=np.linalg.norm(z-z[anchor],axis=1)
        for i,example in zip(group['example_indices'],group['examples']):
            assert rows[i]['point_id']==example['point_id']
            assert abs(distance[i]-example['original_48d_distance_from_anchor'])<1e-8
            np.testing.assert_allclose(example['hh_db'],features['observed_db'][i,:rows[i]['date_count'],0])
    with (output/'points.csv').open(newline='',encoding='utf8') as f:
        assert len(list(csv.DictReader(f)))==len(rows)
    artifacts=json.loads((output/'artifacts.json').read_text())
    for entry in artifacts:
        p=output/entry['href'];assert p.parent.resolve()==output.resolve()
        assert p.stat().st_size==entry['bytes']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256']
    result={'status':'share_with_caveats','scenes_spot_checked':checked,'points':len(rows),
            'artifacts_verified':len(artifacts),'frozen_experiments_unchanged':True,
            'original_space_clustering_reproduced':True,'primary_umap_exact_reproduction':True if refit else None,
            'scope':'Independent physical/source/state spot checks per scene, all sampling IDs and artifact hashes; no event accuracy claim'}
    (output/'validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'products/representation_umap')
    parser.add_argument('--refit',action='store_true',help='Also require exact reproduction of the primary UMAP on this runtime')
    args=parser.parse_args();verify(args.output,args.refit)
