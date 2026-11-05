"""End-to-end training output and empty-acquisition provenance checks."""
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from surface_dynamics.io import exclude_empty_acquisitions
from surface_dynamics.stochastic import reconstruct_internal_gp


class ModelPreparationTests(unittest.TestCase):
    def test_empty_date_exclusion_keeps_partial_measurements_and_source_qa(self):
        power=np.ones((4,1,4,4),dtype='float32');valid=np.ones_like(power,bool);valid[1]=False;valid[2,0,0,0]=False
        cube={'power':power,'valid':valid,'dates':np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-06']),
              'source_metadata_json':np.array(json.dumps([{'id':i} for i in range(4)]))}
        cleaned,excluded=exclude_empty_acquisitions(cube)
        self.assertEqual(len(cleaned['dates']),3);self.assertEqual(excluded[0]['source']['id'],1)
        self.assertEqual(int(cleaned['valid'][1].sum()),15)
        self.assertEqual(len(cube['dates']),4)
        self.assertIn('excluded_empty_acquisitions',json.loads(str(cleaned['processing_metadata_json'])))

    def test_gp_hidden_values_do_not_enter_fit_or_conditional_distribution(self):
        rng=np.random.default_rng(5);values=rng.normal(size=(16,7,1));present=np.ones_like(values,bool);t=np.array([0,12,24,48,60,72,84.])
        first=reconstruct_internal_gp(values,present,t,3);other=values.copy();other[:,3]=10000
        second=reconstruct_internal_gp(other,present,t,3)
        np.testing.assert_allclose(first['mean_db'],second['mean_db'],rtol=0,atol=0)
        np.testing.assert_allclose(first['observation_sd_db'],second['observation_sd_db'],rtol=0,atol=0)
        self.assertTrue((first['observation_sd_db']>0).all())
        with self.assertRaises(ValueError):reconstruct_internal_gp(values,present,t,0)

    def test_training_writes_reloadable_checkpoint_without_opening_test_region(self):
        import torch
        from surface_dynamics.temporal_v2 import train
        from surface_dynamics.transformer import model_from_checkpoint
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rng=np.random.default_rng(11)
            power=np.exp(rng.normal(-2,.2,(6,2,16,16))).astype('float32')
            np.savez(root/'toy.npz',power=power,valid=np.ones_like(power,bool),dates=np.array(['2026-01-01','2026-01-13','2026-01-25','2026-02-06','2026-02-18','2026-03-02']),polarizations=np.array(['HH','HV']),x=500000+np.arange(16)*160,y=5300000-np.arange(16)*160,epsg=32614,radar_band='L')
            recipe={'seed':109,'epochs':1,'training_regions':['toy'],'legacy_development_regions':[],'locked_test_regions':['nonexistent_test_cube'],
                    'pixel_sequences_per_training_region':64,'target_posting_m':160}
            report=train(recipe,root,root/'model')
            checkpoint=torch.load(root/'model/masked_temporal_transformer.pt',map_location='cpu',weights_only=True)
            self.assertEqual(checkpoint['selected_epoch'],1);model_from_checkpoint(checkpoint)
            self.assertFalse(report['test_scores_read_during_training'])
            self.assertTrue((root/'model/embedding_projection.npz').exists())
            self.assertLess(report['spatial_splits']['toy']['training_last_column'],report['spatial_splits']['toy']['validation_first_column'])


if __name__=='__main__':unittest.main()
