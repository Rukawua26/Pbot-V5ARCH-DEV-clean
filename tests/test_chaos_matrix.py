import unittest

from tools.chaos_matrix import CHAOS_MATRIX, run_chaos_matrix


class ChaosMatrixTests(unittest.TestCase):
    def test_matrix_defines_expected_scenarios(self):
        scenario_ids = {row.scenario_id for row in CHAOS_MATRIX}
        self.assertIn("create_ack_timeout_recovered_by_client_id", scenario_ids)
        self.assertIn("chase_limit_hard_floor_stuck", scenario_ids)
        self.assertIn("concurrent_timeout_restore", scenario_ids)
        self.assertIn("exchange_502_retry_recovers", scenario_ids)
        self.assertIn("rate_limit_close_retries_reduce_only", scenario_ids)

    def test_run_chaos_matrix_passes_all_scenarios(self):
        report = run_chaos_matrix()

        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["passed"], report["summary"]["scenarios"])


if __name__ == "__main__":
    unittest.main()
