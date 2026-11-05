"""Audit measured correction support without applying unvalidated corrections."""
from pathlib import Path
import hashlib,json
import numpy as np
from .insar_stack import analyze_stack
from .network import acquisition_dates


def audit_corrections(pairs,metadata,coherence_threshold=.35):
    ds,observations,quality=analyze_stack(pairs,metadata,coherence_threshold)
    ordered=sorted(range(len(pairs)),key=lambda i:acquisition_dates(metadata[i]['title']))
    screens=[];uncertainties=[];eligible=[];reports=[]
    for j,i in enumerate(ordered):
        pair=pairs[i];cols=np.searchsorted(pair['unwrapped_x'],ds.x.values);rows=np.searchsorted(-pair['unwrapped_y'],-ds.y.values);index=np.ix_(rows,cols)
        screen=pair.get('ionospherePhaseScreen',np.full(pair['unwrappedPhase'].shape,np.nan))[index]
        sigma=pair.get('ionospherePhaseScreenUncertainty',np.full(pair['unwrappedPhase'].shape,np.nan))[index]
        support=ds.selected_component_support.values[j].astype(bool);filled=ds.ionosphere_filled.values[j].astype(bool)
        # Positive finite reported uncertainty is an eligibility condition, not
        # proof of calibrated uncertainty or adequate correction accuracy.
        ok=support&~filled&np.isfinite(screen)&np.isfinite(sigma)&(sigma>0)
        screens.append(screen);uncertainties.append(sigma);eligible.append(ok)
        n=int(support.sum());count=int(ok.sum())
        reports.append({'source_id':observations[j]['source_id'],'coherent_selected_component_pixels':n,
                        'ionosphere_filled_coherent_pixels':int((support&filled).sum()),
                        'finite_screen_coherent_pixels':int((support&np.isfinite(screen)).sum()),
                        'missing_or_nonpositive_uncertainty_coherent_pixels':int((support&(~np.isfinite(sigma)|(sigma<=0))).sum()),
                        'unfilled_screen_and_positive_uncertainty_pixels':count,
                        'eligible_fraction_of_grid':float(ok.mean()),'eligible_fraction_of_coherent_support':count/n if n else None,
                        'eligible_screen_05_50_95_rad':np.quantile(screen[ok],[.05,.5,.95]).tolist() if count else None,
                        'eligible_reported_uncertainty_05_50_95_rad':np.quantile(sigma[ok],[.05,.5,.95]).tolist() if count else None})
    eligible=np.stack(eligible);common=np.all(eligible,axis=0);candidate=None
    if common.sum()>=8:
        score=np.where(common,np.min(ds.coherence.values,axis=0),-np.inf);row,col=np.unravel_index(np.argmax(score),score.shape)
        candidate={'pixel':[int(row),int(col)],'x':float(ds.x[col]),'y':float(ds.y[row]),'epsg':int(ds.attrs['epsg_code']),
                   'selection':'maximum minimum coherence among common eligible correction-support cells',
                   'independently_stable':False,'correction_validated':False}
    ds['reported_ionosphere_screen_rad']=(('pair','y','x'),np.asarray(screens,dtype='float32'))
    ds['reported_ionosphere_uncertainty_rad']=(('pair','y','x'),np.asarray(uncertainties,dtype='float32'))
    ds['correction_input_eligible']=(('pair','y','x'),eligible.astype('uint8'))
    ds['common_correction_input_eligible']=(('y','x'),common.astype('uint8'))
    for key in ['reported_ionosphere_screen_rad','reported_ionosphere_uncertainty_rad','correction_input_eligible','common_correction_input_eligible']:ds[key].attrs['grid_mapping']='spatial_ref'
    ds.reported_ionosphere_screen_rad.attrs.update(units='radian',interpretation='Supplied correction input, not applied to phase by this worker')
    ds.reported_ionosphere_uncertainty_rad.attrs.update(units='radian',interpretation='Product-reported uncertainty; empirical calibration not established')
    quality.update(common_correction_input_pixels=int(common.sum()),common_correction_input_fraction_of_grid=float(common.mean()),
                   common_correction_input_fraction_of_common_phase_support=float(common.sum()/quality['common_phase_support_pixels']) if quality['common_phase_support_pixels'] else None,
                   candidate_reference=candidate,eligibility_is_correction_validation=False)
    return ds,reports,quality


def process_readiness(request,output):
    from .engine import validate_request,validate_output,write_cog
    validate_request(request,'insar-readiness');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    pairs=[];metadata=[]
    for name in request['source']['pair_paths']:
        path=Path(name);meta=json.loads(path.with_suffix('.json').read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Readiness input checksum mismatch')
        with np.load(path,allow_pickle=False) as h:pairs.append({k:h[k] for k in h.files})
        metadata.append(meta)
    ds,reports,quality=audit_corrections(pairs,metadata,request.get('coherence_threshold',.35))
    sensitivity=[]
    for threshold in [.2,.35,.5,.7]:
        _,_,q=audit_corrections(pairs,metadata,threshold)
        sensitivity.append({'coherence_threshold':threshold,'common_phase_support_pixels':q['common_phase_support_pixels'],
                            'common_correction_input_pixels':q['common_correction_input_pixels']})
    result={'contract_version':'1.0','request_id':request['request_id'],'analysis':'insar_correction_readiness',
            'status':'not_displacement_ready','corrections_applied':[],'los_displacement_cm':None,
            'pair_quality':reports,'quality':quality,'threshold_sensitivity':sensitivity,'artifacts':[],
            'required_gates':[
                {'id':'common_component_and_correction_support','status':'available_but_unvalidated' if quality['candidate_reference'] else 'blocked','next_input':'At least eight common coherent selected-component cells with unfilled finite ionosphere screen and positive reported uncertainty; this threshold is a policy, not validation.'},
                {'id':'independent_stable_reference','status':'blocked','next_input':'External GNSS/geologic evidence or other independently checked stable reference polygon on common support.'},
                {'id':'ionosphere_correction_convention','status':'blocked','next_input':'Verify product-version correction sign, units and reference convention against processor documentation and independent geodesy before applying the screen.'},
                {'id':'atmosphere_and_tides','status':'blocked','next_input':'Versioned tropospheric and tidal correction fields with acquisition timestamps, datum and uncertainty provenance.'},
                {'id':'unwrapping_and_network_validation','status':'blocked','next_input':'Redundant compatible pair intervals for phase closure, component checks and independent displacement validation; a chain has no closure test.'},
                {'id':'geodetic_sign_and_geometry','status':'blocked','next_input':'Validate LOS sign, wavelength and look geometry against an independently measured displacement before inversion.'}],
            'limitations':['Ionosphere input eligibility is not correction accuracy or stable-ground validation.',
                           'Filled flags, finite screens and positive reported uncertainty are different quality conditions; absence of a fill flag does not establish trustworthy correction.',
                           'All fractions name their denominator. No correction is applied and no phase is integrated or converted into a deformation estimate.',
                           'The maximum-minimum-coherence candidate is a proposed data-support location, not an independently stable reference.',
                           'No earthquake detection, earthquake attribution, vertical motion or forecasting is inferred from this audit.']}
    paths=[('correction_readiness_arrays.nc','measured_correction_inputs_and_eligibility','application/x-netcdf'),
           ('common_correction_input_eligible.tif','common_input_support_not_validated_stability','image/tiff'),
           ('reported_ionosphere_uncertainty_rad.tif','product_reported_uncertainty_not_calibrated_error','image/tiff')]
    ds.attrs.update(description='InSAR correction-input readiness only; no correction applied or deformation estimated')
    ds.to_netcdf(out/paths[0][0],engine='h5netcdf',encoding={k:{'zlib':True,'complevel':3} for k in ds.data_vars if ds[k].ndim>0})
    write_cog(out/paths[1][0],ds.common_correction_input_eligible.values,ds.x.values,ds.y.values,ds.attrs['epsg_code'],'mask',angular=True)
    write_cog(out/paths[2][0],ds.reported_ionosphere_uncertainty_rad.values,ds.x.values,ds.y.values,ds.attrs['epsg_code'],'radian')
    for name,role,media in paths:result['artifacts'].append({'id':Path(name).stem,'href':name,'role':role,'media_type':media,'sha256':hashlib.sha256((out/name).read_bytes()).hexdigest()})
    validate_output(result,'insar-readiness');(out/'request.json').write_text(json.dumps(request,indent=2));(out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    return result
