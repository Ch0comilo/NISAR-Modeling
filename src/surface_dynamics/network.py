"""Catalog-only InSAR acquisition networks; no phase inversion or motion claim."""
from collections import deque
from datetime import date
from pathlib import Path
import json
import re

from .catalog import group_entries, search


def acquisition_dates(title):
    """Reference and secondary starts, never the reference start/end pair."""
    times = re.findall(r'(\d{8}T\d{6})', title)
    if len(times) != 4:
        raise ValueError('GUNW must identify four acquisition timestamps')
    return tuple(f'{times[i][:4]}-{times[i][4:6]}-{times[i][6:8]}' for i in (0, 2))


def build_network(entries, max_baseline_days=48, event_date=None):
    if not 1 <= max_baseline_days <= 366:
        raise ValueError('max_baseline_days must be between 1 and 366')
    event = date.fromisoformat(event_date) if event_date else None
    networks = []
    # Each group has one track, pass, frame, bandwidth and polarization mode.
    # Acquisitions are day nodes; precise timestamps remain in each source ID.
    for key, series in sorted(group_entries(entries, 'GUNW').items()):
        edges = []
        all_dates = set()
        rejected = 0
        for entry in series:
            reference, secondary = acquisition_dates(entry['title'])
            a, b = date.fromisoformat(reference), date.fromisoformat(secondary)
            all_dates.update((reference, secondary))
            days = abs((b - a).days)
            if not days or days > max_baseline_days:
                rejected += 1
                continue
            edges.append({'id': entry['title'], 'reference_date': reference,
                          'secondary_date': secondary, 'temporal_baseline_days': days,
                          'brackets_context_date': min(a, b) < event < max(a, b) if event else None})
        # Revisions or sub-second timestamp changes do not create parallel day
        # edges and spurious two-edge cycles. Retain latest source ID per pair.
        by_pair = {}
        for edge in edges:
            identity = tuple(sorted((edge['reference_date'], edge['secondary_date'])))
            if identity not in by_pair or edge['id'] > by_pair[identity]['id']:
                by_pair[identity] = edge
        edges = sorted(by_pair.values(), key=lambda e: e['id'])
        nodes = sorted(all_dates)
        parent = {node: node for node in nodes}

        def find(node):
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        tree, chords = [], []
        adjacency = {node: [] for node in nodes}
        for edge in sorted(edges, key=lambda e: (e['temporal_baseline_days'], e['id'])):
            a, b = edge['reference_date'], edge['secondary_date']
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
                tree.append(edge['id'])
                adjacency[a].append((b, edge['id']))
                adjacency[b].append((a, edge['id']))
            else:
                chords.append(edge)
        groups = {}
        for node in nodes:
            groups.setdefault(find(node), []).append(node)
        # Fundamental cycles give actual available closure paths, including
        # cycles longer than triangles. They do not constitute measured closure.
        cycles = []
        for edge in chords:
            start, target = edge['reference_date'], edge['secondary_date']
            queue = deque([start]); previous = {start: None}
            while queue and target not in previous:
                node = queue.popleft()
                for neighbor, edge_id in adjacency[node]:
                    if neighbor not in previous:
                        previous[neighbor] = (node, edge_id); queue.append(neighbor)
            path = []; node = target
            while node != start:
                node, edge_id = previous[node]; path.append(edge_id)
            cycles.append({'edge_ids': list(reversed(path)) + [edge['id']],
                           'phase_closure_status': 'not_measured_catalog_only'})
        networks.append({'series_key': key, 'dates': nodes, 'edges': edges,
                         'connected_components': sorted(groups.values()),
                         'connected_component_count': len(groups),
                         'cycle_rank': len(edges) - len(nodes) + len(groups),
                         'minimum_temporal_baseline_forest': tree,
                         'independent_closure_cycles': cycles,
                         'pairs_excluded_by_baseline': rejected,
                         'spatial_phase_support': 'unknown_until_quantitative_pair_reads',
                         'time_series_inversion': 'not_performed'})
    return networks


def plan_network(request, output):
    from .engine import validate_request, validate_output
    validate_request(request, 'insar-network')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    source = request['source']
    if source['mode'] == 'catalog_point':
        catalog = search(source['longitude'], source['latitude'], 'GUNW',
                         start=source['start_date'] + 'T00:00:00Z',
                         end=source['end_date'] + 'T23:59:59Z')
    else:
        catalog = json.loads(Path(source['catalog_path']).read_text(encoding='utf8'))
    networks = build_network(catalog['entries'], request.get('max_baseline_days', 48),
                             request.get('event_date'))
    result = {'contract_version': '1.0', 'request_id': request['request_id'],
              'status': 'complete' if any(n['edges'] for n in networks) else 'no_data',
              'analysis': 'catalog_interferometric_network', 'networks': networks,
              'catalog': {k: catalog[k] for k in ['query_url', 'queried_at_utc', 'hits'] if k in catalog},
              'limitations': [
                  'Catalog topology only. No phase closure, SBAS inversion, displacement or forecasting is computed.',
                  'Separate track/pass/frame/bandwidth/polarization groups are never joined.',
                  'Day nodes retain exact acquisition timestamps in source IDs; temporal baseline is not perpendicular baseline.',
                  'Graph connectivity does not establish coherent spatial support, common datum, corrections or usable ground motion.',
                  'The date window filters catalog reference acquisitions; secondary dates may lie outside that window.'
              ]}
    validate_output(result, 'insar-network')
    (output/'request.json').write_text(json.dumps(request, indent=2))
    (output/'catalog.json').write_text(json.dumps(catalog, indent=2))
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    return result
