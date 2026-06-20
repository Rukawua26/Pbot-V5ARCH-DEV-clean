import unittest

from tools.recovery_drill import run_recovery_drill


class RecoveryDrillTest(unittest.TestCase):
    def test_restart_orphan_drill_adopts_only_after_hard_sl(self):
        report = run_recovery_drill()

        self.assertTrue(report["ok"])
        self.assertTrue(report["within_target"])
        self.assertEqual(report["active_trade_status"], "OPEN")
        self.assertEqual(report["sl_exchange_order_id"], "drill-hard-sl-1")
        self.assertEqual(report["hard_sl_calls"], 1)
        self.assertFalse(report["halt_system_active"])
        self.assertEqual(report["summary"]["failed"], 0)

    def test_recovery_drill_includes_halt_scenarios(self):
        report = run_recovery_drill()
        scenarios = {row["scenario"]: row for row in report["scenarios"]}

        self.assertTrue(scenarios["restart_orphan_hard_sl_failure_halts"]["ok"])
        self.assertTrue(scenarios["restart_orphan_hard_sl_failure_halts"]["halt_system_active"])
        self.assertTrue(scenarios["restart_fetch_positions_ambiguous_halts"]["ok"])
        self.assertTrue(scenarios["restart_fetch_positions_ambiguous_halts"]["halt_system_active"])


if __name__ == "__main__":
    unittest.main()
