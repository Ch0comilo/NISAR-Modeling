"""Fail before generated data, credentials, or oversized objects reach Git."""
from pathlib import Path
import argparse,json,re,subprocess

ROOT=Path(__file__).resolve().parents[1]


def audit(ref='HEAD'):
    entries=[];failures=[]
    forbidden={'products','arrays','scratch','descriptive','research','deliverables','mockup','.venv','.secrets','build','dist'}
    for line in subprocess.check_output(['git','ls-tree','-rl',ref],cwd=ROOT,text=True).splitlines():
        meta,path=line.split('\t',1);mode,kind,sha,size=meta.split();size=int(size)
        entries.append({'path':path,'bytes':size})
        if path.split('/')[0] in forbidden or Path(path).suffix=='.dpapi' or (Path(path).name.startswith('.env') and path != '.env.example'):failures.append(f'Forbidden tracked path: {path}')
        if size>10*1024*1024:failures.append(f'File exceeds 10 MiB Git budget: {path}')
        data=subprocess.check_output(['git','cat-file','blob',sha],cwd=ROOT)
        if path == '.env.example':
            public_options={'NISAR_APP_NAME','NISAR_VOLUME','NISAR_STATE_DICT','NISAR_EARTHDATA_SECRET'}
            for line in data.decode('utf-8').splitlines():
                if not line.strip() or line.lstrip().startswith('#'):continue
                key,separator,value=line.partition('=')
                if not separator or key not in public_options or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}',value):
                    failures.append('Only non-secret service resource names belong in .env.example')
        if path == '.env.example' or Path(path).suffix in {'.py','.md','.json','.toml','.yml','.txt'}:
            # Patterns detect credential structures, never print matching values.
            patterns=[rb'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',rb'(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}',rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----']
            if any(re.search(p,data) for p in patterns):failures.append(f'Possible literal credential in: {path}')
    total=sum(e['bytes'] for e in entries)
    if total>10*1024*1024:failures.append('Repository tree exceeds 10 MiB budget; move generated data to release assets')
    result={'tracked_files':len(entries),'tracked_bytes':total,'largest_file':max(entries,key=lambda e:e['bytes']) if entries else None,'failures':failures}
    print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ref',default='HEAD');args=parser.parse_args();audit(args.ref)
