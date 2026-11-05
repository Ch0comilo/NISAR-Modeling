"""Scientific feature plots: baseline bounds, independent evidence and analogs."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS=['#687987','#0072b2','#d55e00']


def save(fig,out,name):
    fig.savefig(out/(name+'.png'),dpi=140,facecolor='white')
    fig.savefig(out/(name+'.pdf'),facecolor='white');plt.close(fig)


def select_examples(rows):
    chosen=[]
    water=[r for r in rows if r['case_id']=='qadirabad' and r['water_reference_code']==1]
    if water:
        median=np.median([r['features']['first_change_db'][0] for r in water])
        positive=min(water,key=lambda r:(abs(r['features']['first_change_db'][0]-median),r['point_id']))
        controls=[r for r in rows if r['case_id']=='qadirabad' and r['water_reference_code']==0]
        level=np.array(positive['features']['baseline_db'])
        controls=[r for r in controls if np.max(abs(np.array(r['features']['baseline_db'])-level))<=2]
        if controls:
            control=min(controls,key=lambda r:(np.linalg.norm(np.array(r['features']['baseline_db'])-level),r['point_id']))
            chosen.append({'description':'Baseline-level-matched optical nonwater comparator; not assumed event-free','point':control})
        chosen.append({'description':'New-open-water product reference; near the sampled group median HH change','point':positive})
    quiet=[r for r in rows if r['features']['state']=='within_observed_baseline_bounds' and r['water_reference_code']==0]
    if quiet:
        point=min(quiet,key=lambda r:(r['features']['maximum_scaled_departure'],r['point_id']))
        chosen.append({'description':'Small departure within the diagnostic bounds; not verified normal conditions','point':point})
    wheat=[r for r in rows if r['case_id']=='wheatbelt' and r['features']['state']=='outside_observed_baseline_bounds']
    if wheat:
        target=np.quantile([r['features']['maximum_scaled_departure'] for r in wheat],.75)
        point=min(wheat,key=lambda r:(abs(r['features']['maximum_scaled_departure']-target),r['point_id']))
        chosen.append({'description':'Wheatbelt departure near the flagged sample\'s 75th score percentile; crop cause unavailable','point':point})
    return chosen


def examples_plot(out,examples,name='baseline_examples'):
    fig,axes=plt.subplots(len(examples),2,figsize=(13,3*len(examples)),squeeze=False,sharey='col',layout='constrained')
    for i,example in enumerate(examples):
        r=example['point'];v=r['features'];dates=np.array(r['full_record_dates'],dtype='datetime64[D]')
        for c in range(2):
            ax=axes[i,c];ax.plot(dates,r[['hh_db','hv_db'][c]],'.-',color=['#0072b2','#b66728'][c],lw=1.4)
            base=v['baseline_db'][c];bound=v['diagnostic_half_width_db'][c]
            if bound is not None:
                ax.axhspan(base-bound,base+bound,color='#8ca0a7',alpha=.18,label='Baseline diagnostic band')
                ax.axhline(base,color='#687987',lw=.7,ls='--')
            b=np.array(v['baseline_dates'],dtype='datetime64[D]');a=np.array(v['assessment_dates'],dtype='datetime64[D]')
            ax.axvspan(a[0],dates[-1],color='#eabf63',alpha=.08)
            title=r['point_id']
            if name=='baseline_comparison':
                title='Qadirabad: '+('new-open-water reference' if r['water_reference_code']==1 else 'reference-stable nonwater')
            ax.set_title(title+' | '+['HH','HV'][c],loc='left',fontsize=10)
            ax.set_ylabel('Gamma0 (dB)');ax.set_xlabel('Actual acquisition date (UTC)')
            ax.tick_params(axis='x',labelrotation=20,labelsize=8);ax.spines[['top','right']].set_visible(False)
            if c==0:
                score=v['maximum_scaled_departure'];score='unavailable' if score is None else f'{score:.2f}'
                ax.text(.01,.02,f'{v["state"].replace("_"," ")}\nmaximum baseline-scaled departure: {score}',transform=ax.transAxes,fontsize=7,
                        bbox={'facecolor':'white','alpha':.8,'edgecolor':'none'})
    fig.suptitle('Different measured behaviors relative to each location\'s observed baseline',fontsize=15)
    fig.get_layout_engine().set(rect=(0,.045,1,.89))
    fig.text(.015,.012,'Gray bands = median ± max(3 dB, 3 × max(baseline scaled MAD, 1 dB)); not confidence intervals. Gold = observed assessment interval. Selection rules are in examples.json.',fontsize=8)
    save(fig,out,name)


def reference_plot(out,cases):
    names=[c['case_id'] for c in cases if c['reference'].get('groups',{}).get('1',{}).get('cells',0)>1]
    if not names:return
    fig,axes=plt.subplots(len(names),3,figsize=(11,3.1*len(names)),squeeze=False,sharey='col',layout='constrained')
    for i,name in enumerate(names):
        maps=dict(np.load(out/(name+'_maps.npz'),allow_pickle=False));groups=np.load(out/(name+'_reference.npz'))['groups']
        for j,(key,field,title) in enumerate([('first_change_db',0,'First HH change (dB)'),('first_change_db',1,'First HV change (dB)'),('departure_frequency',None,'Observed departure fraction')]):
            ax=axes[i,j];a=maps[key] if field is None else maps[key][field]
            values=[a[(groups==code)&np.isfinite(a)] for code in [0,1,2]]
            use=[k for k,x in enumerate(values) if len(x)]
            if use:
                bp=ax.boxplot([values[k] for k in use],positions=np.array(use)+1,widths=.55,whis=(5,95),showfliers=False,patch_artist=True)
                for patch,k in zip(bp['boxes'],use):patch.set_facecolor(COLORS[k]);patch.set_alpha(.65)
                ax.set_xticks(np.arange(3)+1,['Stable nonwater\nN='+str(len(values[0])),'New open water\nN='+str(len(values[1])),'Stable open water\nN='+str(len(values[2]))],fontsize=7)
            ax.set_title(name.replace('_',' ')+': '+title,loc='left',fontsize=10)
            ax.set_ylabel(title);ax.spines[['top','right']].set_visible(False)
            if field is not None:ax.axhline(0,lw=.7,color='#555555')
            else:ax.set_ylim(-.03,1.03)
    fig.suptitle('Independent water-reference groups have overlapping radar signatures',fontsize=14)
    fig.get_layout_engine().set(rect=(0,.09,1,.86))
    fig.text(.015,.025,'All qualified common 160 m cells; optical product groups are not surveyed flood truth or verified normality. Boxes: 25–75%; line: median; whiskers: 5–95%.',fontsize=8)
    save(fig,out,'reference_feature_distributions')


def spatial_plot(out,name):
    m=dict(np.load(out/(name+'_maps.npz'),allow_pickle=False));g=np.load(out/(name+'_reference.npz'))['groups']
    panels=[(m['first_change_db'][0],'First HH change (dB)','RdBu_r',-6,6),
        (m['first_change_db'][1],'First HV change (dB)','RdBu_r',-6,6),
        (m['ratio_change_db'],'Median HV−HH ratio change (dB)','RdBu_r',-4,4),
        (m['first_scaled_departure'],'First baseline-scaled departure','magma',0,8),
        (m['departure_frequency'],'Fraction of assessment dates outside bounds','viridis',0,1),
        (m['local_co_contrast_db'],'Local HH contrast against 8 neighbors (dB)','RdBu_r',-3,3),
        (m['local_co_spatial_mad_change_db'],'Change of neighborhood HH scaled MAD (dB)','RdBu_r',-2,2),
        (np.where(g==255,np.nan,g),'Independent water product code','tab10',0,4),
        (np.where(m['state_code']==255,np.nan,m['state_code']),'Baseline state: 0 insufficient / 1 within / 2 outside','cividis',0,2)]
    fig,axes=plt.subplots(3,3,figsize=(13,12),layout='constrained')
    extent=[(m['x'][0]-80)/1000,(m['x'][-1]+80)/1000,(m['y'][-1]-80)/1000,(m['y'][0]+80)/1000]
    for ax,(a,title,cmap,lo,hi) in zip(axes.flat,panels):
        a=np.where(m['common_support'],a,np.nan)
        im=ax.imshow(a,extent=extent,cmap=cmap,vmin=lo,vmax=hi,interpolation='nearest');fig.colorbar(im,ax=ax,shrink=.75,pad=.02)
        ax.set_title(title,loc='left',fontsize=9);ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)')
    fig.suptitle(name.replace('_',' ')+': complementary features of the same measured surface',fontsize=15)
    fig.get_layout_engine().set(rect=(0,.055,1,.89))
    fig.text(.015,.015,'160 m grid, fixed common support. Water codes: 0 stable nonwater / 1 new water / 2 stable water / 3 water loss / 4 mixed; unknown masked. Display scales can saturate.',fontsize=8)
    save(fig,out,name+'_feature_maps')


def analog_plot(out,rows,analogs,metadata,arrays):
    lookup={r['point_id']:i for i,r in enumerate(metadata)}
    selected=[]
    for name in ['qadirabad','wheatbelt']:
        candidates=[r for r in rows if r['case_id']==name and r['analog_index'] is not None and
                    analogs[r['analog_index']]['methods'].get('hybrid',{}).get('neighbors')]
        if candidates:
            target=np.median([r['features']['first_change_db'][0] for r in candidates])
            selected.append(min(candidates,key=lambda r:(abs(r['features']['first_change_db'][0]-target),r['point_id'])))
    if not selected:return
    fig,axes=plt.subplots(len(selected),2,figsize=(13,3.7*len(selected)),squeeze=False,layout='constrained')
    choices=[]
    for row,r in enumerate(selected):
        i=r['analog_index'];neighbors=analogs[i]['methods'];series=[('Query',i,'#182c44')]
        for method,color in [('physical','#0072b2'),('learned','#d55e00'),('hybrid','#009e73')]:
            candidate=neighbors[method]['neighbors'][0];j=lookup[candidate['point_id']]
            status='outside envelope' if neighbors[method]['status']=='no_close_cross_region_analog' else 'within envelope'
            series.append((method+': '+candidate['case_id']+' ['+status+']',j,color))
        choices.append({'query_id':r['point_id'],'analog_index':i,'methods':neighbors})
        for c in range(2):
            ax=axes[row,c]
            for label,j,color in series:
                dates=np.array(metadata[j]['sampling']['dates'],dtype='datetime64[D]')
                elapsed=(dates-dates[0]).astype('timedelta64[D]').astype(float)
                ax.plot(elapsed,arrays['observed_db'][j,:,c],'.-',label=label,color=color,lw=1.4)
            ax.set_title(r['point_id']+' | '+['HH','HV'][c],loc='left',fontsize=10)
            ax.set_xlabel('Actual days since each six-date record begins');ax.set_ylabel('Gamma0 (dB)')
            ax.legend(fontsize=8);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Physical, learned and combined distances can select different analogs',fontsize=14)
    fig.get_layout_engine().set(rect=(0,.08,1,.88))
    fig.text(.015,.025,'Same development reference bank and acquisition-matched candidate pool; ≥200 km separation. Nearest history can remain outside the development distance envelope.',fontsize=8)
    save(fig,out,'analog_comparison');(out/'analog_examples.json').write_text(json.dumps(choices,indent=2),encoding='utf8')


def interactive(out,rows):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig=make_subplots(rows=2,cols=2,specs=[[{'rowspan':2},{}],[None,{}]],column_widths=[.58,.42],horizontal_spacing=.15,
        subplot_titles=['Measured first-assessment changes','HH (measured)','HV (measured)'])
    states=['insufficient_baseline','within_observed_baseline_bounds','outside_observed_baseline_bounds']
    for state,color in zip(states,['#a4adb5','#0072b2','#d55e00']):
        ids=[i for i,r in enumerate(rows) if r['features']['state']==state]
        fig.add_trace(go.Scattergl(x=[rows[i]['features']['first_change_db'][0] for i in ids],
            y=[rows[i]['features']['first_change_db'][1] for i in ids],mode='markers',name=state.replace('_',' '),
            marker={'size':5,'color':color,'opacity':.65},customdata=ids,
            hovertext=[rows[i]['point_id']+'<br>Water reference code: '+str(rows[i]['water_reference_code'])+
                '<br>Assessment departures: '+str(rows[i]['features']['outside_baseline_observations'])+'/'+str(rows[i]['features']['supported_assessment_observations'])+
                '<br>Baseline acquisitions: '+str(len(rows[i]['features']['baseline_dates'])) for i in ids],hoverinfo='text'),row=1,col=1)
    r=rows[0]
    for c in range(2):fig.add_trace(go.Scatter(x=r['full_record_dates'],y=r[['hh_db','hv_db'][c]],mode='lines+markers',line={'color':['#0072b2','#b66728'][c]},showlegend=False),row=c+1,col=2)
    shapes=[]
    for c in range(2):
        base=r['features']['baseline_db'][c];band=r['features']['diagnostic_half_width_db'][c]
        shapes.append({'type':'rect','xref':f'x{c+2} domain','yref':f'y{c+2}','x0':0,'x1':1,
            'y0':base-(band or 0),'y1':base+(band or 0),'fillcolor':'#9aaab5','opacity':.18,'line':{'width':0},'layer':'below','visible':band is not None})
    fig.update_layout(template='plotly_white',height=790,title={'text':'Observed surface features: departures from local baseline<br><sup>Color: any assessment-date departure. Click for measured curves and bounds. Within bounds is not verified normal.</sup>','y':.98,'yanchor':'top','font':{'size':16}},
        margin={'t':135,'b':135,'l':65,'r':20},shapes=shapes,legend={'orientation':'h','x':0,'y':-.14,'font':{'size':9}})
    fig.update_xaxes(title_text='First HH change from baseline median (dB)',row=1,col=1)
    fig.update_yaxes(title_text='First HV change from baseline median (dB)',row=1,col=1)
    for c in range(2):fig.update_yaxes(title_text='Gamma0 (dB)',row=c+1,col=2)
    payload=[{'id':r['point_id'],'dates':r['full_record_dates'],'hh':r['hh_db'],'hv':r['hv_db'],
        'base':r['features']['baseline_db'],'band':r['features']['diagnostic_half_width_db']} for r in rows]
    script='const observations='+json.dumps(payload,allow_nan=False)+';\n'
    script+="const graph=document.getElementById('{plot_id}');graph.on('plotly_click',function(e){const p=e.points[0];if(p.curveNumber>2)return;const h=observations[p.customdata];Plotly.restyle(graph,{x:[h.dates,h.dates],y:[h.hh,h.hv]},[3,4]);const change={'annotations[1].text':'HH: '+h.id,'annotations[2].text':'HV: '+h.id};for(let c=0;c<2;c++){change['shapes['+c+'].visible']=h.band[c]!==null;change['shapes['+c+'].y0']=h.base[c]-(h.band[c]||0);change['shapes['+c+'].y1']=h.base[c]+(h.band[c]||0);}Plotly.relayout(graph,change);});"
    fig.write_html(out/'feature_explorer.html',include_plotlyjs=True,full_html=True,post_script=script,div_id='surface-features',config={'displaylogo':False})


def render(output):
    out=Path(output);rows=json.loads((out/'points.json').read_text());cases=json.loads((out/'cases.json').read_text())
    plt.rcdefaults();plt.rcParams.update({'font.size':9,'axes.facecolor':'white','figure.facecolor':'white'})
    examples=select_examples(rows);(out/'examples.json').write_text(json.dumps(examples,indent=2),encoding='utf8')
    examples_plot(out,examples);examples_plot(out,examples[:2],'baseline_comparison');reference_plot(out,cases)
    for name in ['qadirabad','wheatbelt']:spatial_plot(out,name)
    analog_plot(out,rows,json.loads((out/'analogs.json').read_text()),json.loads((out/'analog_metadata.json').read_text()),dict(np.load(out/'analog_features.npz')))
    interactive(out,rows)
