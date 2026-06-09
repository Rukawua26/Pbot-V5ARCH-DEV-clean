import json
import unittest
from unittest.mock import MagicMock, patch

from core.strategy.agents.sr_agent import SRAgent
from core.strategy.orchestrator import StrategyOrchestrator


class TestGetAgentPerformance(unittest.TestCase):
    """Tests for learning.py::get_agent_performance with primary_ids support."""

    def setUp(self):
        patcher = patch("tools.learning.Brain")  # noqa: F841
        self.addCleanup(patcher.stop)
        patcher2 = patch("tools.learning.shadow_logger")
        self.addCleanup(patcher2.stop)
        patcher2.start()
        self.brain_mock = MagicMock()
        # Prevent _get_conn from actually connecting
        self.brain_mock._get_conn.side_effect = Exception("No DB in test")

    def test_primary_ids_returned(self):
        """When primary_ids=["MT","SR","G"], those keys must be in the result."""
        from tools.learning import Brain

        brain = Brain()
        brain._get_conn = MagicMock(side_effect=Exception("No DB"))
        result = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertIn("MT", result)
        self.assertIn("SR", result)
        self.assertIn("G", result)
        self.assertEqual(len(result), 3)

    def test_legacy_ids_when_no_primary(self):
        """When primary_ids is None, legacy agent IDs are returned."""
        from tools.learning import Brain

        brain = Brain()
        brain._get_conn = MagicMock(side_effect=Exception("No DB"))
        result = brain.get_agent_performance()
        self.assertIn("T", result)
        self.assertIn("V", result)
        self.assertIn("G", result)

    def test_primary_ids_match_orchestrator(self):
        """Verify MT/SR/G are exactly what the orchestrator expects."""
        orch = StrategyOrchestrator()
        expected = set(orch.agents.keys())
        brain = MagicMock()
        brain.get_agent_performance.return_value = {"MT": 100.0, "SR": 100.0, "G": 100.0}
        perf = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertEqual(set(perf.keys()), expected)

    def test_performance_affects_adaptive_weights(self):
        """When MT agent underperforms (<60), its weight factor should drop."""
        orch = StrategyOrchestrator()
        base_weights = orch._base_weights["BULL_TREND"].copy()
        # MT at 50 (below 60) should trigger 0.1x factor
        perf = {"MT": 50.0, "SR": 100.0, "G": 120.0}
        weights = orch.get_adaptive_weights("BULL_TREND", agent_performances=perf)
        # MT weight should be lower than base
        self.assertLess(weights["MT"], base_weights["MT"])

    def test_good_performance_boosts_weight(self):
        """When all agents perform well (>120), high performers get boosted."""
        orch = StrategyOrchestrator()
        weights_high = orch.get_adaptive_weights(
            "BULL_TREND", agent_performances={"MT": 150.0, "SR": 100.0, "G": 100.0}
        )
        weights_low = orch.get_adaptive_weights(
            "BULL_TREND", agent_performances={"MT": 50.0, "SR": 100.0, "G": 100.0}
        )
        # MT should have higher weight in high-perf scenario
        self.assertGreater(weights_high["MT"], weights_low["MT"])

    def test_fallback_to_legacy_ids_from_db(self):
        """If DB has snapshot with legacy IDs and we query with primary_ids,
        the function should fall back to matching legacy keys."""
        from tools.learning import Brain

        brain = Brain()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        snap = json.dumps({"votos": {"T": 80, "J": 60, "G": 90}})
        mock_cursor.fetchall.return_value = [
            {"pnl_percent": 2.0, "market_snapshot": snap},
            {"pnl_percent": -1.5, "market_snapshot": snap},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock()
        brain._get_conn = MagicMock(return_value=mock_conn)
        result = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertIn("MT", result)
        self.assertIn("SR", result)
        self.assertIn("G", result)


class TestSRAgentKineticModifier(unittest.TestCase):
    """Tests for SRAgent._calculate_kinetic_modifier deceleration logic."""

    def setUp(self):
        self.agent = SRAgent()

    def _make_df(self, candles):
        """Helper: build a 30-row DataFrame from last 3 candle specs."""
        import numpy as np
        import pandas as pd

        np.random.seed(42)
        base = pd.DataFrame(
            {
                "open": np.random.uniform(100, 101, 27).tolist(),
                "high": np.random.uniform(101, 102, 27).tolist(),
                "low": np.random.uniform(99, 100, 27).tolist(),
                "close": np.random.uniform(100, 101, 27).tolist(),
                "volume": np.random.uniform(1000, 2000, 27).tolist(),
            }
        )
        for c in candles:
            base = pd.concat([base, pd.DataFrame([c])], ignore_index=True)
        return base

    def test_kinetic_boost_buy(self):
        """Deceleration: small bodies < 40% + long lower wicks >= 50% → boost 1.3x."""
        df = self._make_df(
            [
                {"open": 100.0, "high": 100.6, "low": 99.0, "close": 100.5, "volume": 1500},
                {"open": 99.8, "high": 100.3, "low": 98.8, "close": 100.2, "volume": 1800},
                {"open": 99.5, "high": 100.2, "low": 98.5, "close": 100.0, "volume": 2000},
            ]
        )
        modifier = self.agent._calculate_kinetic_modifier(df, -2.5)
        self.assertAlmostEqual(modifier, 1.3, places=2)

    def test_kinetic_penalty_buy(self):
        """Acceleration: large bodies > 80% + tiny lower wicks < 20% → penalty 0.7x."""
        df = self._make_df(
            [
                {"open": 101.0, "high": 101.2, "low": 99.1, "close": 99.2, "volume": 3000},
                {"open": 100.8, "high": 101.0, "low": 98.9, "close": 99.0, "volume": 3500},
                {"open": 100.6, "high": 100.8, "low": 98.7, "close": 98.8, "volume": 4000},
            ]
        )
        modifier = self.agent._calculate_kinetic_modifier(df, -2.5)
        self.assertAlmostEqual(modifier, 0.7, places=2)

    def test_kinetic_penalty_sell(self):
        """Acceleration in SELL zone: large bodies + tiny upper wicks → penalty 0.7x."""
        df = self._make_df(
            [
                {"open": 98.0, "high": 100.0, "low": 97.9, "close": 99.8, "volume": 3000},
                {"open": 98.2, "high": 100.2, "low": 98.1, "close": 100.0, "volume": 3500},
                {"open": 98.4, "high": 100.4, "low": 98.3, "close": 100.2, "volume": 4000},
            ]
        )
        modifier = self.agent._calculate_kinetic_modifier(df, 2.5)
        self.assertAlmostEqual(modifier, 0.7, places=2)

    def test_kinetic_boost_sell(self):
        """Deceleration in SELL zone: small bodies + long upper wicks → boost 1.3x."""
        df = self._make_df(
            [
                {"open": 100.0, "high": 101.5, "low": 99.8, "close": 100.2, "volume": 1500},
                {"open": 100.2, "high": 101.8, "low": 100.0, "close": 100.3, "volume": 1800},
                {"open": 100.3, "high": 102.0, "low": 100.1, "close": 100.5, "volume": 2000},
            ]
        )
        modifier = self.agent._calculate_kinetic_modifier(df, 2.5)
        self.assertAlmostEqual(modifier, 1.3, places=2)

    def test_kinetic_neutral_buy(self):
        """Mixed candles → no modifier."""
        df = self._make_df(
            [
                {"open": 100.0, "high": 100.8, "low": 99.5, "close": 100.3, "volume": 1500},
                {"open": 100.3, "high": 101.0, "low": 99.8, "close": 100.1, "volume": 1800},
                {"open": 100.1, "high": 100.9, "low": 99.6, "close": 100.4, "volume": 2000},
            ]
        )
        modifier = self.agent._calculate_kinetic_modifier(df, -2.5)
        self.assertAlmostEqual(modifier, 1.0, places=2)

    def test_kinetic_clip_ceiling(self):
        """Score capped at 100 after boost."""
        context = {
            "symbol": "TEST/USDT",
            "df": self._make_df(
                [
                    {"open": 100.0, "high": 100.6, "low": 99.0, "close": 100.5, "volume": 1500},
                    {"open": 99.8, "high": 100.3, "low": 98.8, "close": 100.2, "volume": 1800},
                    {"open": 99.5, "high": 100.2, "low": 98.5, "close": 100.0, "volume": 2000},
                ]
            ),
            "z_score": -2.5,
        }
        # Must not exceed 100
        vote = self.agent.vote(context)
        self.assertLessEqual(vote, 100.0)

    def test_kinetic_short_df_fallback(self):
        """DataFrame with < 3 rows returns modifier 1.0."""
        df = self._make_df([]).head(2)
        modifier = self.agent._calculate_kinetic_modifier(df, -2.5)
        self.assertAlmostEqual(modifier, 1.0, places=2)

    def test_kinetic_no_df_fallback(self):
        """None df returns modifier 1.0."""
        modifier = self.agent._calculate_kinetic_modifier(None, -2.5)
        self.assertAlmostEqual(modifier, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
