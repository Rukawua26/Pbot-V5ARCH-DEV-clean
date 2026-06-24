import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


def _ohlc(rows=40, start=100.0, step=1.0):
    close = [start + i * step for i in range(rows)]
    return pd.DataFrame(
        {
            "open": [c - 0.2 for c in close],
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [100.0] * rows,
            "rsi": [50.0] * rows,
        }
    )


class StrategyAgentsCoverageTest(unittest.TestCase):
    def test_mt_agent_scores_buy_sell_momentum_and_divergence(self):
        from core.strategy.agents.mt_agent import MTAgent

        agent = MTAgent()
        self.assertGreater(agent._get_technical_score({"side": "BUY", "rsi": 30, "adx": 30}), 70)
        self.assertGreater(agent._get_technical_score({"side": "SELL", "rsi": 70, "adx": 30}), 70)
        self.assertLess(agent._get_technical_score({"side": "BUY", "rsi": 75, "adx": 10}), 50)
        self.assertEqual(agent._get_momentum_score(pd.DataFrame({"close": [1, 2]}), "BUY"), 50.0)

        up = _ohlc(step=1.0)
        down = _ohlc(start=140.0, step=-1.0)
        self.assertIn(agent._get_momentum_score(up, "BUY"), {50.0, 66.0, 78.0})
        self.assertIn(agent._get_momentum_score(down, "SELL"), {50.0, 66.0, 78.0})

        bullish_div = _ohlc()
        bullish_div.loc[bullish_div.index[-5], "close"] = 110
        bullish_div.loc[bullish_div.index[-1], "close"] = 100
        bullish_div.loc[bullish_div.index[-5], "rsi"] = 35
        bullish_div.loc[bullish_div.index[-1], "rsi"] = 45
        self.assertEqual(agent._get_divergence_score(bullish_div, "BUY"), 80.0)
        self.assertGreaterEqual(agent.vote({"df": up, "side": "BUY", "rsi": 30, "adx": 30}), 20.0)

    def test_sr_agent_entropy_kinetic_and_vote(self):
        from core.strategy.agents.sr_agent import SRAgent

        agent = SRAgent()
        df = _ohlc(rows=40, start=100, step=0.5)
        self.assertGreaterEqual(agent._calculate_entropy(df["close"]), 0.0)
        self.assertEqual(agent._calculate_entropy([1, 2, 3]), 0.0)

        rejection = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0],
                "high": [10.2, 10.2, 10.2],
                "low": [8.0, 8.0, 8.0],
                "close": [10.1, 10.1, 10.1],
            }
        )
        self.assertEqual(agent._calculate_kinetic_modifier(rejection, -2.0), 1.3)
        self.assertEqual(agent._calculate_kinetic_modifier(None, 2.0), 1.0)
        self.assertEqual(agent.vote({"df": df, "z_score": 0.1}), 50.0)
        self.assertNotEqual(agent.vote({"df": None, "z_score": 3.0}), 50.0)

    def test_breakout_agent_watchlist_evaluate_clean_summary(self):
        from core.strategy.agents.breakout_agent import BreakoutAgent

        agent = BreakoutAgent(
            min_ia_prob=60, volume_multiplier=1.2, breakout_buffer_pct=0.5, timeout_minutes=1
        )
        self.assertFalse(agent.add_to_watchlist("", "BUY", 70, 100, "UP"))
        self.assertFalse(agent.add_to_watchlist("BTC", "BUY", 50, 100, "UP"))
        self.assertTrue(agent.add_to_watchlist("BTC", "BUY", 70, 100, "UP", {"source": "TEST"}))
        self.assertEqual(agent.size(), 1)
        self.assertEqual(agent.summary_by_source(), {"TEST": 1})

        df = pd.DataFrame({"close": [100.0] * 24 + [101.0], "volume": [100.0] * 24 + [200.0]})
        ok, payload = agent.evaluate_breakout("BTC", df)
        self.assertTrue(ok)
        self.assertEqual(payload["symbol"], "BTC")
        self.assertEqual(agent.evaluate_breakout("ETH", df), (False, None))

        with patch("core.strategy.agents.breakout_agent.time.time", return_value=10_000.0):
            agent.watchlist["BTC"]["updated_at"] = 0.0
            self.assertEqual(agent.clean_stale_watchlist(), 1)

    def test_shock_distance_buy_sell_and_invalid_inputs(self):
        from core.strategy.shocks import next_shock_distance_pct

        df = pd.DataFrame(
            {
                "high": [100, 104, 101, 103, 102, 106, 101, 104, 103, 99],
                "low": [90, 92, 91, 88, 93, 94, 89, 93, 92, 95],
                "close": [96, 98, 97, 96, 98, 99, 97, 98, 97, 100],
            }
        )

        buy_dist, buy_level = next_shock_distance_pct(df, "BUY", pivot_window=1, lookback_bars=20)
        sell_dist, sell_level = next_shock_distance_pct(
            df, "SELL", pivot_window=1, lookback_bars=20
        )

        self.assertEqual(buy_level, 103.0)
        self.assertAlmostEqual(buy_dist, 3.0)
        self.assertEqual(sell_level, 92.0)
        self.assertAlmostEqual(sell_dist, 8.0)
        self.assertEqual(next_shock_distance_pct(pd.DataFrame(), "BUY"), (None, None))
        self.assertEqual(
            next_shock_distance_pct(pd.DataFrame({"high": [1], "low": [1], "close": [0]}), "BUY"),
            (None, None),
        )


class GhostAgentCoverageTest(unittest.TestCase):
    class _ProbaModel:
        def __init__(self, prob):
            self.prob = prob

        def predict_proba(self, _x):
            return np.array([[1.0 - self.prob, self.prob]])

    class _PredictModel:
        def __init__(self, value):
            self.value = value

        def predict(self, _x):
            return np.array([self.value])

    def test_select_boost_model_prefers_rf_and_nested_models(self):
        from core.strategy.agents.ghost_agent import GhostAgent

        agent = GhostAgent()
        rf = self._ProbaModel(0.7)
        self.assertIs(agent._select_boost_model({"rf": rf}), rf)
        nested = self._ProbaModel(0.6)
        self.assertIs(agent._select_boost_model({"clf": {"xgb": nested}}), nested)
        self.assertIsNone(agent._select_boost_model({"clf": {"bad": object()}}))

    def test_feature_row_defaults_and_context_values(self):
        from core.strategy.agents.ghost_agent import GhostAgent

        row = GhostAgent()._build_feature_row(
            ["rsi", "adx", "vol_rel", "btc_delta_5m", "funding_rate", "unknown"],
            rsi=55,
            adx=25,
            vol_rel=1.4,
            btc_delta=-0.7,
            atr_pct=0.02,
            funding_rate=0.0003,
        )
        self.assertEqual(row.shape, (1, 6))
        self.assertEqual(row[0, 0], 55.0)
        self.assertEqual(row[0, 3], -0.7)
        self.assertEqual(row[0, 5], 0.0)

    def test_get_ai_boost_clamps_and_applies_heuristics(self):
        from core.strategy.agents.ghost_agent import GhostAgent

        agent = GhostAgent()
        agent.load_trained_model = MagicMock(
            return_value={"rf": self._ProbaModel(0.95), "feature_cols": ["rsi", "adx", "vol_rel"]}
        )
        boost = agent.get_ai_boost(
            rsi=60, adx=30, vol_rel=1.5, btc_delta=0.7, atr_pct=0.02, funding_rate=-0.0003
        )
        self.assertEqual(boost, 20.0)

    def test_get_ai_boost_handles_predict_outputs_and_errors(self):
        from core.strategy.agents.ghost_agent import GhostAgent

        agent = GhostAgent()
        agent.load_trained_model = MagicMock(
            return_value={"model": self._PredictModel(80.0), "feature_cols": ["rsi"]}
        )
        self.assertGreater(agent.get_ai_boost(50, 20, 1.0, 0.0, 0.02), 0.0)

        agent.load_trained_model = MagicMock(return_value={"model": object()})
        self.assertEqual(agent.get_ai_boost(50, 20, 1.0, 0.0, 0.02), 0.0)

    def test_vote_routes_model_types_and_falls_back(self):
        from core.strategy.agents.ghost_agent import GhostAgent

        agent = GhostAgent()
        agent.get_ai_boost = MagicMock(return_value=4.0)
        self.assertEqual(agent.vote({"model": None}), 50.0)
        self.assertEqual(agent.vote({"model": {"version": "v118"}}), 54.0)
        self.assertEqual(agent.vote({"model": {"version": "v111_ultimate"}}), 54.0)

        lstm = SimpleNamespace(input_shape=(None, 10, 1))
        self.assertEqual(agent.vote({"model": lstm, "df": _ohlc(), "scaler": object()}), 54.0)


class ConsensusNNCoverageTest(unittest.TestCase):
    def test_prepare_predict_save_and_train_paths(self):
        from core.strategy.consensus_nn import AgentConsensusNN

        with tempfile.TemporaryDirectory() as tmp:
            model_path = str(Path(tmp) / "model.pkl")
            with patch("core.strategy.consensus_nn.os.path.exists", return_value=False):
                nn = AgentConsensusNN(model_path=model_path)
            features = nn.prepare_features({"MT": 60, "SR": 40})
            self.assertEqual(features.shape, (1, 8))
            self.assertEqual(nn.predict({"MT": 60}), (0.5, 0.0))
            nn.save(n_samples=1)

            nn.is_trained = True
            nn.model = SimpleNamespace(predict_proba=MagicMock(return_value=np.array([[0.2, 0.8]])))
            nn.scaler = SimpleNamespace(transform=MagicMock(return_value=np.zeros((1, 8))))
            prob, conf = nn.predict({"MT": 60})
            self.assertEqual(prob, 0.8)
            self.assertAlmostEqual(conf, 0.6)
            nn.save(n_samples=2)
            self.assertTrue(Path(model_path).exists())

            with patch("core.strategy.consensus_nn.MLPClassifier") as mlp:
                nn.scaler = SimpleNamespace(
                    transform=MagicMock(return_value=np.zeros((1, 8))),
                    fit_transform=MagicMock(return_value=np.ones((4, 8))),
                )
                mlp.return_value = SimpleNamespace(
                    fit=MagicMock(), score=MagicMock(return_value=0.75)
                )
                self.assertTrue(nn.train(np.ones((4, 8)), np.array([0, 1, 0, 1])))

    def test_predict_integrity_guard_and_error_fallback(self):
        from core.strategy.consensus_nn import AgentConsensusNN

        with patch("core.strategy.consensus_nn.os.path.exists", return_value=False):
            nn = AgentConsensusNN(model_path="missing.pkl")

        nn.is_trained = True
        nn.scaler = SimpleNamespace(transform=MagicMock(return_value=np.array([[4.0] * 8])))
        nn.model = SimpleNamespace(predict_proba=MagicMock(return_value=np.array([[0.2, 0.8]])))
        self.assertEqual(nn.predict({"MT": 60}), (0.5, 0.0))

        nn._integrity_log_count = 5
        nn.scaler = SimpleNamespace(transform=MagicMock(side_effect=RuntimeError("bad scale")))
        self.assertEqual(nn.predict({"MT": 60}), (0.5, 0.0))


class StrategyOrchestratorCoverageTest(unittest.TestCase):
    def test_adaptive_weights_performance_and_regime_fallback(self):
        from core.strategy.orchestrator import StrategyOrchestrator

        orch = StrategyOrchestrator()
        weights = orch.get_adaptive_weights(
            "UNKNOWN", agent_performances={"MT": 130, "SR": 40, "G": 100}, adx=30, rsi=70
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreater(weights["MT"], weights["SR"])

    def test_correlation_veto_zeroes_lower_performer_after_window(self):
        from core.strategy.orchestrator import CORRELATION_VETO_WINDOW, StrategyOrchestrator

        orch = StrategyOrchestrator()
        weights = {"MT": 0.4, "SR": 0.3, "G": 0.3}
        for i in range(CORRELATION_VETO_WINDOW - 1):
            vote = 55.0 + float(i % 2) * 10.0
            orch._apply_correlation_veto(weights, {"MT": vote, "SR": vote, "G": 45.0 + i})

        vote = 65.0
        adjusted = orch._apply_correlation_veto(
            weights, {"MT": vote, "SR": vote, "G": 80.0}, {"MT": 55, "SR": 90, "G": 100}
        )
        self.assertEqual(adjusted["MT"], 0.0)

    def test_calculate_consensus_handles_agent_error_nn_and_breakout_penalty(self):
        from core.strategy.orchestrator import StrategyOrchestrator

        orch = StrategyOrchestrator()
        orch.agents = {
            "MT": SimpleNamespace(vote=MagicMock(return_value=80.0)),
            "SR": SimpleNamespace(vote=MagicMock(side_effect=RuntimeError("boom"))),
            "G": SimpleNamespace(vote=MagicMock(return_value=70.0)),
        }
        orch.vote_history = {name: orch.vote_history[name] for name in ["MT", "SR", "G"]}
        orch.consensus_nn = SimpleNamespace(predict=MagicMock(return_value=(0.9, 0.8)))

        with patch("core.strategy.orchestrator.shadow_logger.log") as mock_log:
            score, votes = orch.calculate_consensus(
                {"symbol": "BTC", "regime": "RANGE", "adx": 25, "rsi": 50, "breakout_ready": False}
            )

        self.assertEqual(votes["SR"], 50.0)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 100.0)
        self.assertGreaterEqual(mock_log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
