"""Bounded HTTPS range reader for the scratch HDF5 feasibility experiment."""
import io
from collections import OrderedDict
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class RangeReader(io.RawIOBase):
    def __init__(self, url, token, block_size=1024*1024, budget=256*1024*1024):
        if urlparse(url).scheme!='https' or urlparse(url).netloc != 'nisar.asf.earthdatacloud.nasa.gov':
            raise ValueError('Unexpected archive host')
        self.url=url
        self.session=requests.Session()
        retry=Retry(total=3,status_forcelist=[429,502,503,504],allowed_methods=['GET'],
                    backoff_factor=.5,backoff_max=3,raise_on_status=False,
                    respect_retry_after_header=False)
        self.session.mount('https://',HTTPAdapter(max_retries=retry))
        self.session.headers['Authorization']='Bearer '+token
        self.pos=0
        self.block_size=block_size
        self.budget=budget
        self.transferred=0
        self.requests=0
        self.cache=OrderedDict()
        data, total=self._request(0,15)
        if data[:8] != b'\x89HDF\r\n\x1a\n':
            raise ValueError('Not HDF5')
        self.size=total

    def _request(self,start,end):
        with self.session.get(self.url, headers={'Range':f'bytes={start}-{end}'},timeout=(10,45),stream=True) as response:
            if response.status_code != 206:
                raise RuntimeError('Archive did not honor bounded byte-range request: '+str(response.status_code))
            content_range=response.headers.get('Content-Range','')
            if not content_range.startswith(f'bytes {start}-'):
                raise RuntimeError('Incorrect byte-range response')
            if self.transferred+end-start+1 > self.budget:
                raise RuntimeError('Scratch transfer budget exceeded')
            content=response.raw.read(end-start+2)
            if len(content) != end-start+1:
                raise RuntimeError('Unexpected bounded response length')
            self.transferred+=len(content)
            self.requests+=1
            return content,int(content_range.split('/')[-1])

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        if self.pos<0: raise ValueError('Negative seek')
        return self.pos
    def read(self,size=-1):
        if size<0: size=self.size-self.pos
        end=min(self.size,self.pos+size)
        output=[]
        while self.pos<end:
            block=self.pos//self.block_size
            if block not in self.cache:
                start=block*self.block_size
                self.cache[block]=self._request(start,min(self.size,start+self.block_size)-1)[0]
                if len(self.cache)>128: self.cache.popitem(last=False)
            data=self.cache[block]
            offset=self.pos%self.block_size
            take=min(end-self.pos,len(data)-offset)
            output.append(data[offset:offset+take]); self.pos+=take
        return b''.join(output)
    def readinto(self,b):
        data=self.read(len(b)); b[:len(data)]=data; return len(data)
    def close(self):
        if hasattr(self,'session'):self.session.close()
        super().close()
