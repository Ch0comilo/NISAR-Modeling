"""Science-only end-to-end jobs; no mockup or application files are read."""
from pathlib import Path
import json
from surface_dynamics.engine import analyze,analyze_insar

ROOT=Path(__file__).resolve().parents[1]
cases=json.loads((ROOT/'configs/global_cases.json').read_text())
contexts={
 'deliblato':{'event_date':'2026-08-02','source_url':'https://www.esa.int/ESA_Multimedia/Images/2026/08/Wildfire_damage_east_of_Belgrade_Serbia','description':'Independently reported regional fire context; no burn polygon'},
 'chenab':{'event_date':'2026-07-22','source_url':'https://ffd.pmd.gov.pk/b/eacdcd80-11ca-4316-a9e9-e337e5c7d8fd','description':'Regional Chenab high-flood bulletin; no inundation reference polygon'},
 'niscemi':{'event_date':'2026-01-25','source_url':'https://www.protezionecivile.gov.it/en/notizia/niscemi-landslide-national-service-efforts-continue/index.html','description':'Independent landslide context; all selected acquisitions occur afterwards'}
}
summary=[]
for c in cases:
    if c['id']=='kumamoto':continue
    request={'contract_version':'1.0','request_id':c['id'],'theme':c['theme'],'source':{'mode':'local_cube','cube_path':str(ROOT/'products/cubes'/(c['id']+'.npz'))},'analyses':['physical','spectral','stochastic','transformer'],'transformer_checkpoint':str(ROOT/'models/global_explorer_v1/masked_temporal_transformer.pt'),'stochastic_draws':256}
    if c['id'] in contexts:request['context']=contexts[c['id']]
    out=ROOT/'products/runs'/c['id'];out.mkdir(parents=True,exist_ok=True)
    (out/'request.json').write_text(json.dumps(request,indent=2))
    result=analyze(request,out)
    summary.append({'case_id':c['id'],'name':c['name'],'theme':c['theme'],'dates':len(result['observations']['dates']),'status':result['status'],'artifacts':len(result['artifacts']),'result':str(out/'result.json')})
    print('ANALYZED',json.dumps(summary[-1]),flush=True)
(ROOT/'products/global_examples.json').write_text(json.dumps(summary,indent=2))
request=json.loads((ROOT/'contracts/examples/kumamoto-earthquake.json').read_text());analyze_insar(request,ROOT/'products/runs/kumamoto-earthquake')
print('GLOBAL_EXAMPLES_COMPLETE',flush=True)
