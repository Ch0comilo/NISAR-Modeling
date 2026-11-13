from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
from PIL import Image
import rasterio

from surface_dynamics.engine import write_cog
from surface_dynamics.analysis import spatial_structure
from surface_dynamics.render import spatial_spectrum_figure
from surface_dynamics.service_visualizations import Frame, render_gcov_visualizations
from surface_dynamics.service_workers import execute_job, run_explore


DATES = ["2026-06-20", "2026-07-02", "2026-07-14", "2026-07-26"]
X = np.arange(500000, 500040, 10)
Y = np.arange(5300030, 5299990, -10)


def frame(day, **values):
    bands = {name: np.full((4, 4), value, dtype="float32") for name, value in values.items()}
    return Frame(day, bands, X, Y, 32614)


class VisualizationTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_spatial_image_spectrum_uses_posting_and_withholds_a_flat_image(self):
        x = np.arange(32)
        image = np.broadcast_to(np.cos(2 * np.pi * x / 8), (16, 32))
        structure = spatial_structure(image[None, None], 40, 80)
        row, col = np.unravel_index(structure["spatial_fft_power"].argmax(), image.shape)
        self.assertAlmostEqual(abs(structure["spatial_fx_cycles_km"][col]), 3.125)
        self.assertEqual(structure["spatial_fy_cycles_km"][row], 0)
        self.assertEqual(structure["spatial_fft_supported_cells"], image.size)
        figure = self.root / "spatial_spectrum.png"
        self.assertTrue(spatial_spectrum_figure(structure, "HH", figure, "Measured image"))
        self.assertTrue(figure.is_file())
        self.assertFalse(spatial_spectrum_figure(
            spatial_structure(np.zeros((1, 1, 16, 32)), 40), "HH", self.root / "flat.png", "Flat",
        ))

    def test_false_color_preserves_quantitative_bands_and_joint_support(self):
        target = frame(DATES[2], HH=-14, HV=-22, VV=-11, VH=-20)
        target.bands["HV"][1, 1] = np.nan
        earlier = frame(DATES[0], HH=-12, HV=-23, VV=-10, VH=-19)
        views = render_gcov_visualizations(self.root, "/layers", [earlier, target],
                                           target.date, [earlier.date, target.date])
        by_id = {view["id"]: view for view in views}
        hh = by_id["view_polarization_rgb_hh_hv"]
        self.assertEqual((hh["supported_cells"], hh["total_cells"]), (15, 16))
        self.assertEqual(hh["grid"]["posting_m"], [10, 10])
        self.assertEqual(hh["grid"]["crs"], "EPSG:32614")
        with rasterio.open(self.root / Path(hh["cog_url"]).name) as source:
            self.assertEqual(source.tags(ns="IMAGE_STRUCTURE")["LAYOUT"], "COG")
            data = source.read()
            np.testing.assert_array_equal(data[:, 0, 0], [-14, -22, -8])
            self.assertTrue(np.isnan(data[:, 1, 1]).all())
        with Image.open(self.root / Path(hh["png_url"]).name) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertIn(0, np.asarray(image)[:, :, 3])
        vv = by_id["view_cross_co_ratio_vv_vh"]
        self.assertEqual(vv["components"][0]["polarizations"], ["VH", "VV"])
        delta = by_id["view_cross_co_ratio_change_hh_hv"]
        with rasterio.open(self.root / Path(delta["cog_url"]).name) as source:
            self.assertEqual(source.read(1)[0, 0], 3)

    def test_temporal_recipes_exclude_future_and_do_not_cross_pair_polarizations(self):
        frames = [frame(day, HH=value, VH=-23) for day, value in zip(DATES, [-20, -17, -12, 0])]
        views = render_gcov_visualizations(self.root, "/layers", frames, DATES[2])
        self.assertFalse(any("ratio" in view["recipe"] or view["recipe"] == "polarization_rgb" for view in views))
        from surface_dynamics.render import rgb_power

        rgb = rgb_power(np.stack(list(frames[0].bands.values())), ["HH", "VH"])
        np.testing.assert_array_equal(rgb[:, :, 0], rgb[:, :, 1])
        np.testing.assert_array_equal(rgb[:, :, 1], rgb[:, :, 2])
        rgb = next(view for view in views if view["recipe"] == "three_date_rgb")
        self.assertEqual(rgb["dates"], DATES[:3])
        with rasterio.open(self.root / Path(rgb["cog_url"]).name) as source:
            np.testing.assert_array_equal(source.read()[:, 0, 0], [-20, -17, -12])
        frames[0].x = X + 10
        self.assertEqual(render_gcov_visualizations(self.root, "/layers", frames, DATES[2]), [])
        self.assertEqual(render_gcov_visualizations(self.root, "/layers", frames[:2], DATES[2]), [])

    def test_job_publishes_derived_views_and_original_observations(self):
        def measured(body, job_id, out, cache):
            observations = []
            for day, value in zip(DATES[:3], [-20, -17, -12]):
                write_cog(out / (day + ".tif"), np.full((2, 4, 4), value), X, Y, 32614, "dB", ["HH", "HV"])
                observations.append({"date": day, "layer": {"href": f"/v1/jobs/{job_id}/assets/{day}.tif"}})
            return {"status": "complete", "observations": observations,
                    "comparison": {"status": "available", "dates": [DATES[1], DATES[2]]}}

        body = {"operation": "explore", "longitude": -97, "latitude": 48,
                "compare_dates": [DATES[1], DATES[2]]}
        with patch("surface_dynamics.service_workers.process", side_effect=measured):
            result = execute_job(body, "test", self.root / "out", self.root / "cache", "revision")
        urls = {asset["href"] for asset in result["artifacts"]}
        self.assertEqual({view["recipe"] for view in result["visualizations"]},
                         {"polarization_rgb", "cross_co_ratio", "cross_co_ratio_change",
                          "three_date_rgb", "temporal_median", "temporal_mad"})
        for view in result["visualizations"]:
            self.assertTrue({view["png_url"], view["cog_url"]} <= urls)
        self.assertIn(result["science"]["observations"][0]["layer"]["href"], urls)

    def test_optical_followup_finishes_attempt(self):
        def measure(query, entries, cache, out, record):
            record(status="running", phase="optical_evidence", usable_window_date_count=3)
            return "complete", {"status": "evaluated"}, []

        with (patch("surface_dynamics.explore.parse_request", return_value=SimpleNamespace()),
              patch("surface_dynamics.explore.discover", return_value=({}, {})),
              patch("surface_dynamics.explore.select", return_value=("series", [1], None)),
              patch("surface_dynamics.explore.select_alternate", return_value=None),
              patch("surface_dynamics.explore.measure", side_effect=measure),
              patch("surface_dynamics.explore.enrich_lens", return_value={"status": "unavailable"})):
            result = run_explore({}, self.root, self.root)
        self.assertEqual(result["series_attempts"][0]["status"], "complete")
        self.assertIsNone(result["phase"])

    def test_only_registered_scientific_figures_and_animations_are_previewed(self):
        def analysis(body, job_id, out, cache):
            for name in ("physical.png", "observed_evolution.gif", "learned.png"):
                Image.new("RGB", (2, 2)).save(out / name)
            return {"status": "complete", "artifacts": [{"href": "physical.png"},
                    {"href": "observed_evolution.gif", "legend": {"time": "observed dates"}}]}

        with patch("surface_dynamics.service_workers.process", side_effect=analysis):
            result = execute_job({"operation": "analysis", "longitude": 0, "latitude": 0}, "test",
                                 self.root / "out", self.root / "cache", "revision")
        self.assertEqual(len(result["previews"]), 2)
        self.assertTrue(any(asset["media_type"] == "image/gif" for asset in result["artifacts"]))
