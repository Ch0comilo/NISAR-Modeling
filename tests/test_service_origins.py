"""Check which browser origins the API trusts."""

from unittest import TestCase

from surface_dynamics.service_api import DEFAULT_ORIGINS, configured_origins

PRODUCTION = "https://nisar-hazard-watch-nine.vercel.app"


class ConfiguredOriginsTests(TestCase):
    def test_the_defaults_include_the_production_frontend_and_local_development(self):
        origins = configured_origins()

        self.assertIn(PRODUCTION, origins)
        self.assertIn("http://localhost:5173", origins)

    def test_extra_origins_are_added_without_repeating_the_defaults(self):
        origins = configured_origins(f"https://preview.example.com/, {PRODUCTION},")

        self.assertEqual(origins, [*DEFAULT_ORIGINS, "https://preview.example.com"])

    def test_a_wildcard_or_a_path_is_refused(self):
        for bad in ("*", "https://*.example.com", "https://app.example.com/path", "ftp://example.com"):
            with self.subTest(origin=bad), self.assertRaises(ValueError):
                configured_origins(bad)
