"""Readable observed-pattern maps, trajectories and evidence status."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm
from matplotlib.patches import Patch
from .behavior import LEGEND


def report_figure(cube,f,r,path):
    plt.rcParams.update({'font.family':'DejaVu Sans','text.color':'#222222',
        'axes.labelcolor':'#444444','xtick.color':'#444444','ytick.color':'#444444',
        'axes.edgecolor':'#888888','axes.facecolor':'white','figure.facecolor':'white','savefig.facecolor':'white'})
    x,y=cube['x'],cube['y'];dx=x[1]-x[0];dy=y[0]-y[1]
    extent=[(x[0]-dx/2)/1000,(x[-1]+dx/2)/1000,(y[-1]-dy/2)/1000,(y[0]+dy/2)/1000]
    fig,axes=plt.subplots(2,3,figsize=(14,8.5),layout='constrained')
    fig.suptitle(r['request_id']+' | observed surface behavior',fontsize=15)
    for ax,c in zip(axes[0,:2],[f['co_index'],f['cross_index']]):
        if c is None:ax.axis('off');ax.text(.1,.6,'No measured corresponding cross-pol channel');continue
        im=ax.imshow(f['target_change_db'][c],extent=extent,origin='upper',interpolation='nearest',cmap='RdBu_r',vmin=-8,vmax=8)
        ax.set_title(str(cube['polarizations'][c])+' first assessment minus baseline',fontsize=10)
        fig.colorbar(im,ax=ax,shrink=.8,label='dB')
    colors=['#cccccc','#eeeeee','#0072b2','#d55e00','#cc79a7','#e69f00','#56b4e9','#009e73','#ffffff']
    pattern=np.where(f['pattern_code']==255,8,f['pattern_code'])
    axes[0,2].imshow(pattern,extent=extent,origin='upper',interpolation='nearest',cmap=ListedColormap(colors),norm=BoundaryNorm(np.arange(10)-.5,9))
    axes[0,2].set_title('Observed trajectory patterns',fontsize=10)
    present=np.unique(f['pattern_code'])
    axes[0,2].legend(handles=[Patch(color=colors[8 if c==255 else int(c)],label=LEGEND[int(c)].replace('_',' ')) for c in present],fontsize=6.5,loc='upper left',framealpha=.9)
    ax=axes[1,0]
    im=ax.imshow(f['observed_change_frequency'],extent=extent,origin='upper',interpolation='nearest',vmin=0,vmax=1,cmap='viridis')
    ax.set_title('Co-pol threshold crossing frequency\nSupported assessment acquisitions only',fontsize=10)
    fig.colorbar(im,ax=ax,shrink=.8,label='Observed fraction; not probability')
    ax=axes[1,1]
    for pol in [f['co_polarization'],f['cross_polarization']]:
        if pol is None:continue
        rows=[row for row in f['series'] if row['polarization']==pol]
        dates=np.array([row['date'] for row in rows],dtype='datetime64[D]')
        ax.plot(dates,[row['median_change_db'] for row in rows],'o-',label=pol,markersize=3)
    ax.axhline(0,color='gray',lw=.7)
    for value in [-3,3]:ax.axhline(value,color='gray',lw=.7,ls=':')
    ax.set_title('Median trajectory on fixed common cells',fontsize=10);ax.set_ylabel('Change relative to baseline (dB)')
    ax.tick_params(axis='x',rotation=35,labelsize=7);ax.legend(fontsize=8);ax.grid(alpha=.15)
    axes[1,2].axis('off');q=r['quality']
    text=(f"Processing: {r['status']}\nFixed support: {q['fixed_common_support_cells']:,} cells\n"
          f"Selection retained: {q['fixed_fraction_of_selection']:.1%}\n"
          f"Baseline / assessment: {len(f['before'])} / {len(f['after'])} acquisitions\n\n"
          'Regional pattern:\n'+f['regional_pattern'].replace('_',' ')+'\n\n'
          'Independent evidence:\n'+r['evidence']['status'].replace('_',' ')+'\n\n'
          'Patterns describe radar observations.\nNo cause or phenomenon probability\nis established by these colors.\nGaps do not establish continuous duration.')
    axes[1,2].text(0,.98,text,va='top',fontsize=9,linespacing=1.45)
    for ax in [*axes[0],axes[1,0]]:ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)');ax.tick_params(labelsize=7)
    fig.savefig(path,dpi=135);plt.close(fig)


def disturbance_figure(cube,support,baseline,evidence,reference_path,path):
    from .analysis import measured_db
    with np.load(reference_path,allow_pickle=False) as h:
        coverage=h['valid_coverage'].copy();fraction=h['confirmed_loss_fraction'].copy()
    dates=cube['dates'].astype(str);pre=(dates>=baseline['start_date'])&(dates<=baseline['end_date'])
    idx=int(np.flatnonzero(dates==evidence['comparison_sar_date'])[0])
    pols=list(map(str,cube['polarizations']));co=pols.index('HH') if 'HH' in pols else pols.index('VV')
    db=measured_db(cube);delta=np.full(support.shape,np.nan)
    delta[support]=db[idx,co][support]-np.median(db[pre,co][:,support],axis=0)
    x,y=cube['x'],cube['y'];dx=x[1]-x[0];dy=y[0]-y[1]
    extent=[(x[0]-dx/2)/1000,(x[-1]+dx/2)/1000,(y[-1]-dy/2)/1000,(y[0]+dy/2)/1000]
    fig,axes=plt.subplots(1,3,figsize=(14,5.5));fig.subplots_adjust(top=.79,bottom=.26,wspace=.42)
    for ax,data,title,cmap,vmin,vmax,unit in zip(axes,[delta,coverage,fraction],
            ['Observed '+pols[co]+' change at '+evidence['comparison_sar_date'],
             'Recent usable optical coverage','Confirmed in-window vegetation loss'],
            ['RdBu_r','viridis','magma'],[-8,0,0],[8,1,1],['dB','Coverage fraction','Share of valid optical source cells']):
        im=ax.imshow(data,extent=extent,origin='upper',interpolation='nearest',cmap=cmap,vmin=vmin,vmax=vmax)
        fig.colorbar(im,ax=ax,shrink=.75,label=unit);ax.set_title(title,fontsize=9)
        ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)');ax.tick_params(labelsize=7)
    fig.suptitle('Oregon development scene | independent optical disturbance evidence',fontsize=14,y=.96)
    contrast=evidence['matched_contrast'];value=contrast.get('mean_contrast_db');interval=contrast.get('spatial_resampling_95_interval_db')
    note=f"{evidence['confirmed_in_window_loss_cells']} strict confirmed-loss cells; optical/SAR calendar-day offset {evidence['optical_offset_days']}. "
    if value is not None:note+=f'Matched mean co-pol contrast {value:+.2f} dB.'
    fig.text(.06,.14,note,fontsize=9)
    if interval:fig.text(.06,.095,f'Conditional spatial-resampling 95% interval: [{interval[0]:+.2f}, {interval[1]:+.2f}] dB; sensor/reference errors and confounding omitted.',fontsize=9)
    fig.text(.06,.045,'Vegetation disturbance is not a fire-cause or burned-area label. No independently surveyed fire perimeter is supplied.',fontsize=9)
    fig.savefig(path,dpi=135);plt.close(fig)
