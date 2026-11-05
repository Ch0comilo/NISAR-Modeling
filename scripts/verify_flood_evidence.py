"""Independently verify selected measurements, counts and geospatial exports."""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
import rasterio

from prepare_flood_evidence import CONFIG, DEFAULT_OUT, ROOT, digest, read_json, write_json


def check_cube(path, screen):
    with np.load(path, allow_pickle=False) as data:
        cube = {key: data[key] for key in data.files}
    dates = cube["dates"].astype(str).tolist()
    assert dates == [*screen["recent_dates"], screen["target_date"]]
    source = json.loads(str(cube["source_metadata_json"]))
    assert [item["title"] for item in source] == [screen["selected_radar"][day] for day in dates]
    labels = cube["polarizations"].astype(str).tolist()
    pair = ["HH", "HV"] if "HH" in labels else ["VV", "VH"]
    indices = [labels.index(pol) for pol in pair]
    power = cube["power"][:, indices].astype(float)
    support = (cube["valid"][:, indices] & np.isfinite(power) & (power > 0)).all(axis=(0, 1))
    samples = 10 * np.log10(power[:, :, support])
    median = np.median(samples[:-1], axis=0)
    mad = 1.4826 * np.median(abs(samples[:-1] - median), axis=0)
    change = samples[-1] - median
    departure = (abs(change) > np.maximum(3, 3 * np.maximum(mad, 1))).any(axis=0)
    dual = (change[0] <= -3) & (change[1] <= -1.5)
    return cube, support, change, departure, dual


def check_reference(path, support):
    with np.load(path, allow_pickle=False) as data:
        pre, post = data["pre_open_class_fraction"], data["post_open_class_fraction"]
        mixed_pre, mixed_post = data["pre_partial_class_fraction"], data["post_partial_class_fraction"]
        valid = ((data["pre_valid_coverage"] >= .8) & (data["post_valid_coverage"] >= .8) &
                 np.isfinite(pre + post + mixed_pre + mixed_post) & support)
        dry_pre, dry_post = (pre <= .2) & (mixed_pre <= .2), (post <= .2) & (mixed_post <= .2)
        water_pre, water_post = pre >= .8, post >= .8
        conditions = [dry_pre & dry_post, dry_pre & water_post, water_pre & water_post, water_pre & dry_post]
        return np.where(valid, np.select(conditions, [0, 1, 2, 3], default=4), 255)


def check_report(folder, result, recipe):
    admission = result["admission"]
    for name, checksum in admission.get("input_sha256", {}).items():
        assert digest(folder / name) == checksum
    assert result["admission_status"] == admission["status"]
    assert result["event_attribution"] is None
    if not admission.get("input_sha256"):
        assert result["comparison"] is None and not result["artifacts"]
        return 0

    cube, support, delta, departure, dual = check_cube(folder / "cube.npz", admission["metadata"])
    groups = check_reference(folder / "reference.npz", support)
    assert int(support.sum()) == admission["common_radar_cells"]
    assert int((groups != 255).sum()) == admission["joint_reference_cells"]
    counts = {str(code): int((support & (groups == code)).sum()) for code in (0, 1, 2, 3, 4, 255)}
    assert counts == admission["reference_group_cells"]
    positive_rows, positive_cols = np.where(groups == 1)
    size = recipe["spatial_block_pixels"]
    blocks = len(set(zip(positive_rows // size, positive_cols // size)))
    assert blocks == admission["new_water_blocks"]
    if (groups != 255).sum() / support.sum() < recipe["min_joint_optical_fraction"]:
        expected = "insufficient_joint_optical_coverage"
    elif len(positive_rows) < recipe["min_new_water_cells"] or blocks < recipe["min_new_water_blocks"]:
        expected = "insufficient_reference_new_water"
    elif counts["0"] < recipe["min_stable_nonwater_cells"]:
        expected = "insufficient_reference_stable_nonwater"
    else:
        expected = "admitted"
    assert expected == admission["status"]
    report_dir = folder / "report"
    for item in result["artifacts"]:
        path = report_dir / item["href"]
        assert path.stat().st_size == item["bytes"] and digest(path) == item["sha256"]
    with np.load(report_dir / "maps.npz", allow_pickle=False) as maps:
        np.testing.assert_array_equal(maps["radar_common_support"], support)
        np.testing.assert_array_equal(maps["reference_groups"], groups)
        np.testing.assert_allclose(maps["target_change_db"][:, support], delta, atol=6e-6)
        for key, flags in (("recent_mad_departure", departure), ("recent_dual_darkening", dual)):
            np.testing.assert_array_equal(maps[key][support], flags)
            if expected != "admitted":
                assert result["comparison"] is None
                continue
            table = result["comparison"]["recent"][key]
            for code, name in enumerate(("stable_nonwater", "new_open_water", "stable_open_water", "open_water_loss")):
                row = table[name]
                selected = groups[support] == code
                eligible, flagged = int(selected.sum()), int((flags & selected).sum())
                assert (row["eligible_cells"], row["flagged_cells"]) == (eligible, flagged)
                assert row["flag_fraction"] == (flagged / eligible if eligible else None)
        if expected == "admitted":
            assert (result["comparison"]["seasonal"] is not None) == (admission["seasonal"]["status"] == "supported")
        for key in ("target_change_db", "reference_groups", "departure_reference_agreement"):
            with rasterio.open(report_dir / f"{key}.tif") as image:
                assert image.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
                assert image.crs.to_epsg() == int(cube["epsg"])
                assert image.xy(0, 0) == (cube["x"][0], cube["y"][0])
                assert image.transform.a == recipe["posting_m"] and image.transform.e == -recipe["posting_m"]
                expected_raster = maps[key].astype("float32")
                if expected_raster.ndim == 2:
                    expected_raster = expected_raster[None]
                np.testing.assert_array_equal(image.read(), expected_raster)
    return len(result["artifacts"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--recipe", type=Path, default=CONFIG)
    args = parser.parse_args()
    recipe = read_json(args.recipe)
    index = read_json(args.output / "index.json")
    protocol = read_json(args.output / "protocol_freeze.json")
    assert protocol["recipe_sha256"] == digest(args.recipe)
    for name, checksum in protocol["files"].items():
        assert digest(ROOT / name) == checksum
    analysis = read_json(args.output / "analysis_freeze.json")
    assert digest(args.output / "protocol_freeze.json") == analysis["protocol"]
    assert digest(args.output / "admission.json") == analysis["admissions"]
    assert digest(ROOT / "scripts/report_flood_evidence.py") == analysis["scorer"]
    assert [row["case_id"] for row in index["cases"]] == [case["id"] for case in recipe["cases"]]
    count, statuses, regions = 0, Counter(), set()
    for row in index["cases"]:
        path = args.output / row["href"]
        assert digest(path) == row["sha256"]
        result = read_json(path)
        assert row["status"] == result["admission_status"]
        assert row["case_id"] == result["case_id"]
        statuses[row["status"]] += 1
        if row["status"] == "admitted":
            regions.add(row["region"])
        count += check_report(path.parent.parent, result, recipe)
    summary = {"status": "share_with_caveats", "case_status_counts": dict(statuses),
               "admitted_regions": sorted(regions), "checked_event_artifacts": count,
               "scope": "Measured-cube formulas, independent optical group counts, admission decisions, flag counts and COGs."}
    write_json(args.output / "validation.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
