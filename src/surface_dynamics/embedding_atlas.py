"""Label-blind sampling and frozen-state extraction for exploratory UMAP maps.

The temporal encoder and previously frozen experiments are not modified. Each
point is one complete, measured HH/HV trajectory at an explicit 160 m posting.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from rasterio.warp import transform

from .analysis import measured_db, elapsed_days
from .behavior import summarize
from .dynamic_view import constant_control_states
from .io import load_cube, validate_cube
from .transformer import sequences, collate, model_from_checkpoint


def aggregate_hh_hv(cube, posting=160):
    """Strict integer block arithmetic power means before any logarithm."""
    validate_cube(cube)
    pols = list(map(str, cube['polarizations']))
    if str(cube['radar_band']) != 'L' or not {'HH', 'HV'} <= set(pols):
        raise ValueError('Atlas requires measured L-band HH/HV')
    dx = float(cube['x'][1] - cube['x'][0])
    factor = int(round(posting / dx))
    if factor < 1 or not np.isclose(dx * factor, posting) or not np.isclose(cube['y'][0] - cube['y'][1], dx):
        raise ValueError('Atlas requires explicit integer square power blocks')
    t, _, h, w = cube['power'].shape
    if h % factor or w % factor:
        raise ValueError('Atlas never silently crops block edges')
    ids = [pols.index('HH'), pols.index('HV')]
    a = cube['power'][:, ids]
    valid = cube['valid'][:, ids].astype(bool) & np.isfinite(a) & (a > 0)
    shape = (t, 2, h // factor, factor, w // factor, factor)
    ok = valid.reshape(shape).all(axis=(3, 5))
    power = np.where(valid, a, 0).reshape(shape).mean(axis=(3, 5))
    result = {**cube, 'power': np.where(ok, power, np.nan).astype('float32'),
              'valid': ok, 'polarizations': np.array(['HH', 'HV']),
              'x': cube['x'].reshape(-1, factor).mean(1),
              'y': cube['y'].reshape(-1, factor).mean(1)}
    return validate_cube(result)


def sample_blocks(complete, maximum, block, seed):
    """At most one complete trajectory per spatial block, without reading labels."""
    if maximum < 1 or block < 1:
        raise ValueError('Positive sampling bounds are required')
    rng = np.random.default_rng(seed)
    h, w = complete.shape
    ids = []
    for y in range(0, h, block):
        for x in range(0, w, block):
            yy, xx = np.nonzero(complete[y:y+block, x:x+block])
            if len(yy):
                j = int(rng.integers(len(yy)))
                ids.append(int((yy[j] + y) * w + xx[j] + x))
    ids = np.asarray(ids, dtype=int)
    if len(ids) > maximum:
        ids = rng.choice(ids, maximum, replace=False)
    return np.sort(ids)


def case_inventory(root):
    root = Path(root)
    recipe = json.loads((root / 'configs/global_v2.json').read_text())
    descriptions = {c['id']: c for c in json.loads((root / 'configs/global_cases.json').read_text())}
    descriptions.update({c['id']: c for c in recipe['new_regions']})
    cases = []
    for role, key in [('encoder_training_region', 'training_regions'),
                      ('legacy_development', 'legacy_development_regions'),
                      ('consumed_geographic_test', 'locked_test_regions')]:
        for name in recipe[key]:
            cases.append({**descriptions.get(name, {}), 'id': name, 'role': role,
                          'cube_path': f'products/global_v2/cubes/{name}.npz'})
    old = json.loads((root / 'configs/flood_pilot.json').read_text())['cases']
    new = json.loads((root / 'configs/characterization_v05.json').read_text())['cases']
    for group, items in [('flood_pilot', old), ('characterization_v05', new)]:
        for c in items:
            cases.append({**c, 'role': c.get('role', 'flood_development'),
                          'cube_path': f'products/{group}/cases/{c["id"]}/cube.npz'})
    return cases


def extract_atlas(root, config):
    root = Path(root)
    checkpoint = root / 'models/global_explorer_v2/masked_temporal_transformer.pt'
    saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
    if saved.get('architecture_id') != 'visible_normalized_residual_transformer_v2' or saved['posting_m'] != config['posting_m']:
        raise ValueError('Unexpected frozen model/domain')
    model = model_from_checkpoint(saved).eval()
    torch.set_num_threads(4)
    records, rows, admissions = [], [], []
    for case in case_inventory(root):
        path = root / case['cube_path']
        if not path.exists():
            admissions.append({'case_id': case['id'], 'status': 'missing_input'})
            continue
        cube = aggregate_hh_hv(load_cube(path), config['posting_m'])
        db = measured_db(cube)
        complete = np.isfinite(db).all(axis=(0, 1))
        scene_seed = config['sampling_seed'] + int(hashlib.sha256(case['id'].encode()).hexdigest()[:8], 16)
        ids = sample_blocks(complete, config['maximum_pixels_per_scene'], config['sampling_block_pixels'], scene_seed)
        t, _, h, w = db.shape
        admission = {'case_id': case['id'], 'status': 'included' if len(ids) and t >= 3 else 'insufficient_support',
                     'grid_cells': int(complete.size), 'complete_cells': int(complete.sum()),
                     'sampled_cells': len(ids), 'dates': cube['dates'].astype(str).tolist(), 'posting_m': 160,
                     'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        admissions.append(admission)
        print('ATLAS_SUPPORT', json.dumps(admission), flush=True)
        if admission['status'] != 'included':
            continue
        x, p, days = sequences(cube)
        batch = collate([(x[i], p[i], days) for i in ids], np.zeros(4), np.ones(4))
        with torch.no_grad():
            _, actual = model(*batch)
            controlled = (actual - constant_control_states(model, *batch)).numpy()
        trajectories = db.reshape(t, 2, -1)[:, :, ids].transpose(2, 0, 1)
        before = after = np.array([], dtype=int)
        event = None
        if 'baseline_start' in case:
            baseline = {'start_date': case['baseline_start'], 'end_date': case['baseline_end']}
            assessment = {'start_date': case['assessment_start'], 'end_date': case['assessment_end']}
            event = summarize(cube, baseline, assessment)
            if event['status'] == 'characterized':
                before, after = event['before'], event['after']
        change = np.full((len(ids), 48), np.nan, dtype='float32')
        if len(before) and len(after):
            change = controlled[:, after[0]] - controlled[:, before[-1]]
        med = np.median(trajectories, axis=1)
        mad = 1.4826 * np.median(abs(trajectories - med[:, None]), axis=1)
        net = trajectories[:, -1] - trajectories[:, 0]
        step = abs(np.diff(trajectories, axis=1)).max(1)
        # Measured descriptors are separate from the learned vectors.
        physical = np.concatenate([med, mad, net, step], axis=1)
        rr, cc = np.unravel_index(ids, (h, w))
        lon, lat = transform(f'EPSG:{int(cube["epsg"])}', 'EPSG:4326', cube['x'][cc].tolist(), cube['y'][rr].tolist())
        for j, (row, col) in enumerate(zip(rr, cc)):
            target = event['target_change_db'][:, row, col] if event is not None and len(after) else [None, None]
            rows.append({'point_id': f'{case["id"]}:{row}:{col}', 'case_id': case['id'], 'role': case['role'],
                         'row': int(row), 'column': int(col), 'longitude': lon[j], 'latitude': lat[j],
                         'epsg': int(cube['epsg']), 'x_m': float(cube['x'][col]), 'y_m': float(cube['y'][row]),
                         'dates': cube['dates'].astype(str).tolist(), 'date_count': t,
                         'span_days': float(days[-1]), 'median_gap_days': float(np.median(np.diff(days))),
                         'max_gap_days': float(np.max(np.diff(days))),
                         'median_hh_db': float(med[j, 0]), 'median_hv_db': float(med[j, 1]),
                         'mad_hh_db': float(mad[j, 0]), 'mad_hv_db': float(mad[j, 1]),
                         'net_hh_db': float(net[j, 0]), 'net_hv_db': float(net[j, 1]),
                         'max_step_hh_db': float(step[j, 0]), 'max_step_hv_db': float(step[j, 1]),
                         'target_hh_change_db': float(target[0]) if target[0] is not None else None,
                         'target_hv_change_db': float(target[1]) if target[1] is not None else None,
                         'behavior_code': int(event['pattern_code'][row, col]) if event is not None and len(after) else 255,
                         'water_reference_code': 255,
                         'baseline_last_date': str(cube['dates'][before[-1]]) if len(before) else None,
                         'assessment_first_date': str(cube['dates'][after[0]]) if len(after) else None})
        records.append({'case_id': case['id'], 'trajectory': controlled.mean(1), 'change': change,
                        'physical': physical, 'trajectories': trajectories, 'pixel_ids': ids,
                        'grid_shape': [h, w], 'dates': cube['dates'].astype(str).tolist()})
    if not records:
        raise ValueError('No compatible complete trajectories')
    return records, rows, admissions


def add_reference_metadata(root, rows):
    """Open reference classes only AFTER all label-blind fits and sampling."""
    from .flood import load_reference
    from .flood_signatures import reference_groups
    from .representation_probe import coarsen
    root = Path(root)
    for case in case_inventory(root):
        selected = [r for r in rows if r['case_id'] == case['id']]
        if not selected or 'baseline_start' not in case:
            continue
        path = root / case['cube_path']
        if not path.with_name('reference_pair.npz').exists():
            continue
        cube = load_cube(path)
        reference, _ = load_reference(path.with_name('reference_pair.npz'), cube,
                                      selected[0]['baseline_last_date'], selected[0]['assessment_first_date'],
                                      case['assessment_start'])
        if reference is None:
            continue
        _, coarse_ref = coarsen(cube, reference)
        labels = reference_groups(coarse_ref)
        for row in selected:
            row['water_reference_code'] = int(labels[row['row'], row['column']])


def standardize(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or not len(values) or not np.isfinite(values).all():
        raise ValueError('Atlas distance features must be finite, nonempty matrices')
    center = values.mean(0)
    scale = np.maximum(values.std(0), 0.0001)
    return (values-center)/scale, center, scale
