"""Export compact scientific figures, full result summaries and portable examples."""
from pathlib import Path
import json,shutil
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from surface_dynamics.engine import validate_request

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'products/characterization_v05'


def run():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    records=json.loads((BASE/'flood_validation.json').read_text())
    names=[r['case_id'] for r in records];x=np.arange(len(records))
    fig,axes=plt.subplots(1,2,figsize=(12,5));fig.subplots_adjust(top=.78,bottom=.27,wspace=.35)
    coverage=[r['reference_evidence']['fraction_of_radar_support']*100 for r in records]
    axes[0].bar(x,coverage,color='#0072b2');axes[0].set_ylim(0,112)
    for i,(v,r) in enumerate(zip(coverage,records)):
        count=r['reference_evidence']['group_counts']['new strict open-water class']
        axes[0].text(i,v+2,f'{v:.1f}%\nnew-water cells: {count}',ha='center',fontsize=8)
    rates=[r['proxy_comparison']['reference_stable_nonwater_radar_flag_rate']*100 for r in records]
    axes[1].bar(x,rates,color='#d55e00');axes[1].set_ylim(0,max(rates)*1.4)
    for i,v in enumerate(rates):axes[1].text(i,v+.06,f'{v:.3f}%',ha='center',fontsize=9)
    axes[0].set_ylabel('Joint optical reference / first-date radar support (%)')
    axes[1].set_ylabel('Radar flag rate on reference-stable nonwater (%)')
    for ax,title in zip(axes,['Independent evidence coverage','Fixed darkening rule: proxy negative behavior']):
        ax.set_title(title,loc='left',fontsize=11);ax.set_xticks(x,names,rotation=20,ha='right');ax.grid(axis='y',alpha=.15)
    fig.suptitle('Fresh flood-context scenes and candidate controls | fixed policy, 40 m grid',fontsize=14,y=.97)
    fig.text(.08,.10,'Rajshahi and Gimcheon have no strict new-open-water reference cells on eligible support; new-water recall is unavailable.',fontsize=9)
    fig.text(.08,.05,'Controls are candidates, not guaranteed event-free. Optical-product agreement is not surveyed ground truth; cloudy/mixed cells remain excluded.',fontsize=9)
    fig.savefig(ROOT/'docs/images/fresh_flood_validation_v05.png',dpi=130);plt.close(fig)
    probe=json.loads((BASE/'representation_probe.json').read_text())
    fig,axes=plt.subplots(1,2,figsize=(12,5));fig.subplots_adjust(top=.77,bottom=.27,wspace=.35)
    items=probe['cases'];names=[r['case_id'] for r in items];x=np.arange(len(items));width=.36
    for offset,key,color,label in [(-width/2,'physical','#0072b2','Physical descriptor kNN'),(width/2,'learned','#d55e00','Frozen embedding kNN')]:
        rate=[r['methods'][key]['reference_nonwater_predicted_new_water_fraction']*100 for r in items]
        axes[0].bar(x+offset,rate,width=width,color=color,label=label)
        for i,v in enumerate(rate):axes[0].text(i+offset,v+1,f'{v:.1f}',ha='center',fontsize=8)
    axes[0].set_ylim(0,105);axes[0].set_ylabel('Stable optical nonwater predicted as new water (%)');axes[0].legend(fontsize=8,frameon=False)
    classes=[0,1,2];bottom=np.zeros(len(items))
    for c,color,label in zip(classes,['#999999','#cc79a7','#56b4e9'],['Stable nonwater','New open water','Stable open water']):
        count=np.array([r['support']['class_counts'][str(c)] for r in items])
        axes[1].bar(x,count,bottom=bottom,color=color,label=label);bottom+=count
    axes[1].set_ylabel('Qualified proxy comparison cells (160 m grid)');axes[1].legend(fontsize=8,frameon=False)
    for ax,title in zip(axes,['Identical-support negative-class comparison','Reference support limits the experiment']):
        ax.set_title(title,loc='left',fontsize=11);ax.set_xticks(x,names,rotation=20,ha='right');ax.grid(axis='y',alpha=.15)
    fig.suptitle('Frozen representation probe | no neural retraining or test-driven threshold selection',fontsize=14,y=.97)
    fig.text(.08,.10,'Only ONE new-open-water proxy cell exists across these four tests. Positive-class performance and a global winner cannot be established.',fontsize=9)
    fig.text(.08,.05,'This is a development-trained kNN probe of features, not the encoder producing event labels. Its false flags prevent promotion to phenomenon classification.',fontsize=9)
    fig.savefig(ROOT/'docs/images/representation_probe_v05.png',dpi=130);plt.close(fig)
    for name in ['wheatbelt','rowe_creek']:
        src=BASE/'runs'/name/('characterization.png' if name=='wheatbelt' else 'optical_disturbance_evidence.png')
        shutil.copyfile(src,ROOT/'docs/images'/('surface_behavior_v05.png' if name=='wheatbelt' else 'optical_disturbance_v05.png'))
    for src,dst in [('report_inventory.json','characterization_v05_inventory.json'),
                    ('flood_validation.json','fresh_flood_validation_v05.json'),
                    ('representation_probe.json','representation_probe_v05.json'),
                    ('protocol_freeze.json','characterization_v05_protocol_freeze.json'),
                    ('probe_freeze.json','representation_probe_v05_freeze.json'),
                    ('insar_candidate/candidate_report.json','venezuela_insar_v05.json')]:
        shutil.copyfile(BASE/src,ROOT/'docs/results'/dst)
    for name in ['wheatbelt','gimcheon','rowe_creek']:
        request=json.loads((BASE/'runs'/name/'request.json').read_text())
        for section,key in [('source','cube_path'),('water_reference','path')]:
            if section in request:request[section][key]=Path(request[section][key]).relative_to(ROOT).as_posix()
        if request.get('disturbance_reference'):request['disturbance_reference']=Path(request['disturbance_reference']).relative_to(ROOT).as_posix()
        validate_request(request,'characterization')
        (ROOT/'contracts/examples'/f'{name}-characterization.json').write_text(json.dumps(request,indent=2))
    request=json.loads((ROOT/'contracts/examples/wheatbelt-characterization.json').read_text());request['request_id']='world-surface-behavior'
    request['source']={'mode':'catalog_point','longitude':117.5,'latitude':-31.5,'max_dates':12,'native_chip_pixels':512,'target_posting_m':40}
    request['water_reference']={'mode':'discover_optical','longitude':117.5,'latitude':-31.5}
    validate_request(request,'characterization');(ROOT/'contracts/examples/world-characterization.json').write_text(json.dumps(request,indent=2))
    shutil.copyfile(BASE/'runs/wheatbelt/result.json',ROOT/'contracts/examples/wheatbelt-characterization-response.json')
    print(json.dumps({'scientific_previews':4,'shared_report_examples':4,'full_response_examples':1}))


if __name__=='__main__':run()
