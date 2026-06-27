import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from core.analytics.fvg_tracker import (
    FvgStore,
    FvgTracker,
    _compute_gap_id,
    _detect_fvg,
    _make_gap,
    _update_gap_states,
)
from core.config.manager import Config


def _candle(time_val, open_p, high, low, close, volume=1000.0):
    return {
        "time": time_val,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class TestFvgStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "gaps.json")
        self.store = FvgStore(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load(self):
        gaps = [{"id": "abc", "status": "ACTIVE"}]
        self.store.save(gaps)
        loaded = self.store.load()
        self.assertEqual(loaded, gaps)

    def test_load_empty_file_returns_empty_list(self):
        loaded = self.store.load()
        self.assertEqual(loaded, [])

    def test_load_nonexistent_path_returns_empty_list(self):
        path = os.path.join(self.tmpdir, "nope", "gaps.json")
        store = FvgStore(path)
        self.assertEqual(store.load(), [])

    def test_load_corrupt_json_returns_empty_list(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            f.write("{corrupt")
        loaded = self.store.load()
        self.assertEqual(loaded, [])

    def test_atomic_write_does_not_corrupt_on_partial_write(self):
        initial = [{"id": "keep", "value": 42}]
        self.store.save(initial)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write("incomplete")
        loaded = self.store.load()
        self.assertEqual(loaded, initial)

    def test_multiple_saves_preserve_latest(self):
        for i in range(5):
            self.store.save([{"id": str(i), "value": i}])
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "4")


class TestDetectFvg(unittest.TestCase):
    def _df(self, candles: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(candles)

    def test_bullish_fvg_detected(self):
        df = self._df(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )
        gaps = _detect_fvg(df, "BTC/USDT", min_gap_pct=0.1, expires_bars=48)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["type"], "BULLISH_FVG")
        self.assertEqual(gaps[0]["symbol"], "BTC/USDT")
        self.assertEqual(gaps[0]["gap_low"], 100.0)
        self.assertEqual(gaps[0]["gap_high"], 105.0)

    def test_bearish_fvg_detected(self):
        df = self._df(
            [
                _candle(1000, 110, 115, 105, 108),
                _candle(2000, 100, 108, 98, 101),
                _candle(3000, 95, 100, 92, 94),
            ]
        )
        gaps = _detect_fvg(df, "ETH/USDT", min_gap_pct=0.1, expires_bars=48)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["type"], "BEARISH_FVG")
        self.assertEqual(gaps[0]["symbol"], "ETH/USDT")
        self.assertEqual(gaps[0]["gap_low"], 100.0)
        self.assertEqual(gaps[0]["gap_high"], 105.0)

    def test_no_gap_in_normal_trend(self):
        df = self._df(
            [
                _candle(1000, 100, 105, 99, 102),
                _candle(2000, 102, 107, 101, 105),
                _candle(3000, 105, 110, 103, 108),
            ]
        )
        gaps = _detect_fvg(df, "BTC/USDT", min_gap_pct=0.1, expires_bars=48)
        self.assertEqual(len(gaps), 0)

    def test_min_gap_pct_filter(self):
        df = self._df(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )
        gaps = _detect_fvg(df, "BTC/USDT", min_gap_pct=10.0, expires_bars=48)
        self.assertEqual(len(gaps), 0)

    def test_insufficient_candles_returns_empty(self):
        df = self._df(
            [
                _candle(1000, 110, 115, 105, 108),
                _candle(2000, 100, 108, 95, 97),
            ]
        )
        gaps = _detect_fvg(df, "BTC/USDT", min_gap_pct=0.1, expires_bars=48)
        self.assertEqual(len(gaps), 0)

    def test_none_df_returns_empty(self):
        gaps = _detect_fvg(None, "BTC/USDT", min_gap_pct=0.1, expires_bars=48)
        self.assertEqual(len(gaps), 0)


class TestUpdateGapStates(unittest.TestCase):
    def _df(self, candles):
        return pd.DataFrame(candles)

    def test_active_gap_filled_by_price(self):
        now = int(time.time() * 1000)
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, now - 3600000, 48)
        gap["status"] = "ACTIVE"
        df = self._df(
            [
                _candle(now + 1000, 115, 120, 103, 118),
            ]
        )
        updated = _update_gap_states([gap], df, now)
        self.assertEqual(updated[0]["status"], "FILLED")
        self.assertIsNotNone(updated[0]["filled_at"])

    def test_active_gap_partial_fill(self):
        now = int(time.time() * 1000)
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, now - 3600000, 48)
        gap["status"] = "ACTIVE"
        df = self._df(
            [
                _candle(now + 1000, 115, 120, 107, 118),
            ]
        )
        updated = _update_gap_states([gap], df, now)
        self.assertEqual(updated[0]["status"], "PARTIAL_FILL")

    def test_partial_gap_can_be_filled_later(self):
        now = int(time.time() * 1000)
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, now - 3600000, 48)
        gap["status"] = "PARTIAL_FILL"
        df = self._df(
            [
                _candle(now + 1000, 115, 120, 103, 118),
            ]
        )
        updated = _update_gap_states([gap], df, now)
        self.assertEqual(updated[0]["status"], "FILLED")

    def test_formation_candle_does_not_fill_new_gap(self):
        df = self._df(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )
        gaps = _detect_fvg(df, "BTC/USDT", min_gap_pct=0.1, expires_bars=48)
        updated = _update_gap_states(gaps, df, 4000, symbol="BTC/USDT")
        self.assertEqual(updated[0]["status"], "ACTIVE")

    def test_invalidated_gap_keeps_status(self):
        now = int(time.time() * 1000)
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, now - 3600000, 48)
        gap["status"] = "INVALIDATED"
        df = self._df(
            [
                _candle(now, 115, 120, 108, 118),
            ]
        )
        updated = _update_gap_states([gap], df, now)
        self.assertEqual(updated[0]["status"], "INVALIDATED")

    def test_expired_gap_invalidated(self):
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, 0, 48)
        gap["status"] = "ACTIVE"
        now = 48 * 3600 * 1000 + 1
        updated = _update_gap_states([gap], None, now)
        self.assertEqual(updated[0]["status"], "INVALIDATED")

    def test_empty_df_keeps_gaps_unchanged(self):
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, 1000, 48)
        gap["status"] = "ACTIVE"
        df = pd.DataFrame()
        now = 2000
        updated = _update_gap_states([gap], df, now)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["status"], "ACTIVE")


class TestFvgTrackerIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "test_gaps.json")
        self.tracker = FvgTracker(
            enabled=True,
            min_gap_pct=0.1,
            telegram_alerts=False,
            store_path=self.store_path,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_cycle_with_no_symbols(self):
        bot = MagicMock()
        bot.live_prices = {}
        self.tracker.run_cycle(bot)
        self.assertEqual(self.tracker.get_active_gaps(), [])

    def test_run_cycle_with_data(self):
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "100.0"}
        df = pd.DataFrame(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )
        bot.data_service.fetch_and_update_data.return_value = df
        self.tracker.run_cycle(bot)
        gaps = self.tracker.get_active_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["type"], "BULLISH_FVG")

    def test_run_cycle_notifies_new_gap(self):
        events = []
        notifier = MagicMock()
        notifier.notify.side_effect = lambda event, payload: events.append((event, payload))
        tracker = FvgTracker(
            enabled=True,
            min_gap_pct=0.1,
            telegram_alerts=False,
            store_path=self.store_path,
            notifier=notifier,
        )
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "100.0"}
        bot.data_service.fetch_and_update_data.return_value = pd.DataFrame(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )

        tracker.run_cycle(bot)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "fvg.gap_detected")
        self.assertEqual(events[0][1]["symbol"], "BTC/USDT")
        self.assertEqual(events[0][1]["type"], "BULLISH_FVG")

    def test_notifier_failure_does_not_break_cycle(self):
        notifier = MagicMock()
        notifier.notify.side_effect = RuntimeError("boom")
        tracker = FvgTracker(
            enabled=True,
            min_gap_pct=0.1,
            telegram_alerts=False,
            store_path=self.store_path,
            notifier=notifier,
        )
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "100.0"}
        bot.data_service.fetch_and_update_data.return_value = pd.DataFrame(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )

        tracker.run_cycle(bot)

        self.assertEqual(len(tracker.get_active_gaps()), 1)

    def test_persistence_across_cycles(self):
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "100.0"}
        df = pd.DataFrame(
            [
                _candle(1000, 95, 100, 90, 98),
                _candle(2000, 100, 104, 98, 103),
                _candle(3000, 106, 110, 105, 109),
            ]
        )
        bot.data_service.fetch_and_update_data.return_value = df
        self.tracker.run_cycle(bot)
        tracker2 = FvgTracker(
            enabled=True,
            store_path=self.store_path,
        )
        gaps = tracker2.get_active_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["type"], "BULLISH_FVG")

    def test_get_active_gaps_by_symbol(self):
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "100.0", "ETH/USDT": "200.0"}

        def _df_for(symbol, *args, **kwargs):
            if symbol == "BTC/USDT":
                return pd.DataFrame(
                    [
                        _candle(1000, 95, 100, 90, 98),
                        _candle(2000, 100, 104, 98, 103),
                        _candle(3000, 106, 110, 105, 109),
                    ]
                )
            return pd.DataFrame(
                [
                    _candle(1000, 110, 115, 105, 108),
                    _candle(2000, 100, 108, 98, 101),
                    _candle(3000, 95, 100, 92, 94),
                ]
            )

        bot.data_service.fetch_and_update_data.side_effect = _df_for
        self.tracker.run_cycle(bot)
        btc_gaps = self.tracker.get_active_gaps("BTC/USDT")
        eth_gaps = self.tracker.get_active_gaps("ETH/USDT")
        self.assertEqual(len(btc_gaps), 1)
        self.assertEqual(len(eth_gaps), 1)
        self.assertEqual(btc_gaps[0]["symbol"], "BTC/USDT")
        self.assertEqual(eth_gaps[0]["symbol"], "ETH/USDT")


class TestFvgTrackerAlerting(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "alert_test.json")
        self.tracker = FvgTracker(
            enabled=True,
            min_gap_pct=0.1,
            alert_throttle_seconds=3600,
            telegram_alerts=True,
            store_path=self.store_path,
        )
        self.gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, 1000, 48)
        self.gap["status"] = "ACTIVE"
        self.tracker._active_gaps = [self.gap]

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_bot(self, price="105.2"):
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": price}
        bot.price_lock = threading.Lock()
        return bot

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_sent_on_proximity(self, mock_send):
        bot = self._make_bot()
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 1)

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_throttled_within_window(self, mock_send):
        bot = self._make_bot()
        self.tracker._evaluate_and_alert(bot)
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 1)

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_sent_again_after_throttle_expires(self, mock_send):
        self.tracker.alert_throttle_seconds = 0.1
        bot = self._make_bot()
        self.tracker._evaluate_and_alert(bot)
        time.sleep(0.15)
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 2)

    @patch("tools.notifier.send_telegram_msg")
    def test_no_alert_when_telegram_disabled(self, mock_send):
        self.tracker.telegram_alerts = False
        bot = self._make_bot()
        self.tracker.run_cycle(bot)
        mock_send.assert_not_called()

    @patch("tools.notifier.send_telegram_msg")
    def test_no_alert_when_price_far_from_gap(self, mock_send):
        bot = self._make_bot(price="200.0")
        self.tracker._evaluate_and_alert(bot)
        mock_send.assert_not_called()

    @patch("tools.notifier.send_telegram_msg")
    def test_no_alert_for_filled_gap(self, mock_send):
        self.gap["status"] = "FILLED"
        bot = self._make_bot()
        self.tracker._evaluate_and_alert(bot)
        mock_send.assert_not_called()

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_respects_gap_low_as_reference(self, mock_send):
        self.gap["gap_low"] = 100.0
        self.gap["gap_high"] = 105.0
        bot = self._make_bot(price="100.3")
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 1)

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_sent_when_price_inside_gap_zone(self, mock_send):
        self.gap["gap_low"] = 100.0
        self.gap["gap_high"] = 105.0
        bot = self._make_bot(price="103.0")
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 1)

    @patch("tools.notifier.send_telegram_msg")
    def test_alert_uses_nearest_gap_edge(self, mock_send):
        self.gap["gap_low"] = 100.0
        self.gap["gap_high"] = 105.0
        bot = self._make_bot(price="105.3")
        self.tracker._evaluate_and_alert(bot)
        self.assertEqual(mock_send.call_count, 1)

    @patch("tools.notifier.send_telegram_msg")
    def test_evaluate_alert_skips_gaps_without_price(self, mock_send):
        bot = self._make_bot()
        bot.live_prices = {}
        self.tracker._evaluate_and_alert(bot)
        mock_send.assert_not_called()

    @patch("tools.notifier.send_telegram_msg")
    def test_run_cycle_with_alert_integration(self, mock_send):
        now = int(time.time() * 1000)
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 105.0, 104.5, 0.48, now - 10000, 48)
        gap["status"] = "ACTIVE"
        self.tracker._store.save([gap])
        self.tracker._active_gaps = [gap]
        bot = MagicMock()
        bot.live_prices = {"BTC/USDT": "105.2"}
        bot.data_service.fetch_and_update_data.return_value = pd.DataFrame()
        bot.price_lock = threading.Lock()
        self.tracker.run_cycle(bot)
        self.assertEqual(mock_send.call_count, 1)


class TestFvgConfigValidation(unittest.TestCase):
    def _with_config(self, **updates):
        previous = {name: getattr(Config, name) for name in updates}
        for name, value in updates.items():
            setattr(Config, name, value)
        try:
            return Config.validate()
        finally:
            for name, value in previous.items():
                setattr(Config, name, value)

    def test_invalid_scan_interval_is_rejected(self):
        errors = self._with_config(FVG_SCAN_INTERVAL=0)
        self.assertIn("FVG_SCAN_INTERVAL debe ser positivo", errors)

    def test_invalid_max_candles_scan_is_rejected(self):
        errors = self._with_config(FVG_MAX_CANDLES_SCAN=2)
        self.assertIn("FVG_MAX_CANDLES_SCAN debe ser >= 3", errors)

    def test_invalid_max_symbols_per_cycle_is_rejected(self):
        errors = self._with_config(FVG_MAX_SYMBOLS_PER_CYCLE=0)
        self.assertIn("FVG_MAX_SYMBOLS_PER_CYCLE debe ser >= 1", errors)


class TestHelpers(unittest.TestCase):
    def test_compute_gap_id_is_deterministic(self):
        id1 = _compute_gap_id("BTC/USDT", "BULLISH_FVG", 1000)
        id2 = _compute_gap_id("BTC/USDT", "BULLISH_FVG", 1000)
        self.assertEqual(id1, id2)

    def test_compute_gap_id_differs_by_type(self):
        id1 = _compute_gap_id("BTC/USDT", "BULLISH_FVG", 1000)
        id2 = _compute_gap_id("BTC/USDT", "BEARISH_FVG", 1000)
        self.assertNotEqual(id1, id2)

    def test_make_gap_creates_active_gap(self):
        gap = _make_gap("BTC/USDT", "BULLISH_FVG", 110.0, 105.0, 4.76, 1000, 48)
        self.assertEqual(gap["symbol"], "BTC/USDT")
        self.assertEqual(gap["type"], "BULLISH_FVG")
        self.assertEqual(gap["status"], "ACTIVE")
        self.assertIsNotNone(gap["id"])
        self.assertIsNotNone(gap["expires_at"])


if __name__ == "__main__":
    unittest.main()
