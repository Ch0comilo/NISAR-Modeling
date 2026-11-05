"""Reproduce the frozen encoder's label-blind UMAP exploration, offline.

Install the exploration extra and restore the existing v0.5 measured fixtures.
All 2D positions are transductive exploration, never a new held-out benchmark.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import importlib.metadata
import json
import os

# Fix CPU execution before importing Numba/BLAS/Torch. No credential access.
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np
from scipy.spatial.distance import cdist
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.neighbors import NearestNeighbors
import umap

from surface_dynamics.embedding_atlas import (extract_atlas, add_reference_metadata,
                                              standardize, case_inventory)

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf8')


def neighbors(a, k):
    model = NearestNeighbors(n_neighbors=k+1, n_jobs=1).fit(a)
    candidates = model.kneighbors(a, return_distance=False)
    return np.stack([ids[ids != i][:k] for i, ids in enumerate(candidates)])


def overlap(a, b):
    return float(np.mean([len(set(x) & set(y))/len(x) for x, y in zip(a, b)]))


def diagnostics(z, xy, rows, k):
    k = min(k, len(z)//2-1)
    high, low = neighbors(z, k), neighbors(xy, k)
    # Trustworthiness uses deterministic sampled anchors, ranked against ALL
    # atlas points, avoiding a large NxN matrix. Not independent statistical units.
    anchor = np.random.default_rng(419).choice(len(z), min(512, len(z)), replace=False)
    penalty = 0.
    for ids in np.array_split(anchor, max(1, len(anchor)//64)):
        order = np.argsort(cdist(z[ids], z), axis=1, kind='stable')
        ranks = np.argsort(order, axis=1)
        for j, i in enumerate(ids):
            intruders = np.array([q for q in low[i] if q not in high[i]], dtype=int)
            penalty += np.maximum(ranks[j, intruders]-k, 0).sum()
    trust = 1-2*penalty/(len(anchor)*k*(2*len(z)-3*k-1))
    scene = np.array([r['case_id'] for r in rows])
    counts = np.unique(scene, return_counts=True)[1]
    expected = float(np.sum(counts*(counts-1))/(len(rows)*(len(rows)-1)))
    result = {'point_count': len(z), 'neighborhood_k': k, 'original_neighbor_recall_in_2d': overlap(high, low),
              'trustworthiness_sampled_anchors': float(trust), 'trustworthiness_anchor_count': len(anchor),
              'original_same_scene_neighbor_fraction': float(np.mean(scene[high] == scene[:, None])),
              'umap_same_scene_neighbor_fraction': float(np.mean(scene[low] == scene[:, None])),
              'random_other_point_same_scene_fraction': expected, 'neighbor_feature_differences': {}}
    for field in ['date_count', 'span_days', 'median_gap_days', 'mad_hh_db', 'net_hh_db', 'median_hh_db']:
        a = np.array([r[field] for r in rows])
        result['neighbor_feature_differences'][field] = {
            'original_mean_absolute_difference': float(abs(a[high]-a[:, None]).mean()),
            'umap_mean_absolute_difference': float(abs(a[low]-a[:, None]).mean()),
            'all_pairs_mean_absolute_difference': float(abs(a[anchor, None]-a[None]).mean())}
    return result, high, low


def cluster_report(z, labels, rows):
    profiles = []
    for code in sorted(set(labels)-{-1}):
        ids = np.flatnonzero(labels == code)
        names, counts = np.unique([rows[i]['case_id'] for i in ids], return_counts=True)
        profiles.append({'cluster_id': int(code), 'points': len(ids), 'scene_count': len(names),
                         'scene_counts': dict(zip(names.tolist(), counts.tolist())),
                         'largest_scene_fraction': float(counts.max()/len(ids)),
                         'median_descriptors': {key: float(np.median([rows[i][key] for i in ids])) for key in
                             ['median_hh_db', 'median_hv_db', 'mad_hh_db', 'mad_hv_db', 'net_hh_db', 'date_count', 'span_days']}})
    assigned = labels >= 0
    sil = None
    if len(profiles) >= 2 and assigned.sum() > len(profiles):
        sil = float(silhouette_score(z[assigned], labels[assigned], sample_size=min(2000, int(assigned.sum())), random_state=37))
    return {'clusters': profiles, 'cluster_count': len(profiles), 'unassigned_points': int((~assigned).sum()),
            'unassigned_fraction': float((~assigned).mean()), 'original_space_silhouette_assigned_only': sil,
            'interpretation': 'Exploratory density groups in standardized original 48D. Noise is retained; group IDs carry no natural-phenomenon meaning.'}


def artifact_manifest(output):
    files = ['scripts/explore_representations.py', 'scripts/verify_representation_umap.py',
             'src/surface_dynamics/embedding_atlas.py', 'src/surface_dynamics/embedding_atlas_render.py',
             'src/surface_dynamics/io.py', 'src/surface_dynamics/analysis.py', 'src/surface_dynamics/behavior.py',
             'src/surface_dynamics/flood.py', 'src/surface_dynamics/flood_signatures.py',
             'src/surface_dynamics/representation_probe.py', 'requirements-exploration-lock.txt']
    write_json(output/'execution_provenance.json', {
        'scope': 'Final execution/verification source snapshot. Extraction inputs, config and encoder frozen before fitting in extraction_freeze.json. Figures revised for legibility without changing features, positions or groups.',
        'files': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}})
    artifacts = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name not in ['artifacts.json', 'validation.json']:
            artifacts.append({'href': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(output/'artifacts.json', artifacts)
    return artifacts


def run(output, reuse=False, render_only=False):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT/'configs/representation_umap.json').read_text())
    if render_only:
        if json.loads((output/'extraction_freeze.json').read_text())['experiment'] != config:
            raise ValueError('Cannot redraw saved positions with a changed scientific protocol')
        from surface_dynamics.embedding_atlas_render import render
        rows = json.loads((output/'points.json').read_text())
        features = dict(np.load(output/'features.npz', allow_pickle=False))
        stored = dict(np.load(output/'positions.npz', allow_pickle=False))
        positions = {k:a for k,a in stored.items() if not k.endswith(('_mask','_clusters'))}
        clusters = {name:stored[name+'_clusters'] for name in ['trajectory','change','physical']}
        masks = {name:stored[name+'_mask'] for name in ['trajectory','change','physical']}
        render(output, config, rows, features, positions, clusters, masks, json.loads((output/'summary.json').read_text()))
        artifact_manifest(output)
        return
    tracked = ['configs/representation_umap.json', 'configs/global_v2.json', 'configs/flood_pilot.json',
               'configs/characterization_v05.json', 'src/surface_dynamics/embedding_atlas.py',
               'src/surface_dynamics/temporal_v2.py', 'src/surface_dynamics/dynamic_view.py',
               'src/surface_dynamics/transformer.py', 'models/global_explorer_v2/masked_temporal_transformer.pt']
    tracked += [c['cube_path'] for c in case_inventory(ROOT) if (ROOT/c['cube_path']).exists()]
    provenance = {'experiment': config, 'files': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tracked},
                  'versions': {p: importlib.metadata.version(p) for p in
                      ['numpy', 'torch', 'scipy', 'scikit-learn', 'umap-learn', 'numba', 'pynndescent', 'plotly']}}
    freeze = output/'extraction_freeze.json'
    if freeze.exists() and json.loads(freeze.read_text()) != provenance:
        raise ValueError('Input or extraction protocol changed: use a NEW output directory, retain the old exploration')
    write_json(freeze, provenance)
    if reuse and (output/'features.npz').exists():
        features = dict(np.load(output/'features.npz', allow_pickle=False))
        rows = json.loads((output/'points.json').read_text())
        admissions = json.loads((output/'admission.json').read_text())
        # Metadata may already be attached, but it is NEVER supplied to any fit.
    else:
        records, rows, admissions = extract_atlas(ROOT, config)
        maximum_dates = max(len(r['dates']) for r in records)
        padded = []
        for record in records:
            a = np.full((len(record['trajectory']), maximum_dates, 2), np.nan, dtype='float32')
            a[:, :len(record['dates'])] = record['trajectories']; padded.append(a)
        features = {key: np.concatenate([r[key] for r in records]) for key in ['trajectory', 'change', 'physical']}
        features['observed_db'] = np.concatenate(padded)
        np.savez_compressed(output/'features.npz', **features)
        write_json(output/'points.json', rows); write_json(output/'admission.json', admissions)
    report = {'experiment_id': config['experiment_id'], 'point_unit': 'One complete measured 160 m HH/HV trajectory',
              'case_count': len(set(r['case_id'] for r in rows)), 'points': len(rows), 'maps': {},
              'scope': config['scope'], 'neural_training_performed': False, 'reference_labels_used_for_fit': False}
    positions, clusters, scalings, masks = {}, {}, {}, {}
    for name in ['trajectory', 'change', 'physical']:
        selected = np.isfinite(features[name]).all(1)
        subset = [r for r, keep in zip(rows, selected) if keep]
        z, center, scale = standardize(features[name][selected]); scalings[name] = {'center': center.tolist(), 'scale': scale.tolist()}
        print('UMAP_FIT', name, len(z), flush=True)
        mapper = umap.UMAP(**config['umap'])
        xy = mapper.fit_transform(z)
        if not np.isfinite(xy).all(): raise ValueError('UMAP returned nonfinite positions')
        metric, high, low = diagnostics(z, xy, subset, config['neighborhood_k'])
        pca = PCA(n_components=2).fit_transform(z)
        metric['pca_neighbor_recall_in_2d'] = overlap(high, neighbors(pca, config['neighborhood_k']))
        labels = np.full(len(z), -1)
        if name != 'physical':
            params = {key: value for key, value in config['clustering'].items() if key != 'method'}
            labels = HDBSCAN(**params, n_jobs=1, copy=True).fit_predict(z)
            metric['clustering'] = cluster_report(z, labels, subset)
            metric['clustering_sensitivity'] = []
            for count in [5, 20]:
                alt = HDBSCAN(**{**params, 'min_samples': count}, n_jobs=1, copy=True).fit_predict(z)
                joint = (labels >= 0) & (alt >= 0)
                metric['clustering_sensitivity'].append({'min_samples': count,
                    'cluster_count': len(set(alt)-{-1}), 'unassigned_fraction': float((alt < 0).mean()),
                    'adjusted_rand_including_noise': float(adjusted_rand_score(labels, alt)),
                    'both_assigned_points': int(joint.sum()),
                    'adjusted_rand_both_assigned': float(adjusted_rand_score(labels[joint], alt[joint])) if joint.sum() else None})
        positions[name] = xy; clusters[name] = labels; masks[name] = selected
        if name == 'trajectory':
            metric['umap_sensitivity'] = []
            for alternative in config['sensitivity']:
                params = {**config['umap'], **alternative}
                altxy = umap.UMAP(**params).fit_transform(z)
                key = f'trajectory_n{params["n_neighbors"]}_seed{params["random_state"]}'
                positions[key] = altxy
                altmetric, _, altlow = diagnostics(z, altxy, subset, config['neighborhood_k'])
                metric['umap_sensitivity'].append({'parameters': alternative,
                    'original_neighbor_recall_in_2d': altmetric['original_neighbor_recall_in_2d'],
                    'trustworthiness_sampled_anchors': altmetric['trustworthiness_sampled_anchors'],
                    'primary_2d_neighbor_recall_in_alternative': overlap(low, altlow)})
        report['maps'][name] = metric
        print('UMAP_RESULT', name, json.dumps(metric), flush=True)
    # Only now open optical classes. Radar-derived descriptors above were also
    # excluded from the learned UMAP and density-cluster feature matrices.
    add_reference_metadata(ROOT, rows)
    all_clusters = np.full(len(rows), -1, dtype=int)
    all_clusters[masks['trajectory']] = clusters['trajectory']
    for i, row in enumerate(rows):
        row['trajectory_cluster'] = int(all_clusters[i])
        for name in ['trajectory', 'change', 'physical']:
            index = int(np.cumsum(masks[name])[i])-1
            row[name+'_umap_1'] = float(positions[name][index, 0]) if masks[name][i] else None
            row[name+'_umap_2'] = float(positions[name][index, 1]) if masks[name][i] else None
    report['water_reference_counts_after_unsupervised_fit'] = {str(code): sum(r['water_reference_code'] == code for r in rows) for code in [0,1,2,3,4,255]}
    report['limitations'] = [
        'All existing scenes are consumed exploratory data. Joint UMAP/scaling fits include former geographic tests; there is no new held-out evaluation.',
        'One point per selected spatial block reduces dense local duplication but does not remove spatial dependence; scenes, not pixels, are the geographic replication units.',
        'Complete-trajectory selection excludes missing support and can bias the atlas. Scene caps are not global area weights.',
        'UMAP axes, orientation, gaps and apparent island sizes have no physical units; 2D density/separation does not prove original-space clusters.',
        'Temporal mean pooling loses ordering; full contextual states still depend on record duration and acquisition sampling. The separate change map uses a different summary.',
        'Per-trajectory normalization removes absolute backscatter levels. Nearby learned vectors can have different measured levels or causes.',
        'HDBSCAN groups and optical reference colors are exploratory metadata, not trained or validated flood, fire, agriculture, landslide or earthquake labels.',
        'InSAR phase/coherence are excluded from the power-trained encoder. No deformation or earthquake separation is implied.']
    write_json(output/'points.json', rows); write_json(output/'summary.json', report)
    write_json(output/'scaling.json', scalings)
    references = []
    for case in case_inventory(ROOT):
        directory = (ROOT/case['cube_path']).parent
        references.extend(p for p in [directory/'reference_pair.npz', directory/'reference_pair.json'] if p.exists())
    write_json(output/'reference_provenance.json', {
        'scope': 'Independent optical metadata attached after all unsupervised fits; no reference-conditioned sampling',
        'files': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in references}})
    with (output/'points.csv').open('w', newline='', encoding='utf8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader()
        writer.writerows([{**r, 'dates': '|'.join(r['dates'])} for r in rows])
    np.savez_compressed(output/'positions.npz', **positions,
                        **{name+'_clusters': value for name, value in clusters.items()},
                        **{name+'_mask': value for name, value in masks.items()})
    from surface_dynamics.embedding_atlas_render import render
    render(output, config, rows, features, positions, clusters, masks, report)
    artifacts = artifact_manifest(output)
    print('UMAP_COMPLETE', json.dumps({'scenes': report['case_count'], 'points': len(rows), 'artifacts': len(artifacts)}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'products/representation_umap')
    parser.add_argument('--reuse-features', action='store_true')
    parser.add_argument('--render-only', action='store_true', help='Redraw saved coordinates without refitting UMAP or density groups')
    args = parser.parse_args(); run(args.output, args.reuse_features, args.render_only)
