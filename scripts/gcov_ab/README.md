# GCOV reader and posting benchmark

The Modal preview uses the exploration worker, the existing `/v2/jobs` request and response, and the existing FastAPI and Explorer routes. The local harness pins only Chocó and Qadirabad. It resolves every requested layer through the regular FastAPI adapter and downloads the measured files from the Modal preview. The two JSON inputs record the five exact ASF granule IDs and archive collections. Chocó uses the supplied 384-pixel request and Qadirabad uses the same five dates as the Data Lab control.

## Run locally against the preview

Start the backend project's local PostgreSQL, then run `../modeling-gcov-ab/scripts/gcov_ab/fastapi_preview.py` with the backend Python environment from the backend checkout. The harness creates and migrates its own `nisar_gcov_ab` database. Start the local frontend on `http://localhost:5173` with `VITE_DEV_API_TARGET=http://127.0.0.1:8025`. This origin is already allowed by the Modal API for the globe textures.

Open [Chocó in Explorer](http://localhost:5173/explore?point=-76.68431,5.66861&lens=flood_wetland) and click **More information** after the measurement loads. The harness defaults to the retained Chocó pyfive/10 m job. A cache replay may start a new job after its result expires.

From this Modeling worktree:

```powershell
$env:NISAR_RUN_TAG = 'round1'
node scripts/gcov_ab/browser.mjs ../gcov-ab-results
$env:NISAR_RUN_TAG = 'r2'
node scripts/gcov_ab/browser.mjs ../gcov-ab-results
python scripts/gcov_ab/report.py ../gcov-ab-results
```

Use a fresh run tag for cold result and chip caches. The second round reverses the first order. `NISAR_MATRIX` accepts a JSON array of `[site, reader, posting_m, native_side, chip_namespace?]` rows. Reuse a previous namespace with a new run tag to measure chip reuse with a fresh result. Use `NISAR_SINGLE` and `NISAR_REPLAY_SELECTOR` together to measure the browser after a complete Modal result cache hit. The recorded worker invocation identifies cold containers. Treat invocation 1 as the first invocation in that container and report queue time separately. The worker has one physical core, 2 GiB, and a 240 s timeout in `us-east`. Its archive stage has a 150 s deadline, five scene tasks, ten process-wide concurrent range requests, four per scene for pyfive and a 128 MiB transfer budget per scene.

The driver measures from clicking a link that opens the live Explorer. It records the first Cesium frame containing the radar texture, the first decoded visible image in More information, and the PNG dimensions and payload lengths. HTTP response timing is recorded separately. Modal result replays or reattachments are identified in the CSV. The driver sends one experiment at a time.

## Numerical checks

The reader shares the same GCOV crop and mask logic for both implementations. The pyfive transport uses exact authenticated ranges through `cat_ranges`; the h5py control uses the same HTTPS transport and block cache. Request totals include redirects. Byte totals are successful archive range payload bytes. The transport records batch calls and the maximum concurrent requests on a single scene.

At 40 m, the worker checks a metric 10 m L-band source grid and averages 4 × 4 blocks in linear gamma-zero. All 16 cells must be focused and have finite positive power in every retained channel. It then converts to dB. Data Lab masks invalid samples with NaN before its xarray coarsen mean, so partially valid blocks can still contribute there. The 384 × 384 and 1024 × 1024 source windows retain their edges at 96 × 96 and 256 × 256 output cells. PNG size is governed separately by the display renderer and has a maximum edge of 256 px.

Download the native chips from the experiment Volume under `/chips/gcov-ab/{reader}/{chip_namespace}` into separate directories. Pass each pair of directories to `parity.py`, with the JSON records for both 40 m runs passed to `--run40`. The script checks EPSG, coordinates, raw masks, and exact raw HH/HV arrays. It then compares the published 40 m COG masks, pixel edges and dB values to an independent linear-power sum of the 16 offsets. It also reports the selected cell and the highest-contrast fully supported block. An optional `--osm` file adds a block crossed by an OpenStreetMap waterway. The Chocó example uses [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) and the geographic bounds `-76.705,5.649,-76.663,5.689`.

```powershell
python scripts/gcov_ab/parity.py --h5py <h5py-chips> --pyfive <pyfive-chips> --run40 <h5py-40.json> <pyfive-40.json> --output <parity.json>
```

Raw rasters and recorded manifests remain outside Git. The public preview has a separate Volume and job Dict. The opt-in selector is ignored by the normal gateway, and GUNW uses the existing reader.
