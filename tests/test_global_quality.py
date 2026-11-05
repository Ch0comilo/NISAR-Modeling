"""Global partial-footprint, source identity and physical-coordinate checks."""
import hashlib,json,tempfile,unittest,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import xarray as xr
from rasterio.warp import transform
from surface_dynamics.analysis import measured_db,physical_statistics,spatial_structure
from surface_dynamics.io import fetch_chip
from surface_dynamics.probe import probe


class GlobalQualityTests(unittest.TestCase):
    def test_small_positive_power_is_not_silently_floored_in_db(self):
        cube={'power':np.array([1e-14,0.,1.],dtype='float64'),'valid':np.array([True,False,True])}
        result=measured_db(cube);self.assertEqual(result[0],-140.);self.assertTrue(np.isnan(result[1]));self.assertEqual(result[2],0.)

    def test_no_data_statistics_and_texture_remain_masked_without_warnings(self):
        db=np.ones((4,1,16,16));db[:,:,0:4]=np.nan
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always');stat=physical_statistics(db,np.arange(4.));texture=spatial_structure(db,160)
        self.assertEqual(caught,[]);self.assertTrue(np.isnan(stat['median_db'][:,0:4]).all())
        self.assertTrue(np.isnan(texture['spatial_anisotropy'][0:4]).all())

    def test_gradient_orientation_uses_east_north_metres_for_rectangular_pixels(self):
        x=np.arange(64)*100.;y=-np.arange(64)*200.
        plane=(x[None,:]+y[:,None])*.0002;db=np.tile(plane[None,None],(3,1,1,1))
        result=spatial_structure(db,100,200)
        np.testing.assert_allclose(result['spatial_orientation_radians'][16:48,16:48],np.pi/4,atol=1e-3)

    def test_cached_chip_must_match_requested_granule_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'chip.npz';np.savez(path,test=np.arange(4))
            path.with_suffix('.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'title':'different granule','query_point':[0,0],'requested_pixels':512,'crop_policy':'point_centered'}))
            with patch('surface_dynamics.io.load_range_reader',side_effect=RuntimeError('fresh source read requested')):
                with self.assertRaisesRegex(RuntimeError,'fresh source read requested'):fetch_chip({'title':'requested granule'},0,0,path,token='in-memory-test')

    def test_map_probe_inside_grid_without_samples_returns_no_valid_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.nc';x=500000+np.arange(4)*160.;y=5300000-np.arange(4)*160.
            ds=xr.Dataset({'gamma0_db':(('time','polarization','y','x'),np.full((3,1,4,4),np.nan))},
                          coords={'time':np.array(['2026-01-01','2026-01-13','2026-01-25'],dtype='datetime64[D]'),'polarization':['HH'],'x':x,'y':y},attrs={'epsg_code':32614})
            ds.to_netcdf(path,engine='h5netcdf');lon,lat=transform('EPSG:32614','EPSG:4326',[x[1]],[y[1]])
            result=probe({'contract_version':'1.0','request_id':'empty-point','dataset_path':str(path),'longitude':lon[0],'latitude':lat[0]})
            self.assertEqual(result['status'],'no_valid_observations');self.assertEqual(result['observed_series'],[])


if __name__=='__main__':unittest.main()
