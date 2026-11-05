"""Compact masked temporal Transformer; measured acquisition reconstruction only.

Inspired by masked time-series learning, not a reproduction of a published model.
The four named polarization slots prevent silently substituting VV/VH for HH/HV.
"""
from pathlib import Path
import json,copy,hashlib
import numpy as np
import torch
from torch import nn
from .analysis import measured_db,elapsed_days
from .io import POLS,load_cube

class MaskedTemporalTransformer(nn.Module):
    def __init__(self,width=48,layers=2,dropout=.1):
        super().__init__();self.width=width
        self.input=nn.Linear(12,width)
        self.time=nn.Sequential(nn.Linear(9,width),nn.GELU(),nn.Linear(width,width))
        self.mask_token=nn.Parameter(torch.zeros(width))
        layer=nn.TransformerEncoderLayer(width,4,width*2,dropout=dropout,activation='gelu',batch_first=True,norm_first=True)
        self.encoder=nn.TransformerEncoder(layer,layers,enable_nested_tensor=False)
        self.output=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,4))

    def forward(self,x,present,t,padding,masked=None):
        if masked is None:masked=torch.zeros(x.shape[:2],dtype=torch.bool,device=x.device)
        visible=present & ~masked[:,:,None]
        values=torch.where(visible,x,0)
        z=self.input(torch.cat([values,visible.float(),present.float()],-1))
        angles=t[:,:,None]/torch.tensor([12.,48.,180.,365.],device=x.device)[None,None,:]*2*torch.pi
        te=torch.cat([t[:,:,None]/365,torch.sin(angles),torch.cos(angles)],-1)
        z=z+self.time(te)+masked[:,:,None]*self.mask_token
        z=self.encoder(z,src_key_padding_mask=padding)
        return self.output(z),z

def sequences(cube):
    db=measured_db(cube);T,C,H,W=db.shape
    x=np.zeros((H*W,T,4),dtype='float32');present=np.zeros_like(x,dtype=bool)
    for ch,p in enumerate(cube['polarizations']):
        slot=POLS.index(str(p));a=db[:,ch].reshape(T,-1).T
        x[:,:,slot]=np.where(np.isfinite(a),a,0);present[:,:,slot]=np.isfinite(a)
    return x,present,elapsed_days(cube['dates']).astype('float32')

def collate(samples,mean,scale):
    N=len(samples);T=max(s[0].shape[0] for s in samples)
    x=np.zeros((N,T,4),dtype='float32');p=np.zeros_like(x,dtype=bool);t=np.zeros((N,T),dtype='float32');padding=np.ones((N,T),dtype=bool)
    for i,(a,b,c) in enumerate(samples):
        k=len(a);x[i,:k]=(a-mean)/scale;p[i,:k]=b;t[i,:k]=c;padding[i,:k]=False
    return [torch.from_numpy(a) for a in [x,p,t,padding]]

def evaluate(model,cases,mean,scale,seed=51,max_pixels=512):
    rng=np.random.default_rng(seed);model.eval();results={}
    with torch.no_grad():
        for name,cube in cases.items():
            x,p,t=sequences(cube);ids=np.flatnonzero(p.all(1).any(1))
            if len(ids)>max_pixels:ids=rng.choice(ids,max_pixels,replace=False)
            j=len(t)//2;chosen=int(j)
            samples=[(x[i],p[i],t) for i in ids];a,b,tt,pad=collate(samples,mean,scale)
            mask=torch.zeros(a.shape[:2],dtype=torch.bool);mask[:,j]=True
            out,_=model(a,b,tt,pad,mask)
            target=x[ids,j];pred=out[:,j].numpy()*scale+mean;ok=p[ids,j]
            visible=np.delete(x[ids],j,axis=1);visp=np.delete(p[ids],j,axis=1)
            count=visp.sum(1);baseline=np.sum(np.where(visp,visible,0),axis=1)/np.maximum(count,1)
            # Actual-time local interpolation uses measured neighbors, with no synthesized output cube.
            left=max(0,j-1);right=min(len(t)-1,j+1)
            alpha=(t[j]-t[left])/max(float(t[right]-t[left]),1e-6)
            linear=x[ids,left]*(1-alpha)+x[ids,right]*alpha
            rmse=lambda pred:float(np.sqrt(np.mean((pred[ok]-target[ok])**2)))
            results[name]={'task':'withheld_internal_acquisition_reconstruction','masked_date':str(cube['dates'][j]),'pixel_count_correlated':len(ids),'transformer_rmse_db':rmse(pred),'visible_temporal_mean_rmse_db':rmse(baseline),'linear_interpolation_rmse_db':rmse(linear),'effective_geographic_case_count':1}
    return results

def train(train_paths,validation_paths,output,epochs=10,seed=47):
    torch.manual_seed(seed);torch.set_num_threads(4);rng=np.random.default_rng(seed)
    train_cases={Path(p).stem:load_cube(p) for p in train_paths};test_cases={Path(p).stem:load_cube(p) for p in validation_paths}
    samples=[];norm_samples=[];split_counts={}
    for name,cube in train_cases.items():
        x,p,t=sequences(cube);H,W=cube['power'].shape[-2:]
        # Spatially separated validation stripe; these pixels never train the network.
        yy,xx=np.indices((H,W));eligible=(xx.ravel()<int(W*.75))&p.all(1).any(1)
        ids=np.flatnonzero(eligible);ids=rng.choice(ids,min(1536,len(ids)),replace=False)
        samples.extend([(x[i],p[i],t) for i in ids]);norm_samples.extend([x[i][p[i]] for i in ids])
        split_counts[name]=len(ids)
    # Fit normalization independently for each measured polarization on training pixels only.
    means=np.zeros(4,dtype='float32');scales=np.ones(4,dtype='float32');supported=[]
    for ch in range(4):
        values=np.concatenate([a[:,ch][b[:,ch]] for a,b,_ in samples])
        if len(values):means[ch]=np.median(values);scales[ch]=max(float(np.std(values)),1);supported.append(POLS[ch])
    model=MaskedTemporalTransformer();optimizer=torch.optim.AdamW(model.parameters(),lr=8e-4,weight_decay=.01)
    log=[];best=np.inf;best_state=None
    for epoch in range(epochs):
        model.train();losses=[]
        for begin in range(0,len(samples),128):
            if begin==0:order=rng.permutation(len(samples))
            batch=[samples[i] for i in order[begin:begin+128]]
            x,p,t,pad=collate(batch,means,scales)
            masked=(torch.rand(x.shape[:2])<.3)&~pad
            for i in range(len(batch)):
                n=int((~pad[i]).sum());masked[i,n//2]=True
                if masked[i,:n].sum()>n-2:masked[i,:2]=False
            pred,_=model(x,p,t,pad,masked)
            target_mask=p&masked[:,:,None]&~pad[:,:,None]
            loss=((pred-x)**2)[target_mask].mean()
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();losses.append(float(loss.detach()))
        # Model selection sees only the held-out stripe of training geographies.
        spatial_val={}
        for name,c in train_cases.items():
            copy_cube={k:v for k,v in c.items()};cut=int(c['power'].shape[-1]*.8)
            copy_cube['power']=c['power'][... ,cut:];copy_cube['valid']=c['valid'][... ,cut:];copy_cube['x']=c['x'][cut:]
            spatial_val[name]=copy_cube
        val=evaluate(model,spatial_val,means,scales,max_pixels=128)
        score=float(np.mean([v['transformer_rmse_db'] for v in val.values()]))
        log.append({'epoch':epoch+1,'train_normalized_mse':float(np.mean(losses)),'spatial_validation_rmse_db':score})
        print('TRANSFORMER_EPOCH',json.dumps(log[-1]),flush=True)
        if score<best:best=score;best_state=copy.deepcopy(model.state_dict())
    model.load_state_dict(best_state);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    torch.save({'state_dict':model.state_dict(),'mean':torch.from_numpy(means),'scale':torch.from_numpy(scales),'width':48,'layers':2,'supported_polarizations':supported,'radar_bands':['L'],'training_cases':list(train_cases),'seed':seed},output/'masked_temporal_transformer.pt')
    from .representation import fit_projections
    fit_projections(model,train_cases,means,scales,output)
    results={'architecture':'2-layer bidirectional masked temporal Transformer, width48, heads4','parameters':sum(p.numel() for p in model.parameters()),'training_cases':list(train_cases),'heldout_geographic_cases':list(test_cases),'training_pixel_sequences':split_counts,'polarizations':supported,'trained_bands':['L'],'epochs':log,'heldout_case_results':evaluate(model,test_cases,means,scales),'interpretation':'Self-supervised measured-date reconstruction and exploratory embeddings; no event labels, forecast or demonstrated global generalization. All observations in a sequence may contextualize a masked date.'}
    (output/'training_report.json').write_text(json.dumps(results,indent=2))
    return results

def infer(cube,checkpoint,dropout_draws=4):
    if cube['power'].shape[0]*np.prod(cube['power'].shape[-2:])>1_000_000:
        return {'status':'unsupported_model_domain','reason':'Learned-state job exceeds one million pixel/date vectors; coarsen the grid or request fewer dates'}
    torch.set_num_threads(4);state=torch.load(checkpoint,map_location='cpu',weights_only=True)
    pols=list(map(str,cube['polarizations']))
    if not set(pols)<=set(state['supported_polarizations']) or str(cube['radar_band']) not in state['radar_bands']:
        return {'status':'unsupported_model_domain','reason':'Checkpoint did not train on these polarization/band semantics'}
    model=MaskedTemporalTransformer(state['width'],state['layers']);model.load_state_dict(state['state_dict']);model.eval()
    mean=state['mean'].numpy();scale=state['scale'].numpy();x,p,t=sequences(cube);H,W=cube['power'].shape[-2:]
    supported=np.flatnonzero(p.all(1).any(1));embedding=np.full((H*W,48),np.nan,dtype='float32');uncertainty=np.full(H*W,np.nan,dtype='float32')
    reconstruction=np.full_like(x,np.nan);states=np.full((H*W,len(t),48),np.nan,dtype='float32')
    rng_state=torch.random.get_rng_state();torch.manual_seed(73)
    with torch.no_grad():
        for begin in range(0,len(supported),256):
            ids=supported[begin:begin+256];batch=collate([(x[i],p[i],t) for i in ids],mean,scale)
            a,b,tt,pad=batch;out,z=model(a,b,tt,pad)
            states[ids]=z.numpy();embedding[ids]=z.mean(1).numpy();reconstruction[ids]=out.numpy()*scale+mean
            model.train();draw=[]
            for _ in range(dropout_draws):
                _,zz=model(a,b,tt,pad);draw.append(zz.mean(1).numpy())
            uncertainty[ids]=np.std(np.stack(draw),axis=0).mean(1);model.eval()
    torch.random.set_rng_state(rng_state)
    return {'status':'available','embedding':embedding.reshape(H,W,48),'state':states.transpose(1,0,2).reshape(len(t),H,W,48),'dropout_embedding_sd':uncertainty.reshape(H,W),'reconstruction_db_four_slots':reconstruction.transpose(1,2,0).reshape(len(t),4,H,W),'metadata':{'checkpoint':Path(checkpoint).name,'checkpoint_sha256':hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),'training_cases':state['training_cases'],'dropout_draws':dropout_draws,'uncertainty_type':'uncalibrated_MC_dropout_latent_sensitivity','reconstruction':'Full-input decoder response, not a withheld-observation validation score','state_context':'Bidirectional context from all selected observations, including later observations within the requested interval; not an online or forecast state'}}
