import unittest

import pandas as pd

from tools.fidelity_audit import (
    _apply_market_breadth_vetoes,
    _apply_mtf_vetoes,
    _apply_shock_vetoes,
    _apply_side_specific_vetoes,
    _filter_reason_veto_candles,
    _market_breadth_fear_candles,
    _mtf_veto_candles,
    align_runtime_to_proxy,
    apply_runtime_confidence_gate,
    extract_runtime_decisions,
    summarize_fidelity,
)


class FidelityAuditTest(unittest.TestCase):
    def test_extract_runtime_decisions_filters_symbol_and_labels_veto(self):
        events = [
            {
                "ts": "2026-05-11T01:00:10+00:00",
                "event": "FILTER_APPLIED",
                "payload": {
                    "symbol": "BTC/USDT",
                    "side": "BUY",
                    "filter_passed": False,
                    "filter_reason": "MTF_VETO",
                    "prob_final": 31.0,
                },
            },
            {
                "ts": "2026-05-11T01:01:10+00:00",
                "event": "SIGNAL_ANALYZED",
                "payload": {
                    "symbol": "ETH/USDT",
                    "side": "SELL",
                    "mode": "REAL",
                    "prob_final": 81.0,
                },
            },
        ]

        frame = extract_runtime_decisions(events, symbol="BTC/USDT", limit=100)

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["runtime_label"], "NONE")
        self.assertEqual(frame.iloc[0]["runtime_action"], "NONE")

    def test_align_runtime_to_proxy_uses_candle_floor(self):
        runtime = pd.DataFrame(
            [
                {
                    "ts": pd.Timestamp("2026-05-11T01:30:00Z"),
                    "runtime_label": "BUY",
                    "runtime_action": "BUY",
                    "runtime_side": "BUY",
                }
            ]
        )
        proxy = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-05-11T01:00:00Z"),
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                    "score": 70.0,
                    "mt_vote": 70.0,
                    "sr_vote": 50.0,
                    "adx": 25.0,
                    "close": 100.0,
                }
            ]
        )

        aligned = align_runtime_to_proxy(
            runtime,
            proxy,
            timeframe="1h",
            max_time_delta_seconds=3900,
        )

        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned.iloc[0]["proxy_label"], "BUY")

    def test_summarize_fidelity_reports_confusion_and_score(self):
        aligned = pd.DataFrame(
            [
                {
                    "runtime_label": "BUY",
                    "runtime_action": "BUY",
                    "runtime_side": "BUY",
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                },
                {
                    "runtime_label": "NONE",
                    "runtime_action": "NONE",
                    "runtime_side": "NONE",
                    "proxy_label": "SELL",
                    "proxy_action": "SELL",
                },
            ]
        )

        summary = summarize_fidelity(aligned)

        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["label_confusion"]["BUY->BUY"], 1)
        self.assertEqual(summary["label_confusion"]["NONE->SELL"], 1)
        self.assertAlmostEqual(summary["action_agreement_rate"], 0.5)
        self.assertAlmostEqual(summary["side_agreement_rate"], 1.0)
        self.assertAlmostEqual(summary["fidelity_score"], 0.65)

    def test_apply_shock_vetoes_suppresses_proxy_buy_near_resistance(self):
        times = pd.date_range("2026-05-11T00:00:00Z", periods=12, freq="1h")
        candles = pd.DataFrame(
            {
                "time": times,
                "open": [100.0] * 12,
                "high": [
                    100.05,
                    100.08,
                    100.1,
                    100.2,
                    100.1,
                    100.08,
                    100.05,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                ],
                "low": [99.0] * 12,
                "close": [100.0] * 12,
                "volume": [10.0] * 12,
            }
        )
        frame = pd.DataFrame(
            [
                {
                    "time": times[-1],
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                }
            ]
        )

        filtered = _apply_shock_vetoes(frame, candles, min_dist_pct=0.4)

        self.assertEqual(filtered.iloc[0]["proxy_action"], "NONE")
        self.assertIn("SHOCK DEMASIADO CERCA", filtered.iloc[0]["proxy_veto_reason"])

    def test_market_breadth_fear_veto_suppresses_proxy_buy(self):
        events = [
            {
                "ts": "2026-05-11T13:26:51+00:00",
                "event": "FILTER_APPLIED",
                "payload": {
                    "symbol": "BTC/USDT",
                    "side": "BUY",
                    "filter_passed": False,
                    "filter_reason": "MARKET_BREADTH_FEAR: FEAR (100% dump)",
                },
            }
        ]
        fear_candles = _market_breadth_fear_candles(events, timeframe="1h")
        frame = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-05-11T13:00:00Z"),
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                }
            ]
        )

        filtered = _apply_market_breadth_vetoes(frame, fear_candles, timeframe="1h")

        self.assertEqual(filtered.iloc[0]["proxy_action"], "NONE")
        self.assertEqual(filtered.iloc[0]["proxy_veto_reason"], "MARKET_BREADTH_FEAR")

    def test_mtf_veto_suppresses_matching_proxy_side(self):
        events = [
            {
                "ts": "2026-05-11T03:35:48+00:00",
                "event": "MTF_FILTER",
                "payload": {
                    "symbol": "SUI/USDT",
                    "side": "BUY",
                    "weight": 0.0,
                    "reason": "MTF_VETO_15M_SELL_VS_BUY",
                },
            }
        ]
        vetoes = _mtf_veto_candles(events, symbol="SUI/USDT", timeframe="1h")
        frame = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-05-11T03:00:00Z"),
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                    "proxy_veto_reason": None,
                },
                {
                    "time": pd.Timestamp("2026-05-11T03:00:00Z"),
                    "proxy_label": "SELL",
                    "proxy_action": "SELL",
                    "proxy_veto_reason": None,
                },
            ]
        )

        filtered = _apply_mtf_vetoes(frame, vetoes, timeframe="1h")

        self.assertEqual(filtered.iloc[0]["proxy_action"], "NONE")
        self.assertEqual(filtered.iloc[0]["proxy_veto_reason"], "MTF_VETO")
        self.assertTrue(filtered.iloc[0]["proxy_mtf_veto_active"])
        self.assertEqual(filtered.iloc[1]["proxy_action"], "SELL")

    def test_kava_veto_suppresses_matching_proxy_side(self):
        events = [
            {
                "ts": "2026-05-11T14:23:33+00:00",
                "event": "FILTER_APPLIED",
                "payload": {
                    "symbol": "ONDO/USDT",
                    "side": "BUY",
                    "filter_reason": "VETO_KAVA: RIESGO EXCESIVO (66485.29% > 2.50%)",
                },
            }
        ]
        vetoes = _filter_reason_veto_candles(
            events,
            symbol="ONDO/USDT",
            timeframe="1h",
            reason_bucket="VETO_KAVA",
        )
        frame = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-05-11T14:00:00Z"),
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                    "proxy_veto_reason": None,
                }
            ]
        )

        filtered = _apply_side_specific_vetoes(
            frame,
            vetoes,
            timeframe="1h",
            reason="VETO_KAVA",
            active_column="proxy_kava_veto_active",
        )

        self.assertEqual(filtered.iloc[0]["proxy_action"], "NONE")
        self.assertEqual(filtered.iloc[0]["proxy_veto_reason"], "VETO_KAVA")
        self.assertTrue(filtered.iloc[0]["proxy_kava_veto_active"])

    def test_runtime_confidence_gate_suppresses_below_shadow_threshold(self):
        aligned = pd.DataFrame(
            [
                {
                    "proxy_label": "BUY",
                    "proxy_action": "BUY",
                    "proxy_veto_reason": None,
                    "prob_final": 42.0,
                }
            ]
        )

        gated = apply_runtime_confidence_gate(aligned, shadow_min_threshold=55.0)

        self.assertEqual(gated.iloc[0]["proxy_action"], "NONE")
        self.assertEqual(gated.iloc[0]["proxy_veto_reason"], "BELOW_SHADOW_THRESHOLD")


if __name__ == "__main__":
    unittest.main()
