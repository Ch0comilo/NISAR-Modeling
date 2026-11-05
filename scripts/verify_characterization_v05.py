"""Independent calculations, denominator checks and freeze/artifact validation."""
from pathlib import Path
import hashlib,json
import numpy as np
import xarray as xr
import rasterio
from surface_dynamics.engine import validate_output
from surface_dynamics.io import load_cube

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'products/characterization_v05'


def verify_artifacts(out,result):
    ids=[]
    for a in result['artifacts']:
        p=(out/a['href']).resolve();assert p.is_relative_to(out.resolve())
        assert hashlib.sha256(p.read_bytes()).hexdigest()==a['sha256']
        ids.append(a['id'])
    assert len(ids)==len(set(ids))
    for insight in result.get('insights',[]):assert set(insight['evidence_artifacts'])<=set(ids)
    return len(ids)


def run():
    for name in ['protocol_freeze.json','probe_freeze.json']:
        freeze=json.loads((BASE/name).read_text())
        for path,digest in freeze['files'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,(name,path)
    inventory=json.loads((BASE/'report_inventory.json').read_text());total=0;checks=[]
    for item in inventory:
        if item['status']=='no_admitted_cube':continue
        name=item['case_id'];out=BASE/'runs'/name
        r=json.loads((out/'result.json').read_text());validate_output(r,'characterization')
        total+=verify_artifacts(out,r)
        assert r['phenomenon_probability'] is r['causal_attribution'] is r['displacement_cm'] is None
        request=json.loads((out/'request.json').read_text())
        # The fixture name is resolved relative to the verification root, making
        # the authoring-host request's absolute path irrelevant to reproduction.
        directory=ROOT/'products/flood_pilot/cases' if item['role']=='development' else BASE/'cases'
        cube=load_cube(directory/name/'cube.npz');dates=cube['dates'].astype(str)
        pre=(dates>=request['baseline']['start_date'])&(dates<=request['baseline']['end_date'])
        post=np.flatnonzero((dates>=request['assessment']['start_date'])&(dates<=request['assessment']['end_date']))
        chosen=np.flatnonzero(pre|np.isin(np.arange(len(dates)),post))
        db=np.where(cube['valid'],10*np.log10(np.where(cube['valid'],cube['power'],1)),np.nan)
        pols=list(map(str,cube['polarizations']));co=pols.index('HH') if 'HH' in pols else pols.index('VV')
        cross='HV' if pols[co]=='HH' else 'VH';channels=[co]+([pols.index(cross)] if cross in pols else [])
        common=np.isfinite(db[chosen][:,channels]).all(axis=(0,1))
        base=np.full(db.shape[1:],np.nan);base[:,common]=np.median(db[pre][:,:,common],axis=0)
        change=np.where(common[None,None],db-base[None],np.nan)
        expected=change[post[0]]
        assert int(common.sum())==r['quality']['fixed_common_support_cells']
        assert np.isclose(common.mean(),r['quality']['fixed_fraction_of_selection'])
        assert sum(r['behavior']['spatial_pattern_counts'].values())==int(common.sum())
        channel=r['what_changed']['channels'][0]['first_assessment_change_db']
        assert np.isclose(channel['mean'],np.mean(expected[co][common]))
        assert np.isclose(channel['median'],np.median(expected[co][common]))
        expected_frequency=np.where(common,(abs(change[post,co])>=3).sum(0)/len(post),np.nan)
        with xr.open_dataset(out/'behavior_arrays.nc') as ds:
            np.testing.assert_array_equal(ds.comparison_support.values,common)
            np.testing.assert_allclose(ds.target_change_db.values,expected,rtol=1e-6,atol=1e-6,equal_nan=True)
            np.testing.assert_allclose(ds.observed_change_frequency.values,expected_frequency,equal_nan=True)
            np.testing.assert_array_equal(ds.x.values,cube['x']);np.testing.assert_array_equal(ds.y.values,cube['y'])
            if pre.sum()<3 or len(post)<2:assert np.all(ds.pattern_code.values[common]==0)
        with rasterio.open(out/'target_change_db.tif') as ds:
            np.testing.assert_allclose(ds.read(),expected,rtol=1e-6,atol=1e-6,equal_nan=True)
            assert ds.crs.to_epsg()==int(cube['epsg'])
            np.testing.assert_allclose(ds.xy(0,0),(cube['x'][0],cube['y'][0]))
        for path in [out/'methods/result.json',out/'water_evidence/result.json']:
            if path.exists():
                component=json.loads(path.read_text());validate_output(component,'analysis' if path.parent.name=='methods' else 'flood')
                verify_artifacts(path.parent,component)
        checks.append({'case_id':name,'status':r['status'],'fixed_common_cells':int(common.sum()),'artifacts':len(r['artifacts'])})
    for item in json.loads((BASE/'flood_validation.json').read_text()):
        out=BASE/'flood_validation'/item['case_id'];r=json.loads((out/'result.json').read_text());validate_output(r,'flood')
        total+=verify_artifacts(out,r)
        with xr.open_dataset(out/'characterization_arrays.nc') as ds:
            dark=ds.candidate_darkening.values.astype(bool);support=ds.comparison_support.values.astype(bool)
            groups=ds.reference_group.values;scored=support&np.isin(groups,[0,1,2,3]);new=groups==1
            p=r['proxy_comparison'];counts=[int((scored&dark&new).sum()),int((scored&dark&~new).sum()),
                int((scored&~dark&new).sum()),int((scored&~dark&~new).sum())]
            assert counts==[p[k] for k in ['true_positive_proxy_pixels','false_positive_proxy_pixels','false_negative_proxy_pixels','true_negative_proxy_pixels']]
            n=int((support&(groups==0)).sum());flag=int((support&(groups==0)&dark).sum())
            assert p['reference_stable_nonwater_radar_flag_rate']==(flag/n if n else None)
            assert int((groups==1).sum())==r['reference_evidence']['group_counts']['new strict open-water class']
    candidate=BASE/'insar_candidate'
    for name,kind in [('readiness','insar-readiness'),('intervals','insar-stack')]:
        out=candidate/name;r=json.loads((out/'result.json').read_text());validate_output(r,kind);total+=verify_artifacts(out,r)
        assert r['los_displacement_cm'] is None
    with xr.open_dataset(candidate/'readiness/correction_readiness_arrays.nc') as ds:
        support=ds.selected_component_support.values.astype(bool)
        eligible=support&~ds.ionosphere_filled.values.astype(bool)&np.isfinite(ds.reported_ionosphere_screen_rad.values)
        eligible &= np.isfinite(ds.reported_ionosphere_uncertainty_rad.values)&(ds.reported_ionosphere_uncertainty_rad.values>0)
        assert int(eligible.all(0).sum())==2;assert int(support.all(0).sum())==17311
    probe=json.loads((BASE/'representation_probe.json').read_text())
    for item in probe['cases']:
        for method in item['methods'].values():
            matrix=np.array(method['confusion_matrix_true_rows_predicted_columns'])
            assert matrix.sum()==item['support']['joint_unambiguous_reference_cells']
            assert matrix.sum(1).tolist()==[item['support']['class_counts'][str(c)] for c in [0,1,2]]
            for i,recall in enumerate(method['recall_by_class']):assert recall==(matrix[i,i]/matrix[i].sum() if matrix[i].sum() else None)
    report={'assessment':'Share with noted caveats; do not promote a phenomenon classifier',
            'new_measured_cases':5,'new_gcov_acquisitions':42,'shared_characterization_jobs':len(checks),
            'fresh_flood_policy_jobs':4,'new_insar_jobs':2,'registered_artifacts_verified':total,
            'frozen_protocol_and_predictor_hashes_verified':True,'cases':checks,
            'checks':['independently recomputed measured dB changes and fixed populations','coordinates, COG centers and hashes',
                      'observed-frequency denominators and insufficient-support codes','proxy confusion counts and stable-nonwater flags',
                      'all component schemas, unique artifact IDs and explanation links','probe train/test support and absent-class denominators',
                      'new InSAR fill/uncertainty intersection and null displacement'],
            'required_caveats':['Clouds and partial coverage leave no strict new-water positives at the two fresh flood-context sites; recall is unavailable.',
                'The representation probe has only one new-water proxy cell across its four test scenes; no global or positive-class accuracy claim is supported.',
                'The embedding kNN probe produces severe nonwater false flags in the Australian control; the encoder is not promoted to phenomenon labeling.',
                'The Oregon scene has only nine strict confirmed optical vegetation-loss cells; no fire-perimeter validation or wildfire attribution is supplied.',
                'The Venezuela stack has 17,311 common coherent cells but only two common eligible correction-input cells, no independent stable reference or geodetic validation.',
                'Processing complete, radar pattern, independent-product agreement and validated physical cause remain distinct concepts.']}
    (ROOT/'docs/results/characterization_v05_verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':run()
