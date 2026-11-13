# NISAR observation mock

Open `/mock` on the deployed science API in a phone or desktop browser. The science explorer uses that API from the same address. Choose a prepared case to view the published layers without submitting a new job. Richmond includes both HH/HV and VV/VH combinations.

1. Tap **Search catalog** with the default Forest River point and date window.
2. Choose series `099_D_064_4005_DHDH`, baseline through **2026-10-07**, and target **2026-10-19**.
3. Select **Flood / wetland lens** and tap **Submit to Modal**. The result shows the original radar passes and target-minus-baseline change by polarization. Open the worker report for measured values and evidence status. Choose **Physical / spectral / GP / Transformer** to try the model on the same measured history.

The API admits five new jobs per client IP and 12 across the app each UTC day. Catalog searches and cache hits do not count. Devices on the same network may share a public IP. The worker may take a few minutes on fresh archive reads.

## Run from a checkout

Use a computer with Git, Python 3.11 or later, and internet access.

```sh
git clone --branch feat/modal-analysis-api https://github.com/nisar-hazard-watch/NISAR-Modeling.git
cd NISAR-Modeling
python -m pip install requests==2.34.2
python scripts/observation-mock/mock.py
```

If your system uses `python3` or `py -3`, substitute that command for `python`. If pip requires an isolated environment, create one with `python -m venv .venv` and use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows. Open [http://127.0.0.1:8765](http://127.0.0.1:8765) on the computer running the script. The live tab uses the public Modal API with Requests as its only direct dependency. No Modal account or Earthdata token is needed on the device. Use `--port 8766` if 8765 is busy, and open the address the script prints.

For the optional **Measured fixtures** tab, install the science package and restore the measured fixtures from the repository release.

```sh
python -m pip install -e .
python scripts/download_examples.py
python scripts/observation-mock/mock.py
```

Fixture reports and plots are saved locally under `scratch/observation-mock/runs/`.
