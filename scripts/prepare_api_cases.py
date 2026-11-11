"""Build a versioned precomputed case bundle from the verified release fixtures."""
import argparse
import json
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import numpy as np
from rasterio.warp import transform
import xarray as xr

from surface_dynamics.analysis import measured_db
from surface_dynamics.flood import process_flood
from surface_dynamics.insar_stack import process_stack
from surface_dynamics.io import load_cube
from surface_dynamics.service_artifacts import data_layers, publish_files, write_json
from surface_dynamics.service_contracts import Case
from surface_dynamics.surface_features import from_db


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def native_grid(x, y, epsg):
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    return {"crs": f"EPSG:{int(epsg)}", "bounds": [float(x[0]-dx/2), float(y[-1]-dy/2),
            float(x[-1]+dx/2), float(y[0]+dy/2)], "shape": [len(y), len(x)], "posting_m": [dx, dy]}


def series(cube, grid, point, observed, reference_groups, signature):
    dates = cube["dates"].astype(str).tolist()
    polarizations = list(map(str, cube["polarizations"]))
    db = measured_db(cube)
    before = [dates.index(day) for day in observed["baseline_dates"]]
    after = [dates.index(day) for day in observed["assessment_dates"]]
    channel_ids = [polarizations.index(signature[key]) for key in ("co_polarization", "cross_polarization")
                   if signature[key] in polarizations]
    mask = np.isfinite(db[:, channel_ids]).all(axis=(0, 1))
    ref = {"all_common_cells": mask, "optical_new_open_water": mask & (reference_groups == 1),
           "optical_stable_open_water": mask & (reference_groups == 2),
           "optical_stable_nonwater": mask & (reference_groups == 0)}
    point_x, point_y = transform("EPSG:4326", grid["crs"], [point[0]], [point[1]])
    x = int(np.argmin(abs(cube["x"] - point_x[0])))
    y = int(np.argmin(abs(cube["y"] - point_y[0])))
    observations, observed_groups, timeline = [], [], []
    for i, day in enumerate(dates):
        source = observed["sources"][i] if len(observed["sources"]) == len(dates) else None
        for j, pol in enumerate(polarizations):
            a = db[i, j]
            value = float(a[y, x]) if np.isfinite(a[y, x]) else None
            observations.append({"date": day, "polarization": pol, "gamma0_db": value, "source": source})
            for group, support in ref.items():
                values = a[support & np.isfinite(a)]
                count = int(len(values))
                observed_groups.append({"date": day, "polarization": pol, "group": group,
                                        "cells": count, "median_db": float(np.median(values)) if count else None,
                                        "spatial_iqr_db": np.percentile(values, [25, 75]).tolist() if count else None})
    if len(channel_ids) == 2:
        diagnostics = from_db(db[:, channel_ids], dates, before, after)
        count = diagnostics["quality"]["fixed_common_cells"] if diagnostics["quality"]["diagnostic_available"] else 0
        for index, flags in zip(after, diagnostics["maps"]["assessment_departure_mask"]):
            flagged = int(flags.sum())
            timeline.append({"date": dates[index], "outside_cells": flagged, "eligible_cells": count,
                             "outside_percent": 100 * flagged / count if count else None})
    return {"api_version": "2.0", "point": point, "selected_pixel": [y, x], "observations": observations,
            "observed_groups": observed_groups, "diagnostic_band_timeline": timeline,
            "observation_noise_db": None, "gp_conditional_latent_band_db": None,
            "spatial_iqr_semantics": "Interquartile range of measured cells in each fixed-support group."}


def build(root, output):
    root = root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    config = read(root / "configs/flood_pilot.json")
    for spec in config["cases"]:
        case_id = spec["id"]
        point = [spec["longitude"], spec["latitude"]]
        destination = output / case_id
        destination.mkdir()
        with TemporaryDirectory() as work:
            request = read(root / f"contracts/examples/{case_id}-flood.json")
            for field, key in [("source", "cube_path"), ("reference", "path")]:
                request[field][key] = str(root / request[field][key])
            result = process_flood(request, work)
            arrays = Path(work) / "characterization_arrays.nc"
            copy2(arrays, destination / arrays.name)
            copy2(Path(work) / "group_trajectories.csv", destination / "group_trajectories.csv")
        observed = result["observations"]
        cube = load_cube(request["source"]["cube_path"])
        grid = native_grid(cube["x"], cube["y"], cube["epsg"])
        with xr.open_dataset(destination / arrays.name, engine="h5netcdf") as ds:
            samples = series(cube, grid, point, observed, ds.reference_group.values, result["signature_summary"])
        write_json(destination / "series.json", samples)
        info = read(root / f"products/flood_pilot/cases/{case_id}/admission.json")
        signature = result["signature_summary"]
        evidence = {
            "state": signature["interpretation_status"], "radar_support_cells": signature["radar_supported_pixels"],
            "threshold_cells": signature["darkening_pixels"],
            "threshold_cell_area_ha": signature["darkening_signature_analysis_cell_area_ha"],
            "threshold_policy": result["policy"], "area_semantics": signature["area_semantics"],
            "independent_reference": result["reference_evidence"], "comparison": result["proxy_comparison"],
            "sources": observed["sources"], "causal_attribution": None, "flood_probability": None,
            "flood_depth_m": None, "limitations": result["limitations"],
        }
        cases.append(publish(destination, case_id, spec["name"], point, grid, info["series_key"],
                             observed["baseline_dates"], observed["assessment_dates"], result["status"], evidence))

    destination = output / "kumamoto"
    destination.mkdir()
    request = read(root / "contracts/examples/kumamoto-stack-offline-wide.json")
    request["source"]["pair_paths"] = [str(root / p) for p in request["source"]["pair_paths"]]
    with TemporaryDirectory() as work:
        result = process_stack(request, work)
        name = "interferometric_interval_dynamics.nc"
        for filename in (name, "minimum_coherence.tif", "coherence_interval_contrast.tif", "common_phase_support.tif"):
            copy2(Path(work) / filename, destination / filename)
    with xr.open_dataset(destination / name, engine="h5netcdf") as ds:
        grid = native_grid(ds.x.values, ds.y.values, ds.attrs["epsg_code"])
    pair = result["observations"][1]
    evidence = {"state": result["quality"]["phase_comparison_status"], "pair": pair,
                "los_displacement_cm": None, "common_support": result["quality"],
                "limitations": result["limitations"]}
    write_json(destination / "series.json", {"api_version": "2.0", "intervals": result["observations"],
                                             "observation_noise_db": None, "gp_conditional_latent_band_db": None})
    cases.append(publish(destination, "kumamoto", "Kumamoto, Japan", [130.65, 32.65], grid,
                         "024_A_018_4000_SH", [pair["reference_date"]],
                         [item["secondary_date"] for item in result["observations"][1:]],
                         result["status"], evidence, coherence_index=1))
    write_json(output / "index.json", cases)
    print(json.dumps({"cases": len(cases), "directory": str(output)}))


def publish(destination, case_id, title, point, grid, key, before, after, status, evidence, *, coherence_index=0):
    prefix = f"/v2/cases/{case_id}"
    layers = data_layers(destination, prefix + "/layers", coherence_index=coherence_index)
    manifest = Case(id=case_id, title=title, point=point, grid=grid, series_key=key,
                    baseline_dates=before, assessment_dates=after, status=status, evidence=evidence,
                    layers=layers, series_url=prefix + "/series").model_dump(mode="json")
    write_json(destination / "manifest.json", manifest)
    write_json(destination / "assets.json", publish_files(destination, prefix + "/layers"))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.root, arguments.output)
