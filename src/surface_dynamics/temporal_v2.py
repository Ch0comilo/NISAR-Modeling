"""Visible-only normalized, interpolation-residual temporal representation.

An original compact masked reconstruction model. Instance normalization is
motivated by RevIN (Kim et al., ICLR 2022); this is not its code or a reproduction.
Everything describes measured-date context, never a future acquisition.
"""
from pathlib import Path
import copy
import hashlib
import json
import numpy as np
import torch
from torch import nn
from .io import load_cube
from .transformer import sequences, collate

ARCHITECTURE = 'visible_normalized_residual_transformer_v2'


def visible_context(values, present, times, padding, masked):
    """Center, scale and interpolate using visible observations only."""
    visible = present & ~masked[:, :, None] & ~padding[:, :, None]
    clean = torch.where(visible, values, 0.)
    count = visible.sum(1).clamp(min=1)
    center = clean.sum(1) / count
    variance = torch.where(visible, (clean-center[:, None])**2, 0.).sum(1)/count
    scale = torch.sqrt(variance.clamp(min=.25))
    B, T, C = values.shape
    index = torch.arange(T, device=values.device)[None, :, None].expand(B, T, C)
    left = torch.where(visible, index, -1).cummax(1).values
    right = torch.flip(torch.flip(torch.where(visible, index, T), [1]).cummin(1).values, [1])
    li, ri = left.clamp(min=0), right.clamp(max=T-1)
    low = torch.gather(clean, 1, li); high = torch.gather(clean, 1, ri)
    time = times[:, :, None].expand(B, T, C)
    lt = torch.gather(time, 1, li); rt = torch.gather(time, 1, ri)
    fraction = (time-lt)/(rt-lt).clamp(min=1e-6)
    interpolated = low*(1-fraction)+high*fraction
    baseline = torch.where((left>=0)&(right<T), interpolated,
                           torch.where(left>=0, low, torch.where(right<T, high, center[:, None])))
    t0 = torch.where(~padding, times, torch.inf).amin(1)
    span = (torch.where(~padding, times, -torch.inf).amax(1)-t0).clamp(min=1.)
    left_gap = torch.where(left>=0, (time-lt)/span[:, None, None], 1.)
    right_gap = torch.where(right<T, (rt-time)/span[:, None, None], 1.)
    return clean, visible, center, scale, baseline, left_gap, right_gap, t0, span


class ResidualTemporalTransformer(nn.Module):
    def __init__(self, width=48, layers=2, dropout=.1):
        super().__init__()
        self.width=width
        self.input=nn.Linear(24, width)
        self.time=nn.Sequential(nn.Linear(9,width),nn.GELU(),nn.Linear(width,width))
        self.mask_token=nn.Parameter(torch.zeros(width))
        layer=nn.TransformerEncoderLayer(width,4,width*2,dropout=dropout,
                                         activation='gelu',batch_first=True,norm_first=True)
        self.encoder=nn.TransformerEncoder(layer,layers,enable_nested_tensor=False)
        self.output=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,4))
        # The initial reconstruction is the declared actual-time baseline.
        nn.init.zeros_(self.output[-1].weight);nn.init.zeros_(self.output[-1].bias)

    def forward(self, values, present, times, padding, masked=None):
        if masked is None:masked=torch.zeros(values.shape[:2],dtype=torch.bool,device=values.device)
        clean,visible,center,scale,base,lg,rg,t0,span=visible_context(values,present,times,padding,masked)
        normalized=torch.where(visible,(clean-center[:,None])/scale[:,None],0.)
        base_normalized=(base-center[:,None])/scale[:,None]
        features=torch.cat([normalized,visible.float(),present.float(),base_normalized,lg,rg],-1)
        relative=(times-t0[:,None])/span[:,None]
        angles=relative[:,:,None]*torch.tensor([1.,2.,4.,8.],device=values.device)[None,None]*2*torch.pi
        time_features=torch.cat([relative[:,:,None],torch.sin(angles),torch.cos(angles)],-1)
        z=self.input(features)+self.time(time_features)+masked[:,:,None]*self.mask_token
        z=self.encoder(z,src_key_padding_mask=padding)
        correction=.5*torch.tanh(self.output(z))*scale[:,None]
        return base+correction,z


def validation_score(model,cases,max_pixels=96,seed=113):
    """All internal dates; only training-region separated spatial stripes."""
    from .benchmark import interpolation_baseline
    rng=np.random.default_rng(seed);results={};model.eval()
    with torch.no_grad():
        for name,cube in cases.items():
            x,p,t=sequences(cube);ids=np.flatnonzero(p.all(1).any(1))
            if len(ids)>max_pixels:ids=rng.choice(ids,max_pixels,replace=False)
            if not len(ids):raise ValueError('Validation stripe has no complete trajectories')
            a,b,tt,pad=collate([(x[i],p[i],t) for i in ids],np.zeros(4),np.ones(4))
            sse=base_sse=0.;count=0
            for j in range(1,len(t)-1):
                linear,ok=interpolation_baseline(x[ids],p[ids],t,j)
                mask=torch.zeros(a.shape[:2],dtype=torch.bool);mask[:,j]=True
                output,_=model(a,b,tt,pad,mask)
                residual=(output[:,j].numpy()-x[ids,j])[ok]
                sse+=float(np.sum(residual.astype('float64')**2))
                base_sse+=float(np.sum((linear-x[ids,j])[ok].astype('float64')**2));count+=int(ok.sum())
            results[name]={'rmse_db':float(np.sqrt(sse/count)),
                           'interpolation_rmse_db':float(np.sqrt(base_sse/count)),
                           'scored_channel_pixels':count}
    return results


def train(recipe,cube_directory,output):
    seed=recipe['seed'];torch.manual_seed(seed);torch.set_num_threads(4);rng=np.random.default_rng(seed)
    names=recipe['training_regions']
    if set(names)&(set(recipe['legacy_development_regions'])|set(recipe['locked_test_regions'])):raise ValueError('Training/evaluation region overlap')
    cases={name:load_cube(Path(cube_directory)/f'{name}.npz') for name in names}
    samples=[];stripes={};counts={};supported=set();projection_cases={};splits={}
    for name,cube in cases.items():
        supported.update(map(str,cube['polarizations']));x,p,t=sequences(cube);H,W=cube['power'].shape[-2:]
        columns=np.tile(np.arange(W),H)
        complete=p.all(1).any(1);supported_columns=np.unique(columns[complete])
        if len(supported_columns)<6:raise ValueError(f'Insufficient spatially separated observed columns in {name}')
        last_train=int(supported_columns[max(int(len(supported_columns)*.75)-1,0)])
        first_val=int(supported_columns[min(int(np.ceil(len(supported_columns)*.85)),len(supported_columns)-1)])
        ids=np.flatnonzero((columns<=last_train)&complete)
        ids=rng.choice(ids,min(recipe['pixel_sequences_per_training_region'],len(ids)),replace=False)
        if len(ids)<64:raise ValueError(f'Too few training trajectories in {name}')
        samples.extend((x[i],p[i],t) for i in ids);counts[name]=len(ids)
        start=first_val;stripes[name]={**cube,'power':cube['power'][...,start:],'valid':cube['valid'][...,start:],'x':cube['x'][start:]}
        projection_cases[name]={**cube,'power':cube['power'][...,:last_train+1],'valid':cube['valid'][...,:last_train+1],'x':cube['x'][:last_train+1]}
        splits[name]={'training_last_column':last_train,'validation_first_column':first_val,
                      'excluded_buffer_columns':first_val-last_train-1,'basis':'supported columns only; no geographic test values',
                      'training_source_columns':len(supported_columns)}
    model=ResidualTemporalTransformer();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01)
    initial=validation_score(model,stripes);log=[];best=float('inf');best_state=None
    for epoch in range(recipe['epochs']):
        model.train();order=rng.permutation(len(samples));losses=[]
        for begin in range(0,len(samples),128):
            batch=[]
            for idx in order[begin:begin+128]:
                a,p,t=samples[idx];length=int(rng.integers(4,len(t)+1));start=int(rng.integers(0,len(t)-length+1))
                batch.append((a[start:start+length],p[start:start+length],t[start:start+length]))
            x,p,t,pad=collate(batch,np.zeros(4),np.ones(4));mask=torch.zeros(x.shape[:2],dtype=torch.bool)
            for i in range(len(batch)):
                n=int((~pad[i]).sum());j=int(rng.integers(1,n-1));mask[i,j]=True
                if n>=6 and rng.random()<.25:mask[i,min(j+1,n-2)]=True
            prediction,_=model(x,p,t,pad,mask)
            target=p&mask[:,:,None]&~pad[:,:,None]
            loss=((prediction-x)**2)[target].mean()
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();losses.append(float(loss.detach()))
        validation=validation_score(model,stripes)
        score=float(np.mean([v['rmse_db'] for v in validation.values()]))
        log.append({'epoch':epoch+1,'training_mse_db2':float(np.mean(losses)),
                    'case_balanced_spatial_validation_rmse_db':score})
        print('RESIDUAL_TRANSFORMER_EPOCH',json.dumps(log[-1]),flush=True)
        if score<best:best=score;best_state=copy.deepcopy(model.state_dict())
    model.load_state_dict(best_state);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    checkpoint=output/'masked_temporal_transformer.pt'
    torch.save({'architecture_id':ARCHITECTURE,'state_dict':model.state_dict(),'width':48,'layers':2,
                'mean':torch.zeros(4),'scale':torch.ones(4),'supported_polarizations':sorted(supported),
                'radar_bands':['L'],'training_cases':names,'seed':seed,'posting_m':recipe['target_posting_m'],
                'normalization':'visible-only per-trajectory channel mean and SD, SD floor 0.5 dB',
                'selected_epoch':int(np.argmin([e['case_balanced_spatial_validation_rmse_db'] for e in log]))+1},checkpoint)
    from .representation import fit_projections
    fit_projections(model,projection_cases,np.zeros(4),np.ones(4),output)
    final=validation_score(model,stripes)
    report={'architecture_id':ARCHITECTURE,'parameters':sum(p.numel() for p in model.parameters()),
            'recipe':recipe,'training_pixel_sequences':counts,'spatial_splits':splits,'projection_fit_scope':'training-side crops only; no validation or geographic test stripe',
            'initial_interpolation_baseline_validation':initial,
            'epochs':log,'selected_spatial_validation':final,
            'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            'test_scores_read_during_training':False,
            'interpretation':'Masked historical reconstruction and exploratory contextual states; not forecasting. Selection sees only training-region spatial stripes. Spatial dependence and only regional geographic replication remain.'}
    (output/'training_report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


def infer(cube,checkpoint,saved,dropout_draws=4):
    from .transformer import model_from_checkpoint
    if not np.isclose(cube['x'][1]-cube['x'][0],saved['posting_m']) or not np.isclose(cube['y'][0]-cube['y'][1],saved['posting_m']):
        return {'status':'unsupported_model_domain','reason':f'This checkpoint requires {saved["posting_m"]} m arithmetic linear-power aggregation; unsupported posting is not silently resampled'}
    torch.set_num_threads(4)
    model=model_from_checkpoint(saved);model.eval();x,p,t=sequences(cube);H,W=cube['power'].shape[-2:];T=len(t)
    eligible=np.flatnonzero(p.any(2).sum(1)>=3)
    states=np.full((H*W,T,48),np.nan,dtype='float32');uncertainty=np.full(H*W,np.nan,dtype='float32')
    rng_state=torch.random.get_rng_state();torch.manual_seed(127)
    try:
        with torch.no_grad():
            for begin in range(0,len(eligible),256):
                ids=eligible[begin:begin+256];batch=collate([(x[i],p[i],t) for i in ids],np.zeros(4),np.ones(4))
                _,z=model(*batch);states[ids]=z.numpy();draw=[];model.train()
                for _ in range(dropout_draws):
                    _,z=model(*batch);draw.append(z.mean(1).numpy())
                uncertainty[ids]=np.std(np.stack(draw),axis=0).mean(1);model.eval()
    finally:torch.random.set_rng_state(rng_state)
    state=states.transpose(1,0,2).reshape(T,H,W,48)
    rate=np.sqrt(np.mean(np.diff(state,axis=0)**2,axis=-1))/np.diff(t)[:,None,None]
    observed=p.any(2).T.reshape(T,H,W);rate=np.where(observed[1:]&observed[:-1],rate,np.nan)
    return {'status':'available','embedding':states.mean(1).reshape(H,W,48),'state':state,
            'dropout_embedding_sd':uncertainty.reshape(H,W),'contextual_state_change_per_day':rate.astype('float32'),
            'metadata':{'architecture_id':ARCHITECTURE,'checkpoint':Path(checkpoint).name,
                        'checkpoint_sha256':hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
                        'training_cases':saved['training_cases'],'normalization':saved['normalization'],
                        'training_posting_m':saved['posting_m'],'input_posting_m':float(cube['x'][1]-cube['x'][0]),
                        'posting_matches_training':bool(np.isclose(cube['x'][1]-cube['x'][0],saved['posting_m'])),
                        'dropout_draws':dropout_draws,'uncertainty_type':'uncalibrated_MC_dropout_latent_sensitivity',
                        'state_context':'Bidirectional context from all selected dates; only observed acquisitions, no forecast',
                        'state_change_units':'RMS latent-coordinate difference per elapsed day; not physical speed, motion or event likelihood'}}
