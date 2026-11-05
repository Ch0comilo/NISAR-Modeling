"""Scientific evidence figure on measured dates; no application UI."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm
from matplotlib.patches import Patch


def characterization_figure(cube,features,result,path):
    co=features['co_index'];x,y=cube['x'],cube['y'];dx=x[1]-x[0];dy=y[0]-y[1]
    extent=[(x[0]-dx/2)/1000,(x[-1]+dx/2)/1000,(y[-1]-dy/2)/1000,(y[0]+dy/2)/1000]
    fig,axes=plt.subplots(2,3,figsize=(13,8),layout='constrained')
    opts={'extent':extent,'origin':'upper','interpolation':'nearest'}
    maps=[(features['baseline_db'][co],'Baseline median '+features['co_polarization']+' (dB)','viridis',-30,0),
          (features['target_change_db'][co],'First assessment minus baseline (dB)','RdBu_r',-8,8)]
    for ax,(data,title,cmap,vmin,vmax) in zip(axes[0,:2],maps):
        im=ax.imshow(data,cmap=cmap,vmin=vmin,vmax=vmax,**opts);ax.set_title(title,fontsize=10);fig.colorbar(im,ax=ax,shrink=.75)
    groups=features['reference_group'];display=np.where(groups==255,5,groups)
    colors=['#cbbf9d','#0072b2','#56b4e9','#e69f00','#cc79a7','#eeeeee']
    axes[0,2].imshow(display,cmap=ListedColormap(colors),norm=BoundaryNorm(np.arange(7)-.5,6),**opts)
    axes[0,2].set_title('Independent optical-product transitions',fontsize=10)
    axes[0,2].legend(handles=[Patch(color=c,label=l) for c,l in zip(colors,['Stable nonwater','New open water','Stable open water','Open-water loss','Mixed/partial','Unsupported'])],fontsize=7,loc='upper left')
    im=axes[1,0].imshow(np.where(features['comparison_support'],features['candidate_darkening'].astype(float),np.nan),cmap=ListedColormap(['#eeeeee','#0072b2']),vmin=0,vmax=1,**opts)
    axes[1,0].set_title('Declared radar darkening signature\n(co-pol and cross-pol when available)',fontsize=10)
    axes[1,0].legend(handles=[Patch(color='#0072b2',label='Signal threshold met')],fontsize=8)
    ax=axes[1,1];dates=np.asarray(cube['dates'],dtype='datetime64[D]')
    for group,label,color in [('common_radar_support','Common radar support','#555555'),('reference_new_open_water','New optical open water','#0072b2'),('matched_reference_stable_nonwater','Matched reference-stable controls','#d55e00')]:
        rows=[r for r in features['series'] if r['group']==group and r['polarization']==features['co_polarization']]
        values=np.array([r['median_change_db'] if r['median_change_db'] is not None else np.nan for r in rows])
        if np.isfinite(values).any():ax.plot(dates,values,'o-',label=label,color=color,markersize=3)
    ax.axhline(0,color='gray',lw=.7);ax.axhline(-3,color='gray',lw=.7,ls=':');ax.axvline(dates[features['target_index']],color='gray',lw=.7,ls='--')
    ax.set_ylabel('Median co-pol change (dB)');ax.set_title('Observed acquisitions; gaps remain gaps',fontsize=10);ax.tick_params(axis='x',rotation=35,labelsize=7);ax.legend(fontsize=7)
    axes[1,2].axis('off');summary=result['signature_summary'];proxy=result['proxy_comparison'];ref=result['reference_evidence']
    text=(f"Status: {result['status']}\nRadar support: {summary['radar_supported_pixels']:,} cells\n"
          f"Optical joint support: {ref['fraction_of_radar_support']:.1%}\nStrict new-water cells: {ref['group_counts']['new strict open-water class']:,}\n"
          f"Dark-signature cells: {summary['darkening_pixels']:,}\nObjects (>=4 cells): {summary['signature_objects_total']}\n\n"
          'Optical classification agreement is not\nground-truth flood accuracy.\nCloudy/partial cells remain unknown.\n\n'
          'Thresholds describe radar signals;\nobjects are not flood polygons.\nNo flood depth, probability or forecast.')
    axes[1,2].text(0,.98,text,va='top',fontsize=9,linespacing=1.5)
    for ax in [*axes[0],axes[1,0]]:ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)');ax.tick_params(labelsize=7)
    fig.suptitle(result['request_id']+' | retrospective evidence pilot',fontsize=14)
    fig.savefig(path,dpi=125);plt.close(fig)
