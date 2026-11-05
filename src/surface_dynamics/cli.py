import argparse,json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description='Descriptive radar surface dynamics; science/data artifacts only')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('capabilities')
    for name in ['analyze','insar']:
        p=sub.add_parser(name);p.add_argument('request');p.add_argument('--output',required=True)
    p=sub.add_parser('insar-network');p.add_argument('request');p.add_argument('--output',required=True)
    p=sub.add_parser('benchmark');p.add_argument('--cubes',nargs='+',required=True);p.add_argument('--checkpoint',required=True);p.add_argument('--output',required=True);p.add_argument('--max-pixels',type=int,default=512)
    p=sub.add_parser('probe');p.add_argument('request');p.add_argument('--output',required=True)
    p=sub.add_parser('train');p.add_argument('--training',nargs='+',required=True);p.add_argument('--heldout',nargs='+',required=True);p.add_argument('--output',required=True);p.add_argument('--epochs',type=int,default=10)
    p=sub.add_parser('catalog');p.add_argument('--longitude',type=float,required=True);p.add_argument('--latitude',type=float,required=True);p.add_argument('--start');p.add_argument('--end');p.add_argument('--product',choices=['GCOV','GUNW'],default='GCOV');p.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='capabilities':
        from .engine import SCHEMAS
        print((SCHEMAS/'phenomena-catalog.json').read_text(encoding='utf8'))
    elif args.command in ['analyze','insar']:
        from .engine import analyze,analyze_insar
        request=json.loads(Path(args.request).read_text(encoding='utf8'));result=(analyze if args.command=='analyze' else analyze_insar)(request,args.output)
        print(json.dumps({'request_id':result['request_id'],'status':result['status'],'result':str(Path(args.output)/'result.json'),'artifacts':len(result['artifacts'])}))
    elif args.command=='insar-network':
        from .network import plan_network
        result=plan_network(json.loads(Path(args.request).read_text()),args.output)
        print(json.dumps({'status':result['status'],'networks':len(result['networks']),'result':str(Path(args.output)/'result.json')}))
    elif args.command=='benchmark':
        from .benchmark import benchmark
        result=benchmark(args.cubes,args.checkpoint,max_pixels=args.max_pixels)
        Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False));print(json.dumps({'cases':len(result['cases']),'result':args.output}))
    elif args.command=='probe':
        from .probe import probe
        result=probe(json.loads(Path(args.request).read_text()));Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False));print(json.dumps({'status':result['status'],'result':args.output}))
    elif args.command=='train':
        from .transformer import train
        result=train(args.training,args.heldout,args.output,args.epochs);print(json.dumps(result['heldout_case_results']))
    elif args.command=='catalog':
        from .catalog import search,group_entries
        result=search(args.longitude,args.latitude,args.product,start=args.start,end=args.end);Path(args.output).write_text(json.dumps(result,indent=2));print(json.dumps({'hits':result['hits'],'compatible_groups':{k:len(v) for k,v in group_entries(result['entries'],args.product).items()}}))

if __name__=='__main__':main()
