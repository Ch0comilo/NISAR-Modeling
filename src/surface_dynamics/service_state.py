"""Admission and persistent job identity. The ASGI deployment uses one gateway."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from threading import RLock
from time import time
from uuid import uuid4

from .service_contracts import Job, canonical_job

ACTIVE = {"queued", "running"}
LEASE_SECONDS = int(os.environ.get("NISAR_JOB_LEASE_SECONDS", "360"))
CACHE_SECONDS = 86400


def _plain(item):
    tolist = getattr(item, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"{type(item).__name__} is not JSON serializable")


def plain_json(value):
    """Round-trip a result through JSON so the stored job holds only builtin types.

    The gateway image has no numpy: a Dict value pickled with numpy scalars or arrays cannot be
    read there, and every poll of that job answers 500. Arrays and scalars arrive as `tolist()`.
    """
    return json.loads(json.dumps(value, default=_plain))


class Rejected(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class LocalDict(dict):
    def put(self, key, value, *, skip_if_exists=False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


class JobStore:
    def __init__(self, state, spawn, revision, *, max_active=2, jobs_per_day=12, jobs_per_ip_day=5):
        self.state = state
        self.spawn = spawn
        self.revision = revision
        self.max_active = max_active
        self.jobs_per_day = jobs_per_day
        self.jobs_per_ip_day = jobs_per_ip_day
        self.lock = RLock()

    def submit(self, request, ip_key, *, experiment=None):
        key, payload = canonical_job(request, self.revision)
        if experiment:
            payload.update(longitude=request.longitude, latitude=request.latitude, gcov_experiment=experiment)
            key = sha256(json.dumps([key, payload], sort_keys=True).encode()).hexdigest()
        cache_key = "cache:" + key
        now = time()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self.lock:
            cached = self.state.get(cache_key)
            if cached and cached["until"] > now:
                current = self.state.get("job:" + cached["id"])
                if current and current["status"] != "failed" and not (
                    current["status"] in ACTIVE and current["deadline"] <= now
                ):
                    return self.view(current, "in_flight" if current["status"] in ACTIVE else "hit")
            daily = f"day:{day}"
            if self.jobs_per_day is not None and self.state.get(daily, 0) >= self.jobs_per_day:
                raise Rejected(429, "daily_budget", "New job capacity is exhausted for this UTC day.")
            ip = f"ip:{day}:{ip_key}"
            if self.jobs_per_ip_day is not None and self.state.get(ip, 0) >= self.jobs_per_ip_day:
                raise Rejected(429, "rate_limited", "This client reached its daily new-job limit.")
            active = self.state.get("active", {})
            active = {job: until for job, until in active.items()
                      if until > now and (self.state.get("job:" + job) or {}).get("status") in ACTIVE}
            if len(active) >= self.max_active:
                raise Rejected(503, "queue_full", "Worker capacity is full. Retry shortly.")
            job_id = uuid4().hex
            deadline = now + LEASE_SECONDS
            record = {"id": job_id, "status": "queued", "deadline": deadline}
            self.state.put("job:" + job_id, record)
            self.state.put(cache_key, {"id": job_id, "until": now + CACHE_SECONDS})
            self.state.put("active", {**active, job_id: deadline})
            self.state.put(daily, self.state.get(daily, 0) + 1)
            self.state.put(ip, self.state.get(ip, 0) + 1)
            try:
                self.spawn(payload, job_id)
            except Exception as exc:
                self.state.put("job:" + job_id, {**record, "status": "failed", "code": "submit_failed"})
                raise Rejected(503, "submit_failed", "The worker could not be scheduled.") from exc
            return self.view(record)

    def reserve_delivery(self, size):
        if not size:
            return
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"delivery:{day}"
        with self.lock:
            total = self.state.get(key, 0) + size
            if total > 1024**3:
                raise Rejected(429, "daily_budget", "Artifact delivery capacity is exhausted for this UTC day.")
            self.state.put(key, total)

    def get(self, job_id):
        record = self.state.get("job:" + job_id)
        if not record:
            raise Rejected(404, "job_not_found", "The job is unknown or expired.")
        if record["status"] in ACTIVE and record["deadline"] <= time():
            return self.view({**record, "status": "failed", "code": "deadline_exceeded"})
        return self.view(record)

    @staticmethod
    def view(record, cache="miss"):
        status = record["status"]
        job_id = record["id"]
        error = None
        if status == "failed":
            error = {"code": record.get("code", "worker_failed"), "message": "The job could not produce a result."}
        return Job(job_id=job_id, status=status, status_url=f"/v2/jobs/{job_id}",
                   cache=cache, result=record.get("result"), error=error)
