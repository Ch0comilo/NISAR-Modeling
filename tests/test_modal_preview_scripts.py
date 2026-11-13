"""The preview helpers must pick the right apps and never reach production."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import TestCase

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sweep = load("modal_preview_sweep")
smoke = load("modal_preview_smoke")
PRODUCTION = ("nisar-hazard-watch-ml", "nisar-hazard-watch-ml-dev", "nisar-evidence-api",
              "nisar-more-information-staging", "nisar-hazard-watch-ml-pr-", "nisar-hazard-watch-ml-pr-x")


def app(name, state="deployed"):
    return {"app_id": f"ap-{name}", "description": name, "state": state}


class SweepTests(TestCase):
    def test_only_closed_or_missing_previews_are_stale(self):
        apps = [app("nisar-hazard-watch-ml-pr-3"), app("nisar-hazard-watch-ml-pr-4"),
                app("nisar-hazard-watch-ml-pr-5")]
        stale = sweep.stale_previews(apps, {3: "open", 4: "closed"})
        self.assertEqual(stale, [("nisar-hazard-watch-ml-pr-4", 4), ("nisar-hazard-watch-ml-pr-5", 5)])

    def test_production_dev_and_foreign_apps_are_never_selected(self):
        apps = [app(name) for name in PRODUCTION]
        self.assertEqual(sweep.active_previews(apps), [])
        self.assertEqual(sweep.stale_previews(apps, {}), [])

    def test_stopped_previews_are_left_alone(self):
        apps = [app("nisar-hazard-watch-ml-pr-7", state="stopped")]
        self.assertEqual(sweep.stale_previews(apps, {7: "closed"}), [])

    def test_an_app_without_a_description_is_ignored(self):
        self.assertEqual(sweep.active_previews([{"app_id": "ap-1", "description": None, "state": "deployed"}]), [])


class SmokeTests(TestCase):
    COVERAGE = {"series": [
        {"key": "024_A_018_4000_SH", "observations": [
            {"id": "NISAR_L2_PR_GUNW_one", "dates": ["2026-08-14", "2026-07-21"]}]},
    ]}

    def test_the_first_pair_comes_with_its_days_in_order(self):
        self.assertEqual(smoke.first_pair(self.COVERAGE), ("NISAR_L2_PR_GUNW_one", "2026-07-21", "2026-08-14"))

    def test_observations_without_two_days_are_skipped(self):
        coverage = {"series": [{"key": "x", "observations": [{"id": "a", "dates": ["2026-07-21"]}]}]}
        self.assertIsNone(smoke.first_pair(coverage))
        self.assertIsNone(smoke.first_pair({}))

    def test_the_job_asks_for_one_pair_over_kumamoto(self):
        body = smoke.pair_job_body(("NISAR_L2_PR_GUNW_one", "2026-07-21", "2026-08-14"))
        self.assertEqual(body["operation"], "insar-pair")
        self.assertEqual((body["start_date"], body["end_date"]), ("2026-07-21", "2026-08-14"))
        self.assertEqual((body["longitude"], body["latitude"]), smoke.KUMAMOTO)
