"""Freeze a fresh cohort and admit measured inputs independently of radar agreement."""
import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import requests
from surface_dynamics.catalog import group_entries, search
from surface_dynamics.flood_signatures import reference_groups
from surface_dynamics.io import cube_from_chips, fetch_chip, save_cube
from surface_dynamics.optical_water import discover, select_reference

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/flood_evidence.json"
DEFAULT_OUT = ROOT / "products/flood_evidence_a"
FROZEN_FILES = (
    "scripts/prepare_flood_evidence.py",
    "src/surface_dynamics/catalog.py", "src/surface_dynamics/io.py",
    "src/surface_dynamics/optical_water.py", "src/surface_dynamics/ranges.py",
    "src/surface_dynamics/surface_features.py", "src/surface_dynamics/flood_signatures.py",
    "src/surface_dynamics/analysis.py", "src/surface_dynamics/schemas/flood-policy.json",
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".partial.json")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def freeze(path, record):
    if path.exists() and read_json(path) != record:
        raise ValueError(f"Frozen inputs changed for {path.name}. Use a new output directory.")
    if not path.exists():
        write_json(path, record)


def acquisition(entry):
    return entry["time_start"][:10]


def day_offset(day, amount):
    return (date.fromisoformat(day) + timedelta(days=amount)).isoformat()


def optical_catalog(case, day, phase, recipe, folder):
    """Record same-side optical acquisitions before reading any class values."""
    path = folder / "catalogs" / f"optical_{phase}_{day}.json"
    if not path.exists():
        catalog = discover(*case["point"], day, recipe["optical_offset_days"])
        if phase == "pre":
            start, end = day_offset(day, -recipe["optical_offset_days"]), day_offset(case["onset"], -1)
        else:
            start, end = case["target_start"], case["target_end"]
        catalog["entries"] = [
            entry for entry in catalog["entries"]
            if start <= acquisition(entry) <= min(end, recipe["archive_cutoff"])
        ]
        write_json(path, catalog)
    return read_json(path)


def radar_options(catalog, case, recipe):
    start = day_offset(case["onset"], -recipe["recent_history_days"])
    options = []
    for key, entries in group_entries(catalog["entries"]).items():
        if "DHDH" not in key and "DVDV" not in key:
            continue
        # Retain distinct acquisition days within the same mode, track and frame.
        entries = {acquisition(entry): entry for entry in entries}
        recent = [entry for day, entry in entries.items() if start <= day < case["onset"]]
        targets = [entry for day, entry in entries.items() if case["target_start"] <= day <= case["target_end"]]
        options.append((key, recent[-recipe["max_recent_dates"]:], targets, entries))
    return options


def seasonal_entries(entries, target, recipe):
    # Project to the same calendar day in the previous year, including leap years.
    day = date.fromisoformat(target)
    anniversary = day.replace(year=day.year - 1, day=28 if (day.month, day.day) == (2, 29) else day.day)
    window = recipe["seasonal_window_days"]
    days = sorted(entries, key=lambda day: (abs((date.fromisoformat(day) - anniversary).days), day))
    matched = [day for day in days if abs((date.fromisoformat(day) - anniversary).days) <= window]
    return [entries[day] for day in sorted(matched[:recipe["max_seasonal_dates"]])]


def scout(case, recipe, folder):
    path = folder / "catalogs" / "radar.json"
    if not path.exists():
        # Include the complete prior-season search window for every target date.
        start = day_offset(case["target_start"], -366 - recipe["seasonal_window_days"])
        result = search(*case["point"], start=start + "T00:00:00Z", end=recipe["archive_cutoff"] + "T23:59:59Z",
                        collection=case.get("collection"), max_records=500)
        write_json(path, result)
    catalog = read_json(path)
    options = radar_options(catalog, case, recipe)
    report = {"catalog_hits": catalog["hits"], "status": "no_compatible_target_with_three_recent_dates",
              "radar_series": {key: {"recent": list(map(acquisition, recent)), "target": list(map(acquisition, targets))}
                               for key, recent, targets, _ in options}}
    ranked = sorted(
        (acquisition(target), -len(recent), key, target, recent, entries)
        for key, recent, targets, entries in options if len(recent) >= 3 for target in targets
    )
    if ranked:
        report["status"] = "no_same_side_optical_catalog_pair"
    for target_day, _, key, target, recent, entries in ranked:
        pre = optical_catalog(case, acquisition(recent[-1]), "pre", recipe, folder)
        post = optical_catalog(case, target_day, "post", recipe, folder)
        if pre["entries"] and post["entries"]:
            seasonal = seasonal_entries(entries, target_day, recipe)
            report.update(status="metadata_eligible", series_key=key,
                          recent_dates=list(map(acquisition, recent)), target_date=target_day,
                          prior_season_dates=list(map(acquisition, seasonal)),
                          selected_radar={acquisition(entry): entry["title"] for entry in [*recent, target, *seasonal]})
            break
    write_json(folder / "metadata_screen.json", report)
    return report


def supported(cube):
    return (cube["valid"] & np.isfinite(cube["power"]) & (cube["power"] > 0)).all(axis=(0, 1))


def block_count(mask, size):
    padded = np.pad(mask, ((0, -mask.shape[0] % size), (0, -mask.shape[1] % size)))
    return int(padded.reshape(padded.shape[0] // size, size, padded.shape[1] // size, size).any(axis=(1, 3)).sum())


def grid_parameters(case, recipe, folder, entry):
    # Read the posting once from a small measured chip in the selected series.
    probe = folder / "radar_chips" / (acquisition(entry) + "_grid_probe.npz")
    fetch_chip(entry, *case["point"], probe, pixels=8)
    with np.load(probe, allow_pickle=False) as data:
        native = float(data["x"][1] - data["x"][0])
    pixels = int(round(recipe["chip_width_m"] / native))
    factor = int(round(recipe["posting_m"] / native))
    if factor < 1 or pixels > 1024 or not np.isclose(factor * native, recipe["posting_m"]):
        raise ValueError("Requested posting is incompatible with the native grid")
    return pixels, factor


def read_radar(entry, point, path, pixels, native):
    fetch_chip(entry, *point, path, pixels=pixels)
    with np.load(path, allow_pickle=False) as data:
        if not np.isclose(data["x"][1] - data["x"][0], native):
            raise ValueError("Selected acquisitions have different native grid posting")
    return path


def admit(case, recipe, folder, screen, radar_pool):
    report = {"case_id": case["id"], "region": case["region"], "metadata": screen, "status": screen["status"]}
    if screen["status"] != "metadata_eligible":
        return report

    catalog = read_json(folder / "catalogs" / "radar.json")
    by_title = {entry["title"]: entry for entry in catalog["entries"]}
    recent, target = screen["recent_dates"], screen["target_date"]
    seasonal = screen["prior_season_dates"]
    chosen = screen["selected_radar"]
    pixels, factor = grid_parameters(case, recipe, folder, by_title[chosen[recent[0]]])
    native = recipe["posting_m"] / factor

    def assemble(days):
        futures = [radar_pool.submit(read_radar, by_title[chosen[day]], case["point"],
                                     folder / "radar_chips" / (day + ".npz"), pixels, native) for day in days]
        return cube_from_chips([future.result() for future in futures], factor)

    cube = assemble([*recent, target])
    support = supported(cube)
    report.update(grid_cells=int(support.size), common_radar_cells=int(support.sum()))
    if support.mean() < recipe["min_common_fraction"]:
        report["status"] = "insufficient_common_radar_support"
        return report
    save_cube(cube, folder / "cube.npz")

    report["seasonal"] = {"status": "fewer_than_three_prior_season_acquisitions", "dates": seasonal}
    if len(seasonal) >= 3:
        try:
            prior = assemble([*seasonal, target])
        except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
            report["seasonal"].update(status="seasonal_inputs_unavailable", error_type=type(error).__name__)
        else:
            common = supported(prior) & support
            report["seasonal"].update(status="insufficient_shared_radar_support", shared_cells=int(common.sum()))
            if common.mean() >= recipe["min_common_fraction"]:
                save_cube(prior, folder / "seasonal_cube.npz")
                report["seasonal"]["status"] = "supported"

    arrays, references = {}, {}
    for phase, day in (("pre", recent[-1]), ("post", target)):
        catalog = optical_catalog(case, day, phase, recipe, folder)
        aligned, selection = select_reference(catalog, cube, day, folder / "optical",
                                              recipe["optical_candidates_per_date"])
        references[phase] = selection
        report["reference_epochs"] = references
        if aligned is None:
            report["status"] = "optical_asset_unavailable"
            return report
        observed = selection["source"]["observation_time"][:10]
        same_side = observed < case["onset"] if phase == "pre" else case["target_start"] <= observed <= case["target_end"]
        if not same_side or abs((date.fromisoformat(observed) - date.fromisoformat(day)).days) > recipe["optical_offset_days"]:
            report["status"] = "optical_observation_outside_declared_window"
            return report
        arrays.update({phase + "_" + key: value for key, value in aligned.items()})
    groups = reference_groups(arrays)
    new_water = support & (groups == 1)
    joint = int((support & (groups != 255)).sum())
    counts = {str(code): int((support & (groups == code)).sum()) for code in (0, 1, 2, 3, 4, 255)}
    blocks = block_count(new_water, recipe["spatial_block_pixels"])
    report.update(joint_reference_cells=joint, reference_group_cells=counts, new_water_blocks=blocks)
    np.savez_compressed(folder / "reference.npz", **arrays)
    if joint / support.sum() < recipe["min_joint_optical_fraction"]:
        report["status"] = "insufficient_joint_optical_coverage"
    elif counts["1"] < recipe["min_new_water_cells"] or blocks < recipe["min_new_water_blocks"]:
        report["status"] = "insufficient_reference_new_water"
    elif counts["0"] < recipe["min_stable_nonwater_cells"]:
        report["status"] = "insufficient_reference_stable_nonwater"
    else:
        report["status"] = "admitted"
    report["input_sha256"] = {p.name: digest(p) for p in (folder / name for name in
                              ("cube.npz", "reference.npz", "seasonal_cube.npz")) if p.exists()}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--recipe", type=Path, default=CONFIG)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    recipe = read_json(args.recipe)
    freeze(args.output / "protocol_freeze.json", {"protocol_id": recipe["protocol_id"],
           "recipe_sha256": digest(args.recipe),
           "files": {name: digest(ROOT / name) for name in FROZEN_FILES}})

    def process(case):
        folder = args.output / "cases" / case["id"]
        report_path = folder / "admission.json"
        stage = "catalog"
        try:
            screen_path = folder / "metadata_screen.json"
            screen = read_json(screen_path) if screen_path.exists() else scout(case, recipe, folder)
            if args.metadata_only:
                report = {"case_id": case["id"], **screen}
            elif report_path.exists() and read_json(report_path)["status"] != "stage_error":
                report = read_json(report_path)
            else:
                stage = "measured_admission"
                report = admit(case, recipe, folder, screen, radar_pool)
        except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
            report = {"case_id": case["id"], "status": "stage_error", "stage": stage,
                      "reason": str(error) if not isinstance(error, requests.RequestException) else type(error).__name__}
        if not args.metadata_only:
            write_json(report_path, report)
        metadata = report.get("metadata", report)
        print(case["id"], report["status"], metadata.get("target_date", ""), report.get("reason", ""), flush=True)
        return report

    # h5py serializes file access within one process, including remote reads.
    with ProcessPoolExecutor(max_workers=6) as radar_pool, ThreadPoolExecutor(max_workers=3) as cases:
        results = list(cases.map(process, recipe["cases"]))
    name = "metadata_screen.json" if args.metadata_only else "admission.json"
    write_json(args.output / name, results)


if __name__ == "__main__":
    main()
