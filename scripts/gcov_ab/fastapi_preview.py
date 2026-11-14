"""Local FastAPI harness that freezes scene plans and calls the real Modal preview."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
import uvicorn

from nisar_hazard_watch.adapters.asf.scene_parser import parse_scene_identity
from nisar_hazard_watch.api.app import create_app
from nisar_hazard_watch.config.settings import DEFAULT_DATABASE_URL, Settings
from nisar_hazard_watch.models.scene import Scene, SceneGroup, ScenePlan
from nisar_hazard_watch.utils.geometry import aoi_center


ROOT = Path(__file__).resolve().parent
GATEWAY = "https://ch0comilo--nisar-hazard-watch-ml-gcov-ab.us-east.modal.run"
DB_NAME = "nisar_gcov_ab"
DATABASE = make_url(DEFAULT_DATABASE_URL).set(database=DB_NAME)
CASES = {key: json.loads((ROOT / f"{key}.json").read_text()) for key in ("choco", "qadirabad")}
DEFAULT_SELECTOR = "pyfive:10:ab-choco-pyfive-10-384:ab-choco-pyfive-10-384"
state = {"selector": DEFAULT_SELECTOR, "window": 384, "first": None, "last": None}


def fixed_case(lon, lat):
    return next((case for case in CASES.values()
                 if (case["longitude"], case["latitude"]) == (round(lon, 5), round(lat, 5))), None)


class PinnedPlanner:
    def __init__(self, live):
        self.live = live

    async def plan(self, aoi):
        case = fixed_case(*aoi_center(aoi))
        if case is None:
            return await self.live.plan(aoi=aoi)
        scenes = []
        for spec in case["scenes"]:
            name = spec["granule_id"]
            collection = "NISAR_L2_GCOV_V1" if spec["collection"] == "VALIDATED" else "NISAR_L2_GCOV_PROVISIONAL_V1"
            url = f"https://nisar.asf.earthdatacloud.nasa.gov/NISAR/{collection}/{name}/{name}.h5"
            scene = parse_scene_identity(name, url)
            scenes.append(Scene(**scene.model_dump(), coverage=1.0))
        first = scenes[0]
        return ScenePlan(status="ready", aoi=aoi, scenes=scenes,
                         group=SceneGroup(track=first.track, direction=first.direction, frame=first.frame,
                                          code=first.code, mode=first.mode),
                         maturity_mixed=len({scene.maturity for scene in scenes}) > 1,
                         window_start=datetime.fromisoformat(case["start_date"]).replace(tzinfo=timezone.utc),
                         window_end=datetime.fromisoformat(case["end_date"]).replace(tzinfo=timezone.utc))


class PreviewTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        if request.method == "POST" and request.url.path == "/v2/jobs":
            submitted = json.loads(await request.aread())
            if submitted.get("operation") == "explore" and (case := fixed_case(
                submitted["longitude"], submitted["latitude"]
            )):
                expected = {scene["granule_id"]: scene["collection"] for scene in case["scenes"]}
                assert {scene["granule_id"]: scene["collection"] for scene in submitted["scenes"]} == expected
                # Preserve the pinned comparison geometry and bypass incidental planner rounding.
                body = {**case, "window_pixels": state["window"]}
                request = httpx.Request("POST", request.url, json=body, headers={
                    "X-NISAR-GCOV-Experiment": state["selector"],
                }, extensions=request.extensions)
                response = await super().handle_async_request(request)
                await response.aread()
                state["last"] = {"http_status": response.status_code, "modal": response.json(), "request": body}
                if state["first"] is None:
                    state["first"] = state["last"]
                return response
        return await super().handle_async_request(request)


app = create_app(settings=Settings(
    environment="local", database_url=DATABASE.render_as_string(hide_password=False), ml_gateway_url=GATEWAY,
    rate_limit_per_minute=120, max_active_explorations=32,
    gateway_timeout_seconds=45, exploration_deadline_seconds=300,
    exploration_cache_ttl_seconds=0.001, gcov_max_window_pixels=384, _env_file=None,
), gateway_transport=PreviewTransport())
lifespan = app.router.lifespan_context


@asynccontextmanager
async def pinned_lifespan(application):
    async with lifespan(application):
        service = application.state.exploration_service
        service._planner = PinnedPlanner(service._planner)
        yield


app.router.lifespan_context = pinned_lifespan


class Variant(BaseModel):
    selector: str = Field(pattern=r"^(h5py|pyfive):(10|40):[a-z0-9-]{1,32}:[a-z0-9-]{1,32}$")
    window: int = Field(default=384, ge=384, le=1024, multiple_of=8)


@app.post("/benchmark/variant")
async def set_variant(variant: Variant):
    state.update(selector=variant.selector, window=variant.window, first=None, last=None)
    return state


@app.get("/benchmark/variant")
async def get_variant():
    return state


async def ensure_database():
    engine = create_async_engine(DATABASE.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if not await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": DB_NAME}):
                await conn.exec_driver_sql("CREATE DATABASE nisar_gcov_ab")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(ensure_database())
    os.environ["DATABASE_URL"] = DATABASE.render_as_string(hide_password=False)
    os.environ["ENVIRONMENT"] = "local"
    command.upgrade(Config("alembic.ini"), "head")
    uvicorn.run(app, host="127.0.0.1", port=8025, access_log=False)
