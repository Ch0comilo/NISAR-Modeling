"""The preview helpers must pick the right apps and never reach production."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

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
OPEN_PREVIEW = "nisar-hazard-watch-ml-pr-3"
CLOSED_PREVIEW = "nisar-hazard-watch-ml-pr-4"
MISSING_PREVIEW = "nisar-hazard-watch-ml-pr-5"
STOPPED_PREVIEW = "nisar-hazard-watch-ml-pr-7"
COVERAGE = {"series": [
    {"key": "024_A_018_4000_SH", "observations": [
        {"id": "NISAR_L2_PR_GUNW_one", "dates": ["2026-08-14", "2026-07-21"]}]},
]}
PAIR = ("NISAR_L2_PR_GUNW_one", "2026-07-21", "2026-08-14")
EXPECTED_SHA = "bc3641768ee0cda3b80efe645497d9493a0f86b0"
PREVIOUS_SHA = "046f290c509db1ec9048b27a18c00d2e3c3c9dca"
GATEWAY = "https://gateway.example"


def app(name, state="deployed"):
    return {"app_id": f"ap-{name}", "description": name, "state": state}


def health(git_sha, status="ok"):
    return {"status": status, "git_sha": git_sha}


class SweepTests(TestCase):
    def test_only_closed_or_missing_previews_are_stale(self):
        # Arrange
        apps = [app(OPEN_PREVIEW), app(CLOSED_PREVIEW), app(MISSING_PREVIEW)]
        # Act
        stale = sweep.stale_previews(apps, {3: "open", 4: "closed"})
        # Assert
        self.assertEqual(stale, [(CLOSED_PREVIEW, 4), (MISSING_PREVIEW, 5)])

    def test_production_dev_and_foreign_apps_are_never_selected(self):
        # Arrange
        apps = [app(name) for name in PRODUCTION]
        # Act
        active = sweep.active_previews(apps)
        stale = sweep.stale_previews(apps, {})
        # Assert
        self.assertEqual(active, [])
        self.assertEqual(stale, [])

    def test_stopped_previews_are_left_alone(self):
        # Arrange
        apps = [app(STOPPED_PREVIEW, state="stopped")]
        # Act
        stale = sweep.stale_previews(apps, {7: "closed"})
        # Assert
        self.assertEqual(stale, [])

    def test_an_app_without_a_description_is_ignored(self):
        # Arrange
        apps = [{"app_id": "ap-1", "description": None, "state": "deployed"}]
        # Act
        active = sweep.active_previews(apps)
        # Assert
        self.assertEqual(active, [])


class SmokeTests(TestCase):
    def test_the_first_pair_comes_with_its_days_in_order(self):
        # Arrange
        coverage = COVERAGE
        # Act
        pair = smoke.first_pair(coverage)
        # Assert
        self.assertEqual(pair, PAIR)

    def test_observations_without_two_days_are_skipped(self):
        # Arrange
        coverage = {"series": [{"key": "x", "observations": [{"id": "a", "dates": ["2026-07-21"]}]}]}
        # Act
        pair = smoke.first_pair(coverage)
        empty = smoke.first_pair({})
        # Assert
        self.assertIsNone(pair)
        self.assertIsNone(empty)

    def test_the_job_asks_for_one_pair_over_kumamoto(self):
        # Arrange
        pair = PAIR
        # Act
        body = smoke.pair_job_body(pair)
        # Assert
        self.assertEqual(body["operation"], "insar-pair")
        self.assertEqual((body["start_date"], body["end_date"]), ("2026-07-21", "2026-08-14"))
        self.assertEqual((body["longitude"], body["latitude"]), smoke.KUMAMOTO)


class WaitForHealthTests(TestCase):
    def test_a_redeploy_still_answering_with_the_previous_commit_is_waited_out(self):
        # Arrange
        answers = [health(PREVIOUS_SHA), health(PREVIOUS_SHA), health(EXPECTED_SHA)]
        # Act
        with patch.object(smoke, "request", side_effect=answers) as request, patch.object(smoke.time, "sleep"):
            result = smoke.wait_for_health(GATEWAY, EXPECTED_SHA)
        # Assert
        self.assertEqual(result["git_sha"], EXPECTED_SHA)
        self.assertEqual(request.call_count, len(answers))

    def test_a_gateway_that_never_reaches_the_commit_fails_with_what_it_reports(self):
        # Arrange
        answers = [health(PREVIOUS_SHA)] * smoke.HEALTH_ATTEMPTS
        # Act / Assert
        with patch.object(smoke, "request", side_effect=answers), patch.object(smoke.time, "sleep"):
            with self.assertRaises(SystemExit) as raised:
                smoke.wait_for_health(GATEWAY, EXPECTED_SHA)
        self.assertIn(PREVIOUS_SHA, str(raised.exception))

    def test_a_gateway_that_never_answers_ok_fails(self):
        # Arrange
        answers = [health(EXPECTED_SHA, status="starting")] * smoke.HEALTH_ATTEMPTS
        # Act / Assert
        with patch.object(smoke, "request", side_effect=answers), patch.object(smoke.time, "sleep"):
            with self.assertRaises(SystemExit) as raised:
                smoke.wait_for_health(GATEWAY, EXPECTED_SHA)
        self.assertIn("did not answer ok", str(raised.exception))
