"""Physical descriptive statistics on measured, irregular acquisitions."""
import numpy as np
from scipy.ndimage import gaussian_filter


POLARIZATION_PAIRS = (("HH", "HV"), ("VV", "VH"))


def supported_reduction(values,function):
    support=np.isfinite(values).any(0)
    result=np.full(values.shape[1:],np.nan,dtype='float64')
    if support.any():result[support]=function(values[:,support],axis=0)
    return result

def measured_db(cube):
    p=cube['power'];ok=cube['valid'].astype(bool)&np.isfinite(p)&(p>0)
    return np.where(ok,10*np.log10(np.where(ok,p,1.)),np.nan)

def elapsed_days(dates):
    t=np.array(dates,dtype='datetime64[D]')
    return (t-t[0]).astype('timedelta64[D]').astype(float)

def physical_statistics(db,t):
    n=np.isfinite(db).sum(0);mean=supported_reduction(db,np.nanmean)
    median=supported_reduction(db,np.nanmedian);mad=1.4826*supported_reduction(abs(db-median),np.nanmedian)
    w=np.isfinite(db);tt=t[:,None,None,None]
    mt=np.sum(np.where(w,tt,0),0)/np.maximum(n,1)
    centered_t=tt-mt
    slope=np.nansum(centered_t*(db-mean),0)/np.maximum(np.sum(np.where(w,centered_t**2,0),0),1e-10)
    slope[n<3]=np.nan
    increments=np.diff(db,axis=0);rate=increments/np.diff(t)[:,None,None,None]
    peak=supported_reduction(abs(increments),np.nanmax)
    support=np.isfinite(db).all(axis=(0,1))
    tc=t-t.mean();flat=db.reshape(len(t),-1)
    complete=np.isfinite(flat).all(0)
    z=flat[:,complete]-flat[:,complete].mean(0)
    if z.shape[1]>=3:
        u,s,vh=np.linalg.svd(z,full_matrices=False)
        modes=np.full((min(3,len(s)),flat.shape[1]),np.nan)
        modes[:,complete]=vh[:len(modes)]
        pod={'spatial_modes':modes.reshape(-1,*db.shape[1:]),'temporal_coefficients':u[:,:len(modes)]*s[:len(modes)],'variance_fraction':s*s/max(np.sum(s*s),1e-12)}
    else:pod={}
    return {'valid_date_count':n,'median_db':median,'mad_db':mad,'slope_db_per_day':slope,'maximum_step_db':peak,'change_rate_db_per_day':rate,'complete_support':support,**pod}

def spatial_structure(db,pixel_m,pixel_m_y=None):
    pixel_m_y=pixel_m if pixel_m_y is None else pixel_m_y
    image=supported_reduction(db[:,0],np.nanmedian);support=np.isfinite(image)
    filled=np.where(support,image,np.median(image[support]) if support.any() else 0.)
    gx=gaussian_filter(filled,1,order=(0,1))/pixel_m;gy=-gaussian_filter(filled,1,order=(1,0))/pixel_m_y
    a=gaussian_filter(gx*gx,2);b=gaussian_filter(gx*gy,2);c=gaussian_filter(gy*gy,2)
    anisotropy=np.sqrt((a-c)**2+4*b*b)/np.maximum(a+c,1e-20)
    orientation=.5*np.arctan2(2*b,a-c)
    h,w=image.shape;window=np.outer(np.hanning(h),np.hanning(w))
    power=abs(np.fft.fftshift(np.fft.fft2((filled-filled.mean())*window)))**2
    power[h//2,w//2]=0
    fy=np.fft.fftshift(np.fft.fftfreq(h,d=pixel_m_y/1000));fx=np.fft.fftshift(np.fft.fftfreq(w,d=pixel_m/1000))
    return {'spatial_anisotropy':np.where(support,anisotropy,np.nan),'spatial_orientation_radians':np.where(support,orientation,np.nan),
            'spatial_fft_supported_cells':int(support.sum()), 'spatial_fft_total_cells':int(support.size),
            'spatial_fft_power':power if support.any() else np.full_like(power,np.nan),
            'spatial_fx_cycles_km':fx,'spatial_fy_cycles_km':fy,'orientation_convention':'gradient axis, modulo pi; radians counterclockwise from east toward north'}

def region_series(db,mask,draws=400,block=8,seed=17):
    """Spatial block-bootstrap intervals of mean dB; not temporal/sensor uncertainty."""
    rng=np.random.default_rng(seed);h,w=mask.shape;blocks=[];counts=[]
    for y in range(0,h,block):
        for x in range(0,w,block):
            z=db[:,:,y:y+block,x:x+block]
            submask=mask[y:y+block,x:x+block]&np.isfinite(z).all(axis=(0,1))
            if submask.size<block*block or submask.mean()<.8:continue
            blocks.append(z[:,:,submask].sum(-1));counts.append(int(submask.sum()))
    if len(blocks)<4:return None
    b=np.stack(blocks,axis=-1);counts=np.asarray(counts)
    indices=rng.integers(0,len(blocks),size=(draws,len(blocks)))
    sampled=b[:,:,indices].sum(-1)/counts[indices].sum(-1)[None,None]
    return {'mean_db':b.sum(-1)/counts.sum(),'quantile_db':np.quantile(sampled,[.025,.1,.25,.5,.75,.9,.975],axis=2),
            'block_count':len(blocks),'draw_count':draws,'supported_pixels':int(counts.sum()),
            'interval_type':'spatial_block_bootstrap_cell_weighted_mean_log_power','block_pixels':block,
            'support_scope':'Complete finite channel/date cells in full blocks with >=80% common mask support; weighted by cell count, not equal block means'}
