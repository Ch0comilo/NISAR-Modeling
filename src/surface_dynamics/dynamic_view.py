"""Constant-trajectory control for frozen contextual representations.

Subtract the same model's response to a constant observed trajectory with the
same times and masks. This removes a pure calendar/mask response; it is an
exploratory representation contrast, not causal attribution or physical motion.
"""
from pathlib import Path
import numpy as np
import torch
from .transformer import model_from_checkpoint,sequences,collate
from .io import load_cube
from .representation import fit_basis


def constant_control_states(model,values,present,times,padding):
    count=(present&~padding[:,:,None]).sum(1).clamp(min=1)
    mean=torch.where(present&~padding[:,:,None],values,0.).sum(1)/count
    control=mean[:,None].expand_as(values)
    _,state=model(control,present,times,padding)
    return state


def controlled_view(cube,checkpoint,raw_state):
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    model=model_from_checkpoint(saved);model.eval();x,p,t=sequences(cube)
    H,W=cube['power'].shape[-2:];T=len(t);ids=np.flatnonzero(p.any(2).sum(1)>=3)
    control=np.full((H*W,T,48),np.nan,dtype='float32')
    with torch.no_grad():
        for begin in range(0,len(ids),256):
            selected=ids[begin:begin+256]
            batch=collate([(x[i],p[i],t) for i in selected],np.zeros(4),np.ones(4))
            control[selected]=constant_control_states(model,*batch).numpy()
    state=raw_state-control.transpose(1,0,2).reshape(T,H,W,48)
    observed=p.any(2).T.reshape(T,H,W)
    state=np.where(observed[:,:,:,None],state,np.nan)
    rate=np.sqrt(np.mean(np.diff(state,axis=0)**2,axis=-1))/np.diff(t)[:,None,None]
    return {'state':state.astype('float32'),'change_per_day':rate.astype('float32'),
            'interpretation':'Frozen-model actual trajectory minus constant-trajectory control with identical dates/masks; not causal attribution, a measured band, physical motion or an event score'}


def fit_dynamic_projection(cube_directory,model_directory):
    import json
    model_directory=Path(model_directory);report=json.loads((model_directory/'training_report.json').read_text())
    saved=torch.load(model_directory/'masked_temporal_transformer.pt',map_location='cpu',weights_only=True)
    model=model_from_checkpoint(saved);model.eval();rng=np.random.default_rng(139);features=[]
    with torch.no_grad():
        for name in report['recipe']['training_regions']:
            cube=load_cube(Path(cube_directory)/f'{name}.npz');x,p,t=sequences(cube);H,W=cube['power'].shape[-2:]
            columns=np.tile(np.arange(W),H)
            ids=np.flatnonzero((columns<=report['spatial_splits'][name]['training_last_column'])&p.all(1).any(1))
            if len(ids)>512:ids=rng.choice(ids,512,replace=False)
            batch=collate([(x[i],p[i],t) for i in ids],np.zeros(4),np.ones(4))
            _,actual=model(*batch);control=constant_control_states(model,*batch)
            features.append((actual-control).numpy().reshape(-1,48))
    fit_basis(np.concatenate(features),model_directory/'dynamic_projection.npz')
    return {'fitting_scope':'training-side spatial pixels only, every measured date; no validation/test data',
            'constant_control':'same frozen weights, acquisition dates and masks; each observed channel replaced by its visible time mean',
            'interpretation':'Representation contrast removes the pure calendar/mask response. Nonlinear interactions remain; no causal or physical parameter identification.'}
