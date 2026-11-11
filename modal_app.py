"""Modal entry points for the NISAR evidence API."""
from hashlib import sha256
import logging
import os
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import modal


ROOT = Path(__file__).resolve().parent
STORAGE = Path("/nisar-store")
APP_NAME = os.environ.get("NISAR_APP_NAME", "nisar-evidence-api")
VOLUME_NAME = os.environ.get("NISAR_VOLUME", "nisar-evidence-v2")
SECRET_NAME = os.environ.get("NISAR_EARTHDATA_SECRET", "nisar-explorer-preview")
STATE_NAME = os.environ.get("NISAR_STATE_DICT", f"{APP_NAME}-jobs")
DEPS = ("fastapi==0.129.0", "pydantic==2.12.5", "requests==2.34.2")
app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
state = modal.Dict.from_name(STATE_NAME, create_if_missing=True)

def revision():
    digest = sha256()
    for item in [ROOT / "modal_app.py", ROOT / "requirements-lock.txt",
                 *sorted((ROOT / "src" / "surface_dynamics").rglob("*.py")),
                 *sorted((ROOT / "src" / "surface_dynamics" / "schemas").glob("*.json")),
                 *sorted((ROOT / "models" / "global_explorer_v2").glob("*.npz")),
                 ROOT / "models" / "global_explorer_v2" / "masked_temporal_transformer.pt"]:
        digest.update(item.relative_to(ROOT).as_posix().encode())
        digest.update(sha256(item.read_bytes()).digest())
    return digest.hexdigest()[:24]

# The SDK evaluates this module in the container too, where the revision comes from the image.
REVISION = os.environ.get("NISAR_BUILD_REVISION") or revision()
common = modal.Image.debian_slim(python_version="3.13").env(
    {"PYTHONPATH": "/opt/nisar", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MPLBACKEND": "Agg"}
)
runtime_config = {
    "NISAR_BUILD_REVISION": REVISION, "NISAR_APP_NAME": APP_NAME, "NISAR_VOLUME": VOLUME_NAME,
    "NISAR_STATE_DICT": STATE_NAME, "NISAR_EARTHDATA_SECRET": SECRET_NAME,
}
package = ROOT / "src" / "surface_dynamics"
gateway_image = common.uv_pip_install(*DEPS).env(runtime_config).add_local_dir(package, "/opt/nisar/surface_dynamics")
worker_image = (
    common.apt_install("libexpat1", "libgomp1")
    .uv_pip_install(requirements=[str(ROOT / "requirements-lock.txt")],
                    extra_index_url="https://download.pytorch.org/whl/cpu",
                    extra_options="--index-strategy unsafe-best-match")
    .uv_pip_install(*DEPS)
    .env(runtime_config)
    .add_local_dir(package, "/opt/nisar/surface_dynamics")
    .add_local_dir(ROOT / "models" / "global_explorer_v2", "/opt/nisar/models/global_explorer_v2")
)


@app.function(image=worker_image, volumes={str(STORAGE): volume}, cpu=(1.0, 1.0), memory=(2048, 2048),
              timeout=240, max_containers=1, scaledown_window=10, retries=0,
              secrets=[modal.Secret.from_name(SECRET_NAME, required_keys=["EARTHDATA_TOKEN"])])
def worker(body, job_id):
    from surface_dynamics.service_workers import execute_job

    key = "job:" + job_id
    record = state.get(key)
    if not record:
        return
    from time import time
    if record["deadline"] <= time():
        state.put(key, {**record, "status": "failed", "code": "deadline_exceeded"})
        return
    state.put(key, {**record, "status": "running"})
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    os.chdir("/opt/nisar")
    try:
        with TemporaryDirectory() as work:
            out = Path(work) / job_id
            result = execute_job(body, job_id, out, STORAGE / "chips", REVISION)
            dest = STORAGE / "jobs" / job_id
            dest.mkdir(parents=True, exist_ok=True)
            copy2(out / "manifest.json", dest / "manifest.json")
            for artifact in result["artifacts"]:
                copy2(out / artifact["name"], dest / artifact["name"])
            volume.commit()
        state.put(key, {**record, "status": "succeeded", "result": result})
    except Exception as exc:
        logging.getLogger(__name__).warning("job=%s operation=%s failure=%s", job_id,
                                            body["operation"], type(exc).__name__)
        state.put(key, {**record, "status": "failed", "code": "worker_failed"})


@app.function(image=gateway_image, volumes={str(STORAGE): volume}, cpu=(.125, .25), memory=(256, 512),
              timeout=20, max_containers=1, scaledown_window=5)
@modal.concurrent(max_inputs=16)
@modal.asgi_app(label="nisar-evidence-api")
def api():
    from surface_dynamics.service_api import create_app
    from surface_dynamics.service_state import JobStore

    return create_app(STORAGE, JobStore(state, worker.spawn, REVISION), reload=volume.reload)
