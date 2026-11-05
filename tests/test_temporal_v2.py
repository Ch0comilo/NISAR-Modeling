"""Leakage and physical-baseline gates for the new reconstruction model."""
import unittest
import numpy as np
import torch
from surface_dynamics.temporal_v2 import ResidualTemporalTransformer
from surface_dynamics.benchmark import interpolation_baseline


class TemporalV2Tests(unittest.TestCase):
    def inputs(self):
        torch.manual_seed(19)
        x=torch.randn(3,8,4)*2-15;present=torch.ones_like(x,dtype=torch.bool)
        t=torch.tensor([0.,12.,24.,48.,60.,72.,96.,108.])[None].repeat(3,1)
        pad=torch.zeros(3,8,dtype=torch.bool);mask=torch.zeros_like(pad);mask[:,3]=True
        return x,present,t,pad,mask

    def test_hidden_measurements_cannot_leak_through_normalization_or_baseline(self):
        model=ResidualTemporalTransformer(width=16,layers=1,dropout=0).eval()
        # A nonzero trained-like residual head exercises more than the baseline.
        torch.nn.init.normal_(model.output[-1].weight,std=.02)
        x,p,t,pad,mask=self.inputs();changed=x.clone();changed[:,3]=10000
        out,z=model(x,p,t,pad,mask);other,zz=model(changed,p,t,pad,mask)
        torch.testing.assert_close(out,other,atol=0,rtol=0);torch.testing.assert_close(z,zz,atol=0,rtol=0)

    def test_zero_residual_matches_actual_time_interpolation(self):
        model=ResidualTemporalTransformer(width=16,layers=1,dropout=0).eval()
        x,p,t,pad,mask=self.inputs();out,_=model(x,p,t,pad,mask)
        expected,ok=interpolation_baseline(x.numpy(),p.numpy(),t[0].numpy(),3)
        np.testing.assert_allclose(out[:,3].detach().numpy()[ok],expected[ok],atol=2e-6)

    def test_level_and_time_origin_shifts_preserve_contextual_states(self):
        model=ResidualTemporalTransformer(width=16,layers=1,dropout=0).eval()
        x,p,t,pad,mask=self.inputs();out,z=model(x,p,t,pad,mask)
        shifted,zz=model(x+8,p,t+1000,pad,mask)
        torch.testing.assert_close(z,zz,atol=2e-5,rtol=2e-5)
        torch.testing.assert_close(out+8,shifted,atol=2e-5,rtol=2e-5)

    def test_missing_channel_and_padding_do_not_create_nan_states(self):
        model=ResidualTemporalTransformer(width=16,layers=1,dropout=0).eval()
        x,p,t,pad,mask=self.inputs();p[:,:,2:]=False;pad[1,6:]=True
        out,z=model(x,p,t,pad,mask)
        self.assertTrue(torch.isfinite(out).all());self.assertTrue(torch.isfinite(z).all())

    def test_constant_control_removes_pure_calendar_state_evolution(self):
        from surface_dynamics.dynamic_view import constant_control_states
        model=ResidualTemporalTransformer(width=16,layers=1,dropout=0).eval()
        x,p,t,pad,mask=self.inputs();x[:]=-12
        _,raw=model(x,p,t,pad)
        control=constant_control_states(model,x,p,t,pad)
        self.assertGreater(float((raw[:,1:]-raw[:,:-1]).detach().abs().max()),.01)
        torch.testing.assert_close(raw-control,torch.zeros_like(raw),atol=1e-6,rtol=0)


if __name__=='__main__':unittest.main()
