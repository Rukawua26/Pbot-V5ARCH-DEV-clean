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

        agent = BreakoutAgent(min_ia_prob=60, volume_multiplier=1.2, breakout_buffer_pct=0.5, timeout_minutes=1)
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
                mlp.return_value = SimpleNamespace(fit=MagicMock(), score=MagicMock(return_value=0.75))
                self.assertTrue(nn.train(np.ones((4, 8)), np.array([0, 1, 0, 1])))


if __name__ == "__main__":
    unittest.main()
