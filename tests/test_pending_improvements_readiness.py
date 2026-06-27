import unittest

from tools.pending_improvements_readiness import build_report


class PendingImprovementsReadinessTest(unittest.TestCase):
    def test_paper_mode_with_observation_flags_reports_ready(self):
        ok, warnings, blocked = build_report(
            {
                "PAPER_MODE": "true",
                "FVG_TRACKER_ENABLED": "true",
                "GLOBAL_MARKET_PROVIDER_ENABLED": "true",
                "GLOBAL_FEAR_GREED_FILTER_ENABLED": "true",
                "GLOBAL_BTC_DOM_FILTER_ENABLED": "true",
                "SIGNAL_AGENT_OVERRIDE_ENABLED": "true",
                "SNIPER_API_KEY": "1234567890abcdef",
            }
        )

        self.assertFalse(blocked)
        self.assertTrue(any("Modo PAPER" in item for item in ok))
        self.assertFalse(any("Dashboard API" in item for item in warnings))

    def test_real_mode_is_blocked_for_observation(self):
        ok, warnings, blocked = build_report(
            {
                "PAPER_MODE": "false",
                "ALLOW_REAL_TRADING": "true",
                "EXECUTION_BACKEND": "live",
            }
        )

        self.assertFalse(ok)
        self.assertTrue(warnings)
        self.assertTrue(any("REAL" in item for item in blocked))

    def test_defaults_are_safe_but_warn_about_missing_observation_data(self):
        ok, warnings, blocked = build_report({})

        self.assertFalse(blocked)
        self.assertTrue(any("Modo PAPER" in item for item in ok))
        self.assertTrue(any("FVG_TRACKER_ENABLED=false" in item for item in warnings))
        self.assertTrue(any("GLOBAL_MARKET_PROVIDER_ENABLED=false" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
