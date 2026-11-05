"""Scientific static figures and self-contained interactive UMAP plot files."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import numpy as np
from scipy.spatial.distance import cdist

from .embedding_atlas import standardize


def frame(ax, title):
    ax.set_title(title, loc='left', fontsize=11, pad=8)
    ax.set_xlabel('UMAP 1 (arbitrary units)'); ax.set_ylabel('UMAP 2 (arbitrary units)')
    ax.spines[['top', 'right']].set_visible(False)


def dots(ax, xy, values, title, categorical=False, legend=False):
    if categorical:
        names = sorted(set(values), key=str)
        palette = plt.get_cmap('tab20', max(20, len(names)))
        for i, name in enumerate(names):
            chosen = np.array(values) == name
            color = '#c4c8ce' if name in [-1, 'unassigned', 'unknown', 'insufficient_temporal_support'] else palette(i)
            water_colors = {'stable nonwater': '#ac7f38', 'new open water': '#0072b2',
                            'stable open water': '#56b4e9', 'open-water loss': '#d55e00', 'mixed': '#b293c4'}
            color = water_colors.get(name, color)
            ax.scatter(xy[chosen, 0], xy[chosen, 1], s=8, c=[color], alpha=.7, linewidths=0, label=str(name).replace('_', ' '))
        if legend: ax.legend(fontsize=7, loc='best', markerscale=2, framealpha=.9)
    else:
        values = np.asarray(values)
        limit = float(np.quantile(abs(values), .97)) or 1
        if 'change' in title.lower() or 'net HH' in title:
            im = ax.scatter(*xy.T, c=values, s=7, cmap='RdBu_r', vmin=-limit, vmax=limit, linewidths=0, alpha=.8)
        else:
            im = ax.scatter(*xy.T, c=values, s=7, cmap='viridis', linewidths=0, alpha=.8)
        ax.figure.colorbar(im, ax=ax, shrink=.8, pad=.02)
    frame(ax, title)


def save(fig, output, name):
    fig.savefig(Path(output)/(name+'.png'), dpi=145, facecolor='white')
    fig.savefig(Path(output)/(name+'.pdf'), facecolor='white')
    plt.close(fig)


def prototypes(rows, features, labels, maximum, feature_name='trajectory'):
    z, _, _ = standardize(features[feature_name])
    groups = sorted(set(labels)-{-1}, key=lambda c: (-int((labels == c).sum()), c))[:maximum]
    examples = []
    if not groups:
        # Never force a density cluster. Show deterministic spread-out neighborhoods.
        anchors = [int(np.argmin(np.sum((z-z.mean(0))**2, axis=1)))]
        while len(anchors) < min(maximum, len(z)):
            distance = cdist(z, z[anchors]).min(1)
            anchors.append(int(np.argmax(distance)))
        groups = [None]*len(anchors)
    for i, group in enumerate(groups):
        if group is None:
            anchor = anchors[i]
        else:
            members = np.flatnonzero(labels == group)
            center = z[members].mean(0)
            anchor = int(members[np.argmin(np.sum((z[members]-center)**2, axis=1))])
        dist = np.linalg.norm(z-z[anchor], axis=1)
        order = np.argsort(dist, kind='stable')
        other = [int(j) for j in order if rows[j]['case_id'] != rows[anchor]['case_id']]
        same = [int(j) for j in order if j != anchor and rows[j]['case_id'] == rows[anchor]['case_id']]
        selected = [anchor] + same[:1] + other[:1]
        examples.append({'cluster_id': int(group) if group is not None else None,
                         'population': int((labels == group).sum()) if group is not None else None,
                         'anchor_index': anchor, 'example_indices': selected,
                         'examples': [{**rows[j], 'original_48d_distance_from_anchor': float(dist[j]),
                                       'original_48d_neighbor_rank': int(np.flatnonzero(order == j)[0]),
                                       'hh_db': features['observed_db'][j, :rows[j]['date_count'], 0].tolist(),
                                       'hv_db': features['observed_db'][j, :rows[j]['date_count'], 1].tolist()} for j in selected]})
    return examples


def interactive(output, name, rows, xy, observed_db, labels):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=2, cols=2, specs=[[{'rowspan': 2}, {}], [None, {}]],
                        column_widths=[.58, .42], horizontal_spacing=.16,
                        subplot_titles=['Click a point to inspect its measured history', 'HH (measured)', 'HV (measured)'])
    scenes = sorted(set(r['case_id'] for r in rows)); scene_id = {s: i for i, s in enumerate(scenes)}
    colors = [scene_id[r['case_id']] for r in rows]
    hover = [f"{r['point_id']}<br>{r['date_count']} actual dates | {r['span_days']:.0f} days"
             f"<br>HH MAD: {r['mad_hh_db']:.2f} dB | net: {r['net_hh_db']:+.2f} dB"
             f"<br>{r['longitude']:.5f}, {r['latitude']:.5f}<br>Original 48D group: {labels[i]}"
             for i, r in enumerate(rows)]
    fig.add_trace(go.Scattergl(x=xy[:, 0], y=xy[:, 1], mode='markers', customdata=list(range(len(rows))),
                              hovertext=hover, hoverinfo='text', name='Trajectories',
                              marker={'size': 5, 'opacity': .7, 'color': colors, 'colorscale': 'Turbo',
                                      'showscale': False,
                                      'colorbar': {'title': 'Scene', 'tickvals': list(range(len(scenes))),
                                                   'ticktext': scenes, 'len': .8, 'thickness': 10, 'x': .51,
                                                   'tickfont': {'size': 8}}}), row=1, col=1)
    for channel in range(2):
        fig.add_trace(go.Scatter(x=rows[0]['dates'], y=observed_db[0, :rows[0]['date_count'], channel],
                                mode='lines+markers', name=['HH', 'HV'][channel], line={'color': ['#127a9e', '#b25b20'][channel]}),
                      row=channel+1, col=2)
    buttons = []
    for title, field, continuous in [('Scene', None, False), ('HH dispersion (dB)', 'mad_hh_db', True),
                                     ('Net HH change (dB)', 'net_hh_db', True), ('Acquisition count', 'date_count', True),
                                     ('Record duration (days)', 'span_days', True), ('Original 48D group', 'trajectory_cluster', False),
                                     ('Optical water reference', 'water_reference_code', False)]:
        a = colors if field is None else labels.tolist() if field == 'trajectory_cluster' else [r[field] for r in rows]
        short = {'mad_hh_db':'MAD (dB)', 'net_hh_db':'ΔHH (dB)', 'date_count':'Dates',
                 'span_days':'Days', 'trajectory_cluster':'Group', 'water_reference_code':'Reference'}
        colorbar = {'title': {'text': short.get(field, title), 'font': {'size': 9}}, 'len': .8, 'thickness': 10, 'x': .51, 'tickfont': {'size': 8}}
        if field is None: colorbar.update(tickvals=list(range(len(scenes))), ticktext=scenes)
        elif field == 'water_reference_code':
            colorbar.update(tickvals=[0,1,2,3,4,5], ticktext=['nonwater','new water','stable water','water loss','mixed','unknown'])
            a = [5 if v == 255 else v for v in a]
        elif field == 'trajectory_cluster':
            codes = sorted(set(a)); colorbar.update(tickvals=codes, ticktext=['unassigned' if v == -1 else f'C{v}' for v in codes])
        buttons.append({'label': title, 'method': 'restyle', 'args': [{'marker.color': [a],
                       'marker.colorscale': 'Viridis' if continuous else 'Turbo', 'marker.colorbar': colorbar,
                       'marker.showscale': field is not None}, [0]]})
    fig.update_layout(template='plotly_white', height=790, title={'text': f'Frozen Transformer UMAP: {name}<br><sup>One point = one observed 160 m HH/HV history. Hover for scene names; click for curves.</sup>', 'y': .98, 'yanchor': 'top', 'font': {'size': 17}},
                      margin={'t': 150, 'b': 105, 'l': 60, 'r': 20}, showlegend=False,
                      updatemenus=[{'buttons': buttons, 'x': 0, 'y': 1.12, 'xanchor': 'left'}],
                      annotations=list(fig.layout.annotations)+[{'text': 'UMAP axes have no physical units. All scenes jointly fitted; no held-out accuracy.<br>Optical colors describe product transitions; unknown/mixed retained. Labels attached after fit.',
                          'xref': 'paper', 'yref': 'paper', 'x': 0, 'y': -.12, 'showarrow': False, 'xanchor': 'left', 'font': {'size': 11}}])
    fig.update_xaxes(title_text='UMAP 1 (arbitrary units)', row=1, col=1)
    fig.update_yaxes(title_text='UMAP 2 (arbitrary units)', row=1, col=1)
    for channel in range(2): fig.update_yaxes(title_text='Gamma0 (dB)', row=channel+1, col=2)
    histories = [{'dates': r['dates'], 'hh': observed_db[i, :r['date_count'], 0].tolist(),
                  'hv': observed_db[i, :r['date_count'], 1].tolist(), 'id': r['point_id']} for i, r in enumerate(rows)]
    # A Plotly file, not an application/frontend modification. No network/CDN.
    script = 'const histories='+json.dumps(histories, allow_nan=False)+';\n'
    script += "const graph=document.getElementById('{plot_id}');graph.on('plotly_click',function(e){const p=e.points[0];if(p.curveNumber!==0)return;const h=histories[p.customdata];Plotly.restyle(graph,{x:[h.dates,h.dates],y:[h.hh,h.hv]},[1,2]);Plotly.relayout(graph,{'annotations[1].text':'HH: '+h.id,'annotations[2].text':'HV: '+h.id});});"
    fig.write_html(Path(output)/(name+'_interactive.html'), include_plotlyjs=True, full_html=True,
                   post_script=script, div_id='nisar-'+name, config={'displaylogo': False, 'modeBarButtonsToRemove': ['sendDataToCloud']})


def render(output, config, rows, features, positions, clusters, masks, report):
    output = Path(output)
    plt.rcdefaults(); plt.rcParams.update({'font.size': 9, 'axes.facecolor': 'white', 'figure.facecolor': 'white'})
    xy = positions['trajectory']; scenes = [r['case_id'] for r in rows]
    fig, axes = plt.subplots(2, 2, figsize=(14, 11), layout='constrained')
    fig.set_constrained_layout_pads(w_pad=.12, h_pad=.12, hspace=.08)
    dots(axes[0,0], xy, scenes, 'Same 2D map, colored by source scene', True)
    dots(axes[0,1], xy, [r['mad_hh_db'] for r in rows], 'Measured HH dispersion: scaled MAD (dB)')
    dots(axes[1,0], xy, [r['net_hh_db'] for r in rows], 'Measured net HH change: last minus first (dB)')
    dots(axes[1,1], xy, clusters['trajectory'].tolist(), 'Density groups fitted in original 48D; gray = unassigned', True, True)
    handles, labels = axes[0,0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5,.035), ncol=7, fontsize=8, markerscale=2, frameon=False)
    fig.suptitle(f'{len(rows):,} measured histories across {report["case_count"]} scenes | frozen Transformer → UMAP', fontsize=17)
    fig.get_layout_engine().set(rect=(0,.15,1,.76))
    fig.text(.015,.009,'48D temporal mean after constant-trajectory control; 160 m; label-blind fit. UMAP positions are exploratory, not physical coordinates or event classes.', fontsize=9)
    save(fig, output, 'trajectory_umap')
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.5), layout='constrained')
    dots(axes[0], xy, [r['date_count'] for r in rows], 'Acquisition count (possible confound)')
    dots(axes[1], xy, [r['span_days'] for r in rows], 'Record duration in days (possible confound)')
    dots(axes[2], positions['physical'], scenes, 'Separate UMAP of 8 measured descriptors', True)
    fig.suptitle('Check acquisition sampling and compare with measured physical descriptors', fontsize=15)
    fig.get_layout_engine().set(rect=(0,.09,1,.80))
    fig.text(.02,.025,'Physical descriptors: HH/HV median, scaled MAD, first-to-last change and largest step. Layout coordinates are not shared across separately fitted maps.',fontsize=9)
    save(fig, output, 'sampling_and_physical')
    fig, axes = plt.subplots(2, 2, figsize=(12, 10), layout='constrained')
    keys = ['trajectory']+[k for k in positions if k.startswith('trajectory_n')]
    for ax, key in zip(axes.flat, keys):
        label = 'Primary: neighbors=30, seed=37' if key == 'trajectory' else key.replace('trajectory_', '').replace('_', ', ')
        dots(ax, positions[key], scenes, label, True)
    fig.suptitle('All declared UMAP sensitivity runs; same points and source colors', fontsize=15)
    fig.get_layout_engine().set(rect=(0,.045,1,.87))
    fig.text(.02,.012,'Separate layouts can rotate, reflect or rearrange islands. Compare neighborhoods, not absolute 2D coordinates.',fontsize=9)
    save(fig, output, 'umap_sensitivity')
    selected = masks['change']; event_rows = [r for r, keep in zip(rows, selected) if keep]
    event_xy = positions['change']
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), layout='constrained')
    dots(axes[0,0], event_xy, [r['case_id'] for r in event_rows], 'Change map by source scene', True, True)
    dots(axes[0,1], event_xy, [r['target_hh_change_db'] for r in event_rows], 'Measured first-assessment HH change (dB)')
    water = {0:'stable nonwater',1:'new open water',2:'stable open water',3:'open-water loss',4:'mixed',255:'unknown'}
    dots(axes[1,0], event_xy, [water[r['water_reference_code']] for r in event_rows], 'Independent optical product, attached after fit', True, True)
    from .behavior import LEGEND
    dots(axes[1,1], event_xy, [LEGEND[r['behavior_code']].replace('observed_', '') for r in event_rows], 'Fixed observed-behavior rules, attached after fit', True, True)
    fig.suptitle(f'Baseline → first assessment: {len(event_rows):,} histories in eight characterization scenes', fontsize=15)
    fig.get_layout_engine().set(rect=(0,.055,1,.87))
    fig.text(.015,.015,'48D controlled state difference, last baseline → first assessment. Measured Δ uses baseline median. Optical groups are imperfect references, not event labels.', fontsize=9)
    save(fig, output, 'change_umap')
    examples = prototypes(rows, features, clusters['trajectory'], config['cluster_examples'])
    (output/'examples.json').write_text(json.dumps(examples, indent=2, allow_nan=False), encoding='utf8')
    render_examples(output, 'neighborhood_examples', examples, rows, features, xy, clusters['trajectory'])
    event_features = {key: a[selected] for key, a in features.items()}
    change_examples = prototypes(event_rows, event_features, clusters['change'], config['cluster_examples'], 'change')
    (output/'change_examples.json').write_text(json.dumps(change_examples, indent=2, allow_nan=False), encoding='utf8')
    render_examples(output, 'change_neighborhood_examples', change_examples, event_rows, event_features, event_xy, clusters['change'])
    interactive(output, 'trajectory', rows, xy, features['observed_db'], clusters['trajectory'])
    change_indices = np.flatnonzero(selected)
    interactive(output, 'change', event_rows, event_xy, features['observed_db'][change_indices], clusters['change'])


def render_examples(output, name, examples, rows, features, xy, labels):
    fig, axes = plt.subplots(len(examples), 3, figsize=(16, 3.25*len(examples)), squeeze=False, layout='constrained')
    line_colors = ['#172d48', '#d77724', '#128b82']
    for i, example in enumerate(examples):
        group = example['cluster_id']; anchor = example['anchor_index']; ax = axes[i,0]
        ax.scatter(*xy.T, c='#d7dce3', s=3, linewidths=0)
        if group is not None:
            member = labels == group
            ax.scatter(*xy[member].T, c='#558abb', s=5, linewidths=0)
        for j, (index, r) in enumerate(zip(example['example_indices'], example['examples'])):
            ax.scatter(*xy[index], s=65, c=line_colors[j], edgecolor='white', zorder=5)
            ax.annotate(str(j+1), xy[index], xytext=(5,5), textcoords='offset points', fontsize=9)
            elapsed = (np.array(r['dates'], dtype='datetime64[D]')-np.datetime64(r['dates'][0])).astype('timedelta64[D]').astype(float)
            for channel in range(2):
                label = f'{j+1}: {r["case_id"]} | d={r["original_48d_distance_from_anchor"]:.2f}'
                axes[i,channel+1].plot(elapsed, r[['hh_db','hv_db'][channel]], '.-', lw=1.3, color=line_colors[j], label=label)
        title = f'C{group}: {example["population"]} sampled histories' if group is not None else 'Neighborhood example (no density group)'
        frame(ax, title)
        for channel in range(2):
            a = axes[i,channel+1]; a.set_title(['Measured HH','Measured HV'][channel],loc='left',fontsize=11)
            a.set_xlabel('Actual days since each record starts'); a.set_ylabel('Gamma0 (dB)')
            a.legend(fontsize=7,loc='best'); a.spines[['top','right']].set_visible(False)
    fig.suptitle(('Change map' if name.startswith('change') else 'Trajectory map')+': group representative, same-scene neighbor, closest other-scene history',fontsize=15)
    fig.get_layout_engine().set(rect=(0,.045,1,.88))
    fig.text(.015,.012,'Distances use standardized original 48D, not UMAP. Other-scene neighbor may belong to another group or be far away. Different starts/durations remain visible.',fontsize=9)
    save(fig, output, name)
