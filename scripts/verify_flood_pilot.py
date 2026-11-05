"""Independent spot calculations and artifact/denominator QA on real outputs."""
from pathlib import Path
import hashlib,json
import numpy as np
import xarray as xr
import rasterio
from PIL import Image
from surface_dynamics.io import load_cube
from surface_dynamics.engine import validate_output

ROOT=Path(__file__).resolve().parents[1]


def artifacts(out,result):
    for artifact in result['artifacts']:
        path=(out/artifact['href']).resolve()
        assert path.is_relative_to(out.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
    ids={a['id'] for a in result['artifacts']}
    for insight in result.get('insights',[]):assert set(insight['evidence_artifacts'])<=ids
    return len(result['artifacts'])


def run():
    report={'assessment':'Share with noted caveats','pilot_role':'three inspected development cases, not a held-out global benchmark',
            'cases':[],'insar_windows':[],'verified_artifacts':0}
    for case in json.loads((ROOT/'configs/flood_pilot.json').read_text())['cases']:
        name=case['id'];out=ROOT/'products/flood_pilot/runs'/name
        r=json.loads((out/'result.json').read_text());validate_output(r,'flood');report['verified_artifacts']+=artifacts(out,r)
        assert r['flood_depth_m'] is r['flood_probability'] is r['causal_attribution'] is None
        cube=load_cube(ROOT/'products/flood_pilot/cases'/name/'cube.npz')
        dates=cube['dates'].astype(str);pre=(dates>=case['baseline_start'])&(dates<=case['baseline_end'])
        post=np.flatnonzero((dates>=case['assessment_start'])&(dates<=case['assessment_end']))
        db=np.where(cube['valid'],10*np.log10(np.where(cube['valid'],cube['power'],1)),np.nan)
        count=np.isfinite(db[pre]).sum(0);minimum=min(int(pre.sum()),max(3,int(np.ceil(pre.sum()*.75))))
        finite=count>0;base=np.full(count.shape,np.nan);base[finite]=np.nanmedian(db[pre][:,finite],axis=0)
        base[count<minimum]=np.nan;expected=db[post[0]]-base
        co=list(cube['polarizations']).index('HH');cross=list(cube['polarizations']).index('HV')
        supported=np.isfinite(expected[[co,cross]]).all(0)
        dark=supported&(expected[co]<=-3)&(expected[cross]<=-1.5)
        with xr.open_dataset(out/'characterization_arrays.nc') as ds:
            np.testing.assert_allclose(ds.gamma0_power,cube['power'],equal_nan=True)
            np.testing.assert_allclose(ds.target_change_db,expected,atol=1e-6,rtol=1e-6,equal_nan=True)
            np.testing.assert_array_equal(ds.candidate_darkening,dark)
            np.testing.assert_array_equal(ds.x,cube['x']);np.testing.assert_array_equal(ds.y,cube['y'])
            groups=ds.reference_group.values;eligible=supported&(groups!=255)
            assert int(eligible.sum())==r['reference_evidence']['comparison_supported_pixels']
            assert np.isclose(eligible.sum()/supported.sum(),r['reference_evidence']['fraction_of_radar_support'])
            assert sum(r['reference_evidence']['group_counts'].values())==int(supported.sum())
            # Recompute all four proxy counts without calling characterization helpers.
            scored=supported&np.isin(groups,[0,1,2,3]);new=groups==1;p=r['proxy_comparison']
            assert int((scored&dark&new).sum())==p['true_positive_proxy_pixels']
            assert int((scored&dark&~new).sum())==p['false_positive_proxy_pixels']
            assert int((scored&~dark&new).sum())==p['false_negative_proxy_pixels']
            assert int((scored&~dark&~new).sum())==p['true_negative_proxy_pixels']
            control=ds.matched_control_mask.values.astype(bool);matched=ds.matched_positive_mask.values.astype(bool)
            assert control.sum()==matched.sum();assert np.all(groups[control]==0) and np.all(groups[matched]==1)
            if matched.any():
                contrast=expected[co][matched].mean()-expected[co][control].mean()
                assert np.isclose(contrast,r['matched_controls']['co_pol_contrast']['mean_contrast_db'])
            area=float((cube['x'][1]-cube['x'][0])*(cube['y'][0]-cube['y'][1])/10000)
            assert np.isclose(dark.sum()*area,r['signature_summary']['darkening_signature_analysis_cell_area_ha'])
            for obj in r['objects']:
                mask=ds.object_labels.values==obj['object_id'];assert mask.sum()==obj['analysis_cells']
                assert obj['first_change_interval']['by']==r['observations']['fixed_assessment_date']
                assert obj['continuous_duration_inferred'] is False
        with rasterio.open(out/'target_change_db.tif') as cog:
            np.testing.assert_allclose(cog.read(),expected,atol=1e-6,rtol=1e-6,equal_nan=True)
            assert cog.crs.to_epsg()==int(cube['epsg'])
            np.testing.assert_allclose(cog.xy(0,0),(cube['x'][0],cube['y'][0]))
        with Image.open(out/'flood_characterization.png') as im:assert min(im.size)>800
        assert r['reference_evidence']['pre_date']<case['assessment_start']<=r['reference_evidence']['post_date']
        assert max(r['reference_evidence']['pre_offset_days'],r['reference_evidence']['post_offset_days'])<=7
        report['cases'].append({'case_id':name,'status':r['status'],'dates':len(dates),'reference_fraction':r['reference_evidence']['fraction_of_radar_support'],
                                'new_open_water_cells':int((groups==1).sum()),'fixed_target':r['observations']['fixed_assessment_date'],
                                'paired_contrast_db':r['matched_controls']['co_pol_contrast'].get('mean_contrast_db')})
    for case in ['kumamoto','kumamoto_wide']:
        out=ROOT/'products/insar_readiness'/case;r=json.loads((out/'result.json').read_text());validate_output(r,'insar-readiness')
        report['verified_artifacts']+=artifacts(out,r)
        assert r['status']=='not_displacement_ready' and r['los_displacement_cm'] is None and r['corrections_applied']==[]
        with xr.open_dataset(out/'correction_readiness_arrays.nc') as ds:
            phase=ds.selected_component_support.values.astype(bool)
            eligible=phase&~ds.ionosphere_filled.values.astype(bool)&np.isfinite(ds.reported_ionosphere_screen_rad.values)
            eligible &= np.isfinite(ds.reported_ionosphere_uncertainty_rad.values)&(ds.reported_ionosphere_uncertainty_rad.values>0)
            np.testing.assert_array_equal(ds.correction_input_eligible.values,eligible)
            assert int(eligible.all(0).sum())==r['quality']['common_correction_input_pixels']
            assert int(phase.all(0).sum())==r['quality']['common_phase_support_pixels']
        report['insar_windows'].append({'case_id':case,'common_phase_pixels':r['quality']['common_phase_support_pixels'],
                                         'common_correction_input_pixels':r['quality']['common_correction_input_pixels']})
    freeze=json.loads((ROOT/'models/global_explorer_v2/evaluation_freeze.json').read_text())
    assert hashlib.sha256((ROOT/'models/global_explorer_v2/masked_temporal_transformer.pt').read_bytes()).hexdigest()==freeze['checkpoint_sha256']
    report['frozen_transformer_unchanged']=True
    report['checks']=['measured power and independently recomputed dB changes','common support and explicit denominators','proxy confusion counts including stable water',
                      'matched-positive-only contrasts','nominal grid-mask areas and object timing','native coordinates/COGs and artifact SHA-256',
                      'optical before/after eligibility and date offsets','filled/finite/positive-uncertainty correction gates','null depth, probability, attribution and displacement']
    report['required_caveats']=['Independent optical-product agreement is not surveyed ground-truth accuracy.',
        'Cloud gaps, 1-7 day offsets and three inspected scenes prevent global validation.',
        'Chokwe uses beta products and only one assessment date; Richmond observation misses the reported event interval.',
        'Baseline medians and optical snapshot comparisons have different temporal support; last-baseline sensitivity is provided without threshold optimization.',
        'Spatial bootstrap intervals omit sensor/reference error and causal identification; controls are observational.',
        'No validated pixel-level vegetation, urban flood, under-canopy water or inundation-depth labels exist.',
        'InSAR corrections and independent stable reference remain unresolved.']
    target=ROOT/'docs/results/flood_pilot_verification.json';target.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':run()
