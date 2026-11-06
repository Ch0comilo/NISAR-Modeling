"""Public catalog discovery; retain real acquisition identities and no-data results."""
from collections import defaultdict
from datetime import datetime, timezone
import re
import requests

CMR='https://cmr.earthdata.nasa.gov/search/granules.json'
COLLECTIONS={'GCOV':'NISAR_L2_GCOV_PROVISIONAL_V1','GUNW':'NISAR_L2_GUNW_PROVISIONAL_V1'}

def search(longitude, latitude, product='GCOV', start=None, end=None, max_records=2000,collection=None, *, timeout=(10,60)):
    if not -180<=longitude<=180 or not -90<=latitude<=90:raise ValueError('Coordinates outside WGS84 bounds')
    if product not in COLLECTIONS:raise ValueError('Unsupported product')
    selected=collection or COLLECTIONS[product]
    allowed={COLLECTIONS[product]}
    if product=='GCOV':allowed.update(['NISAR_L2_GCOV_BETA_V1','NISAR_L2_GCOV_V1'])
    if selected not in allowed:raise ValueError('Unsupported product collection')
    end=end or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    params={'short_name':selected,'point':f'{longitude},{latitude}','page_size':min(max_records,2000),'sort_key':'start_date','temporal':f'{start or ""},{end}'}
    response=requests.get(CMR,params=params,timeout=timeout);response.raise_for_status()
    hits=int(response.headers.get('CMR-Hits','0'))
    if hits>min(max_records,2000):raise ValueError('Catalog result exceeds limit; narrow dates rather than silently truncate')
    return {'query_url':response.url,'queried_at_utc':datetime.now(timezone.utc).isoformat(),'server_date':response.headers.get('Date'),'hits':hits,'entries':response.json()['feed']['entry']}

def group_entries(entries, product='GCOV'):
    groups=defaultdict(dict)
    for entry in entries:
        title=entry['title'];parts=title.split('_')
        if product=='GCOV':
            key='_'.join(parts[5:10]);timestamps=re.findall(r'(\d{8}T\d{6})',title)[:1]
        else:
            key='_'.join(parts[5:8]+parts[9:11]);all_times=re.findall(r'(\d{8}T\d{6})',title);timestamps=[all_times[0],all_times[2]] if len(all_times)>=4 else []
        identity=tuple(timestamps)
        if not identity:continue
        previous=groups[key].get(identity)
        # Same provisional collection: retain the lexically latest processing/counter revision.
        if previous is None or title>previous['title']:groups[key][identity]=entry
    return {key:sorted(value.values(),key=lambda e:e['time_start']) for key,value in groups.items()}

def science_url(entry):
    for link in entry['links']:
        url=link.get('href','')
        if url.startswith('https://nisar.asf.earthdatacloud.nasa.gov/NISAR/') and url.endswith('.h5') and '_QA_' not in url:return url
    raise ValueError('No public science HDF5 asset')

def choose_series(groups, min_dates=3, max_dates=10):
    candidates=[(key,entries) for key,entries in groups.items() if len(entries)>=min_dates]
    if not candidates:return None,[]
    candidates.sort(key=lambda item:('DHDH' in item[0] or 'DVDV' in item[0],len(item[1]),item[0]),reverse=True)
    key,entries=candidates[0]
    if len(entries)>max_dates:
        import numpy as np
        entries=[entries[i] for i in np.linspace(0,len(entries)-1,max_dates,dtype=int)]
    return key,entries
