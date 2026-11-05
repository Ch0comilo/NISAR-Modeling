"""Meaningful offline gates for science semantics, masks, contracts and learning."""
import unittest,json,tempfile
from pathlib import Path
import numpy as np
import torch
from scipy.signal import lombscargle
from surface_dynamics.spectral import periodogram
from surface_dynamics.stochastic import smooth_observations
from surface_dynamics.transformer import MaskedTemporalTransformer
from surface_dynamics.insar import select_event_pair,valid_gunw_mask,analyze_pair
from surface_dynamics.io import validate_cube
from surface_dynamics.engine import validate_request,write_cog,analyze
from jsonschema.exceptions import ValidationError
import rasterio

ROOT=Path(__file__).resolve().parents[1]

class ScienceTests(unittest.TestCase):
    def test_irregular_spectrum_matches_scipy(self):
        t=np.array([0,11,25,39,50,71,84,97,114,137,152,177.])
        y=7+2*np.sin(2*np.pi*t/44.5)+.1*np.cos(t)
        f=np.linspace(1/100,1/20,101)
        actual=periodogram(y,t,f)
        reference=lombscargle(t,y,2*np.pi*f,normalize=True,floating_mean=True)
        np.testing.assert_allclose(actual,reference,rtol=1e-9,atol=1e-9)
        self.assertLess(abs(1/f[np.argmax(actual)]-44.5),1.5)

    def test_gunw_has_four_times_not_two_acquisitions_at_same_date(self):
        title='NISAR_L2_PR_GUNW_026_024_A_018_028_4000_SH_20260721T202900_20260721T202934_20260814T202858_20260814T202933_P05023_N_F_J_001'
        entry={'title':title};self.assertIs(select_event_pair([entry],'2026-07-28'),entry)
        self.assertIsNone(select_event_pair([entry],'2026-09-01'))

    def test_mask_ionosphere_flag_is_separate_from_data_anomalies(self):
        mask=np.array([11,0,255,11|(1<<8),11|(1<<24),111],dtype='uint32')
        np.testing.assert_array_equal(valid_gunw_mask(mask),[True,False,False,False,True,True])

    def test_masked_transformer_cannot_read_hidden_measurement(self):
        torch.manual_seed(1);m=MaskedTemporalTransformer(width=16,layers=1,dropout=0).eval()
        x=torch.randn(2,8,4);present=torch.ones_like(x,dtype=torch.bool);times=torch.arange(8).float()[None].repeat(2,1)*12
        pad=torch.zeros(2,8,dtype=torch.bool);mask=torch.zeros_like(pad);mask[:,3]=True
        out,_=m(x,present,times,pad,mask);changed=x.clone();changed[:,3]=10000
        other,_=m(changed,present,times,pad,mask)
        torch.testing.assert_close(out,other,rtol=0,atol=0)

    def test_gp_draws_are_historical_and_covariance_is_nonnegative(self):
        rng=np.random.default_rng(2);t=np.array([0,12,24,48,60,72.]);db=rng.normal(size=(6,1,8,8)).astype('float32')
        out=smooth_observations(db,t,draws=64)
        self.assertEqual(out['region_draws_db'].shape,(64,1,6));self.assertTrue(np.isfinite(out['mean_db']).all())
        self.assertGreaterEqual(float(out['sd_db'].min()),0);self.assertGreaterEqual(out['parameters'][0]['covariance_min_eigenvalue'],-1e-8)

    def test_request_rejects_tokens_forecasts_and_reversed_dates(self):
        request=json.loads((ROOT/'contracts/examples/world-point.json').read_text())
        bad={**request,'token':'do-not-accept-credentials-in-request'}
        with self.assertRaises(ValidationError):validate_request(bad)
        bad={**request,'analyses':['forecast']}
        with self.assertRaises(ValidationError):validate_request(bad)
        bad=json.loads(json.dumps(request));bad['source']['start_date']='2026-12-01'
        with self.assertRaises(ValueError):validate_request(bad)

    def test_invalid_power_and_duplicate_dates_are_rejected(self):
        cube={'power':np.ones((3,1,8,8)),'valid':np.ones((3,1,8,8),bool),'dates':['2026-01-01','2026-01-02','2026-01-02'],'polarizations':['HH'],'x':np.arange(8)*20.,'y':-np.arange(8)*20.,'epsg':32614}
        with self.assertRaises(ValueError):validate_cube(cube)
        cube['dates']=['2026-01-01','2026-01-02','2026-01-03'];cube['power'][0,0,0,0]=-1
        with self.assertRaises(ValueError):validate_cube(cube)

    def test_cog_retains_values_and_pixel_center_georeference(self):
        base=ROOT/'scratch/test_runs';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(base.resolve()))
            path=Path(temp)/'test.tif';a=np.arange(256,dtype='float32').reshape(16,16)
            x=500000+np.arange(16)*20;y=5300000-np.arange(16)*20
            write_cog(path,a,x,y,32614,'test value')
            with rasterio.open(path) as ds:
                np.testing.assert_array_equal(ds.read(1),a);self.assertEqual(ds.xy(0,0),(x[0],y[0]));self.assertEqual(ds.crs.to_epsg(),32614)

    def test_missing_support_returns_no_data_instead_of_empty_success(self):
        base=ROOT/'scratch/test_runs';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(base.resolve()))
            temp=Path(temp);p=temp/'cube.npz'
            np.savez(p,power=np.ones((3,1,8,8),dtype='float32'),valid=np.zeros((3,1,8,8),bool),dates=np.array(['2026-01-01','2026-01-13','2026-01-25']),polarizations=np.array(['HH']),x=500000+np.arange(8)*20,y=5300000-np.arange(8)*20,epsg=32614,radar_band='L')
            r=analyze({'contract_version':'1.0','request_id':'no-data-test','source':{'mode':'local_cube','cube_path':str(p)},'analyses':['physical','spectral']},temp/'out')
            self.assertEqual(r['status'],'no_data');self.assertEqual(r['capabilities']['physical']['status'],'insufficient_data')

    def test_degrees_are_not_silently_treated_as_metres(self):
        cube={'power':np.ones((3,1,8,8)),'valid':np.ones((3,1,8,8),bool),'dates':['2026-01-01','2026-01-02','2026-01-03'],'polarizations':['HH'],'x':np.arange(8)*.01,'y':-np.arange(8)*.01,'epsg':4326}
        with self.assertRaises(ValueError):validate_cube(cube)

    def test_component_reference_never_connects_separate_islands(self):
        phase=np.arange(9,dtype=float).reshape(3,3);component=np.array([[1,1,1],[2,2,2],[2,2,2]])
        pair={'wrapped_complex':np.exp(1j*phase),'wrapped_coherence':np.ones((3,3)),'unwrapped_coherence':np.ones((3,3)),'unwrappedPhase':phase,'connectedComponents':component,'wrapped_mask':np.full((3,3),11,dtype='uint32'),'unwrapped_mask':np.full((3,3),11,dtype='uint32'),'center_frequency_hz':1239000000.}
        result=analyze_pair(pair)
        self.assertEqual(result['metadata']['selected_component'],2);self.assertTrue(np.isnan(result['referenced_unwrapped_rad'][0]).all())
        self.assertIn('unavailable',result['metadata']['los_displacement_status'])

    def test_packaged_schemas_match_published_contracts(self):
        from surface_dynamics.engine import SCHEMAS
        for path in (ROOT/'contracts').glob('*.schema.json'):
            self.assertEqual(json.loads(path.read_text()),json.loads((SCHEMAS/path.name).read_text()))

    def test_fresh_worker_writes_cf_crs_and_preserves_measurements(self):
        import xarray as xr
        base=ROOT/'scratch/test_runs';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(base.resolve()))
            temp=Path(temp);path=temp/'cube.npz';rng=np.random.default_rng(7)
            power=np.exp(rng.normal(-2,.4,(6,1,16,16))).astype('float32')
            np.savez(path,power=power,valid=np.ones_like(power,bool),dates=np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-18','2026-03-02','2026-03-14']),polarizations=np.array(['HH']),x=500000+np.arange(16)*20,y=5300000-np.arange(16)*20,epsg=32614,radar_band='L')
            r=analyze({'contract_version':'1.0','request_id':'fresh-cf-test','source':{'mode':'local_cube','cube_path':str(path)},'analyses':['physical']},temp/'out')
            self.assertEqual(r['status'],'complete')
            with xr.open_dataset(temp/'out/surface_dynamics.nc') as ds:
                self.assertIn('spatial_ref',ds);self.assertEqual(ds.gamma0_power.attrs['grid_mapping'],'spatial_ref')
                np.testing.assert_array_equal(ds.gamma0_power.values,power)

if __name__=='__main__':unittest.main()
