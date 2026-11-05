"""Static scientific figures and observed-data GIFs; no frontend code."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
from io import BytesIO

BG='#0b1221';FG='#e9eef7';MUTED='#a7b6ce'

def style():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'text.color':FG,'axes.labelcolor':MUTED,'xtick.color':MUTED,'ytick.color':MUTED,'axes.edgecolor':'#66748c','axes.facecolor':BG,'figure.facecolor':BG,'savefig.facecolor':BG})

def extent(cube):
    x,y=cube['x'],cube['y'];dx=x[1]-x[0];dy=y[0]-y[1]
    return [(x[0]-dx/2)/1000,(x[-1]+dx/2)/1000,(y[-1]-dy/2)/1000,(y[0]+dy/2)/1000]

def rgb_power(db,pols):
    names=list(map(str,pols));co=names.index('HH') if 'HH' in names else names.index('VV') if 'VV' in names else 0
    cross=names.index('HV') if 'HV' in names else names.index('VH') if 'VH' in names else None
    a=db[co];scale=lambda v,lo,hi:np.clip((v-lo)/(hi-lo),0,1)
    if cross is None:rgb=np.repeat(scale(a,-25,0)[...,None],3,-1)
    else:rgb=np.stack([scale(a,-25,0),scale(db[cross],-32,-5),scale(db[cross]-a,-18,-2)],-1)
    rgb[~np.isfinite(db).all(0)]=[.03,.04,.07]
    return rgb

def save(fig,path):
    fig.savefig(path,dpi=150,bbox_inches='tight',pad_inches=.25);plt.close(fig)

def map_panel(fig,ax,array,cube,title,cmap='magma',units='',vmin=None,vmax=None):
    im=ax.imshow(array,extent=extent(cube),cmap=cmap,vmin=vmin,vmax=vmax,interpolation='none')
    ax.set_title(title,loc='left',fontsize=11,pad=10);ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)')
    if array.ndim==2:fig.colorbar(im,ax=ax,fraction=.045,pad=.03,label=units)

def physical_figure(cube,db,stat,structure,path,title):
    style();fig,axs=plt.subplots(2,3,figsize=(16,10));fig.subplots_adjust(top=.86,bottom=.16,hspace=.36,wspace=.55)
    fig.suptitle(title.upper()+'  /  MEASURED SURFACE DYNAMICS',x=.06,ha='left',fontsize=20,fontweight='bold',y=.97)
    dates=cube['dates'];first,middle,last=0,len(dates)//2,len(dates)-1
    temporal=np.stack([np.clip((db[k,0]+25)/25,0,1) for k in [first,middle,last]],-1)
    temporal[~np.isfinite(temporal).all(-1)]=[.03,.04,.07]
    map_panel(fig,axs[0,0],temporal,cube,'Three measured dates → RGB')
    map_panel(fig,axs[0,1],stat['median_db'][0],cube,f'Median {cube["polarizations"][0]} return','inferno','gamma0 (dB)',-25,0)
    map_panel(fig,axs[0,2],stat['mad_db'][0],cube,'Temporal variability (robust MAD)','magma','dB',0,np.nanpercentile(stat['mad_db'][0],98))
    limit=max(np.nanpercentile(abs(stat['slope_db_per_day'][0]),98),.001)
    map_panel(fig,axs[1,0],stat['slope_db_per_day'][0],cube,'Observed linear trend','RdBu_r','dB/day',-limit,limit)
    map_panel(fig,axs[1,1],stat['maximum_step_db'][0],cube,'Largest observed acquisition step','magma','absolute dB',0,np.nanpercentile(stat['maximum_step_db'][0],98))
    map_panel(fig,axs[1,2],structure['spatial_anisotropy'],cube,'Directional spatial texture','viridis','anisotropy (0–1)',0,1)
    for ax in axs[:,1:].ravel():ax.set_ylabel('')
    fig.text(.06,.07,f'RGB: {cube["polarizations"][0]} on {dates[first]} / {dates[middle]} / {dates[last]}; fixed limits [−25, 0] dB. EPSG:{int(cube["epsg"])}.',fontsize=10)
    fig.text(.06,.035,'Measured scattering response, including unresolved noise/geometry effects. No crop, burn, inundation or landslide class is inferred.',color=MUTED,fontsize=10)
    save(fig,path)

def spectral_figure(cube,spectral,local,path,title):
    style();fig,axs=plt.subplots(1,3,figsize=(16,5.8));fig.subplots_adjust(top=.79,bottom=.21,wspace=.6)
    fig.suptitle(title.upper()+'  /  SPECTRAL QUESTIONS, NOT EVENT LABELS',x=.06,ha='left',fontsize=18,fontweight='bold',y=.96)
    map_panel(fig,axs[0],spectral['candidate_period_days'][0],cube,'Strongest candidate period','turbo','days')
    map_panel(fig,axs[1],spectral['peak_normalized_power'][0],cube,'Normalized fitted spectral power','magma','power (0–1)',0,1)
    axs[1].set_ylabel('')
    for i,p in enumerate(cube['polarizations']):axs[2].plot(spectral['periods_days'],spectral['roi_spectra'][i],label=str(p))
    axs[2].plot(spectral['periods_days'],spectral['sampling_window'],'--',color=MUTED,label='Sampling window')
    axs[2].set_xlabel('Candidate period (days)');axs[2].set_ylabel('Normalized power');axs[2].set_title('Regional detrended periodograms',loc='left');axs[2].legend();axs[2].grid(alpha=.15)
    p=spectral['channels'][0]['max_statistic_permutation_p'];fig.text(.06,.11,f'Regional max-statistic permutation p = {p:.3f}; null assumes exchangeable residuals. Candidate periods are not confirmed cycles.',fontsize=10)
    fig.text(.06,.055,'Actual irregular timestamps; no interpolated acquisitions. Sampling aliases, limited duration and spatially correlated pixels restrict interpretation.',color=MUTED,fontsize=10)
    save(fig,path)
    if local.get('status')=='available':
        fig,ax=plt.subplots(figsize=(10,5));t=np.arange(len(cube['dates']));im=ax.pcolormesh(t,local['periods_days'],local['power'],cmap='magma',vmin=0,vmax=1,shading='nearest')
        ax.set_yscale('log');ax.set_ylabel('Local candidate period (days)');ax.set_xlabel('Observed acquisition index (not equal elapsed time)');ax.set_title(title+' / local weighted spectral behavior',loc='left');fig.colorbar(im,ax=ax,label='Local normalized fit power')
        ax.set_xticks(t[::2],list(cube['dates'][::2]),rotation=40,ha='right');fig.text(.01,.01,'Actual times used in fitting; unsupported edges/local windows are blank. This is weighted sinusoidal analysis, not a regular-grid CWT.',fontsize=8);fig.subplots_adjust(bottom=.27)
        save(fig,Path(path).with_name('time_frequency.png'))

def stochastic_figure(cube,region,stochastic,path,title):
    style();C=len(cube['polarizations']);fig,axs=plt.subplots(C,1,figsize=(12,3.5*C+1.5),squeeze=False);fig.subplots_adjust(top=.85,bottom=.20,hspace=.35)
    fig.suptitle(title.upper()+'  /  STOCHASTIC HISTORICAL STATES',x=.07,ha='left',fontsize=18,fontweight='bold',y=.97)
    dates=np.array(cube['dates'],dtype='datetime64[D]');draws=stochastic['region_draws_db']
    for c,ax in enumerate(axs[:,0]):
        quant=np.quantile(draws[:,c],[.025,.1,.25,.5,.75,.9,.975],axis=0)
        for a,b,alpha in [(0,6,.12),(1,5,.2),(2,4,.35)]:ax.fill_between(dates,quant[a],quant[b],color='#51cfce',alpha=alpha)
        for member in draws[:12,c]:ax.plot(dates,member,color='#51cfce',alpha=.15,lw=.7)
        if region is not None:
            ax.fill_between(dates,region['quantile_db'][0,:,c],region['quantile_db'][6,:,c],color='white',alpha=.15,label='95% spatial block interval')
            ax.plot(dates,region['mean_db'][:,c],'o-',color='white',label='Measured regional mean dB')
        ax.plot(dates,quant[3],color='#51cfce',label='Conditional GP median and 50/80/95% bands')
        ax.set_ylabel(str(cube['polarizations'][c])+' gamma0 (dB)');ax.grid(alpha=.15);ax.legend(fontsize=8,loc='best')
    fig.text(.07,.09,'Matérn-3/2 empirical-Bayes GP conditioned at observed dates only. Bands omit hyperparameter/model uncertainty and are not calibrated.',fontsize=10)
    fig.text(.07,.045,'White bands describe spatial heterogeneity; turquoise bands describe the assumed latent process. Neither is a measured sensor-error interval.',fontsize=10,color=MUTED)
    save(fig,path)

def learned_figure(cube,db,rgb,uncertainty,distance,path,title):
    style();fig,axs=plt.subplots(1,4,figsize=(20,5.5));fig.subplots_adjust(top=.78,bottom=.22,wspace=.6)
    fig.suptitle(title.upper()+'  /  A SHARED LEARNED REPRESENTATION',x=.05,ha='left',fontsize=18,fontweight='bold',y=.96)
    map_panel(fig,axs[0],rgb_power(db[-1],cube['polarizations']),cube,'Measured polarization RGB')
    map_panel(fig,axs[1],rgb,cube,'Frozen global latent → RGB')
    map_panel(fig,axs[2],uncertainty,cube,'MC-dropout latent sensitivity','magma','latent SD')
    map_panel(fig,axs[3],distance,cube,'Distance from training embedding','inferno','standardized distance')
    for ax in axs[1:]:ax.set_ylabel('');ax.tick_params(labelsize=8)
    fig.text(.05,.10,'Same Transformer and RGB basis across all locations. RGB mixes latent axes; it is not a land-cover legend or a causal explanation.',fontsize=10)
    fig.text(.05,.05,'Sensitivity and embedding distance are exploratory; they are not event probabilities, validated novelty detectors or calibrated uncertainty.',color=MUTED,fontsize=10)
    save(fig,path)

def observed_gif(cube,db,path,title):
    style();frames=[]
    for i,date in enumerate(cube['dates']):
        fig,ax=plt.subplots(figsize=(6,6.5));fig.subplots_adjust(top=.84,bottom=.18,left=.15,right=.96)
        ax.imshow(rgb_power(db[i],cube['polarizations']),extent=extent(cube));ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)')
        fig.suptitle(title+' / observed scattering',fontsize=14,fontweight='bold',y=.97);ax.set_title(str(date),loc='left',fontsize=13)
        fig.text(.1,.06,'Fixed power RGB; actual measured dates only.\nPlayback is uniform; acquisition intervals can differ.',fontsize=9,color=MUTED)
        buffer=BytesIO();fig.savefig(buffer,format='png',dpi=100);plt.close(fig);buffer.seek(0);frames.append(Image.open(buffer).convert('RGB').copy())
    frames[0].save(path,save_all=True,append_images=frames[1:],duration=700,loop=0)

def learned_gif(cube,db,rgb_time,path,title,learned_label='Contextual Transformer state RGB'):
    style();frames=[]
    for i,date in enumerate(cube['dates']):
        fig,axs=plt.subplots(1,2,figsize=(10,5.5));fig.subplots_adjust(top=.81,bottom=.20,wspace=.18)
        axs[0].imshow(rgb_power(db[i],cube['polarizations']));axs[1].imshow(np.nan_to_num(rgb_time[i],nan=.04))
        for ax,label in zip(axs,['Measured power RGB',learned_label]):ax.set_title(label,loc='left');ax.set_xticks([]);ax.set_yticks([])
        fig.suptitle(title+' / '+str(date),fontsize=15,fontweight='bold',y=.96)
        fig.text(.08,.08,'Fixed colors across time and geography. Learned states use all selected dates as context.\nDerived representation, not a measured extra band or a forecast; playback intervals are uniform.',fontsize=9,color=MUTED)
        stream=BytesIO();fig.savefig(stream,format='png',dpi=100);plt.close(fig);stream.seek(0);frames.append(Image.open(stream).convert('RGB').copy())
    frames[0].save(path,save_all=True,append_images=frames[1:],duration=700,loop=0)

def insar_figure(pair,result,path,title,subtitle=''):
    style();fig,axs=plt.subplots(1,4,figsize=(20,6));fig.subplots_adjust(top=.78,bottom=.24,wspace=.65)
    fig.suptitle(title+' / REAL INTERFEROMETRIC OBSERVATIONS',x=.05,ha='left',fontsize=18,fontweight='bold',y=.96)
    fig.text(.05,.87,subtitle,color=MUTED,fontsize=11)
    for i,(ax,key,prefix,cmap,unit,limits) in enumerate(zip(axs,['wrapped_phase_rad','coherence20','referenced_unwrapped_rad','phase_equivalent_cm'],['wrapped','wrapped','unwrapped','unwrapped'],['twilight','magma','RdBu_r','RdBu_r'],['radians','coherence','radians','phase-equivalent cm'],[(-np.pi,np.pi),(0,1),(None,None),(None,None)])):
        a=result[key];c={'x':pair[prefix+'_x'],'y':pair[prefix+'_y']}
        if limits[0] is None:
            lim=np.nanpercentile(abs(a),98);limits=(-lim,lim)
        im=ax.imshow(a,extent=extent(c),cmap=cmap,vmin=limits[0],vmax=limits[1]);fig.colorbar(im,ax=ax,fraction=.045,pad=.03,label=unit)
        ax.set_title(key.replace('_',' '),loc='left',fontsize=10);ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)' if i==0 else '');ax.tick_params(labelsize=8)
    m=result['metadata'];fig.text(.05,.13,f'One fringe: {m["one_fringe_phase_equivalent_cm"]:.2f} cm phase-equivalent range. Selected component support: {m["selected_fraction"]:.1%}. Ionosphere filled: {m["ionosphere_filled_fraction"]:.1%}.',fontsize=10)
    fig.text(.05,.075,'No atmospheric/ionospheric/tide phase corrections or verified stable reference. Phase-equivalent cm is NOT a deformation product.',fontsize=10)
    fig.text(.05,.035,'Dark pixels are excluded, not surface classes. Native 20/80 m grids remain separate; no earthquake magnitude or vertical motion is inferred.',color=MUTED,fontsize=10)
    save(fig,path)


def insar_stack_figure(ds,observations,quality,path,title):
    style();N=len(observations);fig,axs=plt.subplots(2,N,figsize=(5*N,9),squeeze=False)
    fig.subplots_adjust(top=.82,bottom=.20,hspace=.40,wspace=.45)
    fig.suptitle(title+' / OBSERVED INTERFEROMETRIC INTERVALS',x=.06,ha='left',fontsize=17,fontweight='bold',y=.97)
    fig.text(.06,.90,'Common native grid; separate pair intervals. Coherence is observability, and phase is uncorrected.',color=MUTED,fontsize=11)
    values=ds.phase_relative_to_shared_pixel_rad.values;finite=abs(values[np.isfinite(values)])
    limit=max(float(np.quantile(finite,.98)),.1) if len(finite) else 1.
    cube={'x':ds.x.values,'y':ds.y.values}
    for i,observation in enumerate(observations):
        label=observation['reference_date']+' → '+observation['secondary_date']
        for row,(array,cmap,unit,vmin,vmax) in enumerate([(ds.coherence.values[i],'magma','coherence',0,1),(values[i],'RdBu_r','uncorrected radians',-limit,limit)]):
            ax=axs[row,i];im=ax.imshow(array,extent=extent(cube),cmap=cmap,vmin=vmin,vmax=vmax)
            if row==0 or quality['shared_reference']:fig.colorbar(im,ax=ax,fraction=.045,pad=.03,label=unit)
            else:ax.text(.5,.5,'Phase comparison unavailable\nNo shared reference support\nacross all selected intervals',transform=ax.transAxes,ha='center',va='center',fontsize=10,color=MUTED)
            ax.set_title(label+'\n'+str(observation['interval_days'])+' day interval',loc='left',fontsize=9)
            ax.set_xlabel('Easting (km)');ax.set_ylabel('Northing (km)' if i==0 else '');ax.tick_params(labelsize=8)
            if row==1 and quality['shared_reference']:
                ref=quality['shared_reference'];ax.plot(ref['x']/1000,ref['y']/1000,'+',color='black',ms=9)
    ref_note='Shared reference is selected for coherence; stability is not established.' if quality['shared_reference'] else 'No common phase reference is available; coherence remains observable.'
    fig.text(.06,.12,f'Common selected-component phase support: {quality["common_phase_support_fraction"]:.1%}. '+ref_note,fontsize=10)
    fig.text(.06,.075,'Interval length, moisture and scattering differences can change coherence. No phase integration, SBAS inversion, correction or validated displacement.',fontsize=10)
    datum_note='The same physical pixel supplies the phase datum in every pair.' if quality['shared_reference'] else 'Phase arrays are unavailable, not zero motion.'
    fig.text(.06,.035,datum_note+' Disconnected components remain excluded separately for each interval.',color=MUTED,fontsize=10)
    save(fig,path)
