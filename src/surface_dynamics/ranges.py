"""Bounded HTTPS range reader for the scratch HDF5 feasibility experiment."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Semaphore, local
from time import monotonic
from collections import OrderedDict
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

GCOV_HTTP_SLOTS = Semaphore(10)

class RangeReader(io.RawIOBase):
    def __init__(self, url, token, block_size=1024*1024, budget=256*1024*1024, deadline=None,
                 *, parallel=False, stats=None):
        if urlparse(url).scheme!='https' or urlparse(url).netloc != 'nisar.asf.earthdatacloud.nasa.gov':
            raise ValueError('Unexpected archive host')
        self.url=url
        self.session=requests.Session()
        retry=Retry(total=3,status_forcelist=[429,502,503,504],allowed_methods=['GET'],
                    backoff_factor=.5,backoff_max=3,raise_on_status=False,
                    respect_retry_after_header=False)
        self.session.mount('https://',HTTPAdapter(max_retries=retry if deadline is None else 0))
        self.session.headers['Authorization']='Bearer '+token
        self.pos=0
        self.block_size=block_size
        self.budget=budget
        self.transferred=0
        self.requests=0
        self.deadline=deadline
        self.cache=OrderedDict()
        self._lock = Lock()
        self._reserved = 0
        self._stats = stats if stats is not None else {}
        self._stats.update(http_requests=0, asf_bytes=0, range_batches=0, batch_ranges=0, peak_requests=0)
        self._active = 0
        self._parallel = parallel
        self._threads = local()
        self._sessions = []
        if parallel:
            self.path = url
            self.fs = self
            self._pool = ThreadPoolExecutor(max_workers=4)
        try:
            data, total=self._request(0,15)
            if data[:8] != b'\x89HDF\r\n\x1a\n':
                raise ValueError('Not HDF5')
            self.size=total
        except BaseException:
            self.close()
            raise

    def cat_ranges(self, paths, starts, ends, *, on_error="raise"):
        ranges = list(zip(paths, starts, ends, strict=True))
        if any(path != self.path or not 0 <= start < end <= self.size for path, start, end in ranges):
            raise ValueError("Invalid archive chunk range")
        self._stats["range_batches"] += 1
        self._stats["batch_ranges"] += len(ranges)
        futures = [self._pool.submit(self._request, start, end - 1, bulk=True) for _, start, end in ranges]
        try:
            return [future.result()[0] for future in futures]
        finally:
            for future in futures:
                future.cancel()

    def _session(self, bulk):
        if not bulk:
            return self.session
        if not hasattr(self._threads, "session"):
            session = requests.Session()
            session.headers.update(self.session.headers)
            self._threads.session = session
            with self._lock:
                self._sessions.append(session)
        return self._threads.session

    def _timeout(self):
        if self.deadline is None:
            timeout = (10, 45)
        else:
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise requests.Timeout('Archive chip read deadline exceeded')
            timeout = (min(5, remaining), min(12, remaining))
        return timeout

    def _request(self, start, end, *, bulk=False):
        expected = end - start + 1
        with self._lock:
            if self.transferred + self._reserved + expected > self.budget:
                raise RuntimeError("Scratch transfer budget exceeded")
            self._reserved += expected
        content = b""
        try:
            with GCOV_HTTP_SLOTS:
                timeout = self._timeout()
                with self._lock:
                    self.requests += 1
                    self._active += 1
                    self._stats["http_requests"] = self.requests
                    self._stats["peak_requests"] = max(self._active, self._stats["peak_requests"])
                try:
                    content, total = self._download(start, end, timeout, bulk)
                finally:
                    with self._lock:
                        self._active -= 1
            return content, total
        finally:
            with self._lock:
                self._reserved -= expected
                self.transferred += len(content)
                self._stats["asf_bytes"] = self.transferred

    def _download(self, start, end, timeout, bulk):
        session = self._session(bulk)
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        with session.get(self.url, headers=headers, timeout=timeout, stream=True, allow_redirects=False) as response:
            if response.status_code != 206:
                raise RuntimeError('Archive did not honor bounded byte-range request: '+str(response.status_code))
            content_range=response.headers.get('Content-Range','')
            if not content_range.startswith(f'bytes {start}-{end}/'):
                raise RuntimeError('Incorrect byte-range response')
            content=response.raw.read(end-start+2)
            if len(content) != end-start+1:
                raise RuntimeError('Unexpected bounded response length')
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
        if hasattr(self, "_pool"):
            self._pool.shutdown(wait=True, cancel_futures=True)
        for session in getattr(self, "_sessions", ()):
            session.close()
        if hasattr(self,'session'):self.session.close()
        super().close()
