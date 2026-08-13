import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_cycles import (
    _consume_late_task_exception,
    _fetch_triage_data_async,
    _resolve_triage_worker_count,
    fetch_triage_data_parallel,
)


class TriageWorkerSizingTest(unittest.TestCase):
    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 16)
    def test_worker_count_never_exceeds_triage_count(self):
        self.assertEqual(_resolve_triage_worker_count(1), 1)
        self.assertEqual(_resolve_triage_worker_count(3), 3)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 16)
    def test_worker_count_respects_configured_cap(self):
        self.assertEqual(_resolve_triage_worker_count(50), 16)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 100)
    def test_worker_count_has_hard_safety_cap(self):
        self.assertEqual(_resolve_triage_worker_count(50), 32)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 0)
    def test_worker_count_handles_invalid_low_cap(self):
        self.assertGreaterEqual(_resolve_triage_worker_count(50), 1)

    def test_worker_count_handles_empty_input(self):
        self.assertEqual(_resolve_triage_worker_count(0), 1)

    @patch("core.bot_cycles.asyncio.run_coroutine_threadsafe")
    @patch("core.bot_cycles._resolve_triage_worker_count", return_value=16)
    @patch("core.bot_cycles.Config.TRIAGE_TIMEOUT_SECONDS", 4)
    def test_async_wait_budget_accounts_for_worker_waves(self, _workers, run_threadsafe):
        completed = MagicMock()
        completed.result.return_value = {}
        run_threadsafe.return_value = completed
        bot = SimpleNamespace(main_loop=SimpleNamespace(is_running=MagicMock(return_value=True)))
        top_triage = [{"symbol": f"S{i}/USDT"} for i in range(30)]

        fetch_triage_data_parallel(bot, top_triage)

        completed.result.assert_called_once_with(timeout=12.0)
        run_threadsafe.call_args.args[0].close()

    @patch("core.bot_cycles.asyncio.run_coroutine_threadsafe")
    @patch("core.bot_cycles._resolve_triage_worker_count", return_value=16)
    @patch("core.bot_cycles.Config.TRIAGE_TIMEOUT_SECONDS", 4)
    def test_outer_timeout_cancels_cycle_and_returns_explicit_errors(
        self, _workers, run_threadsafe
    ):
        pending = MagicMock()
        pending.result.side_effect = TimeoutError
        run_threadsafe.return_value = pending
        bot = SimpleNamespace(
            main_loop=SimpleNamespace(is_running=MagicMock(return_value=True)),
            log=MagicMock(),
        )
        top_triage = [{"symbol": "BTC/USDT"}]

        result = fetch_triage_data_parallel(bot, top_triage)

        self.assertEqual(result["BTC/USDT"]["error"], "OUTER_TIMEOUT")
        pending.cancel.assert_called_once()
        run_threadsafe.call_args.args[0].close()

    def test_late_async_exception_is_consumed(self):
        async def run():
            async def fail():
                raise RuntimeError("late boom")

            task = asyncio.create_task(fail())
            task.add_done_callback(_consume_late_task_exception)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertTrue(task.done())

        asyncio.run(run())

    def test_missing_main_loop_fails_fast_without_spawning_fetches(self):
        bot = SimpleNamespace(
            main_loop=None,
            _fetch_pair_data=MagicMock(),
            log=MagicMock(),
        )
        top_triage = [{"symbol": "BTC/USDT"}]

        result = fetch_triage_data_parallel(bot, top_triage)

        self.assertEqual(result["BTC/USDT"]["error"], "LOOP_UNAVAILABLE")
        bot._fetch_pair_data.assert_not_called()

    @patch("core.bot_cycles.Config.TRIAGE_TIMEOUT_SECONDS", -0.99)
    @patch("core.bot_cycles._resolve_triage_worker_count", return_value=1)
    def test_late_fetch_stays_bounded_and_is_cleaned_up(self, _workers):
        async def run():
            release = asyncio.Event()

            async def delayed_fetch(*_args):
                await release.wait()
                return "BTC/USDT", {"close": [1.0]}, 0.1

            bot = SimpleNamespace(
                _fetch_pair_data=MagicMock(),
                update_radar=MagicMock(),
                log=MagicMock(),
            )
            btc = [{"symbol": "BTC/USDT"}]
            eth = [{"symbol": "ETH/USDT"}]

            with patch("core.bot_cycles.asyncio.to_thread", side_effect=delayed_fetch):
                first = await _fetch_triage_data_async(bot, btc)
                duplicate = await _fetch_triage_data_async(bot, btc)
                capacity = await _fetch_triage_data_async(bot, eth)
                self.assertEqual(first["BTC/USDT"]["error"], "TIMEOUT")
                self.assertEqual(duplicate["BTC/USDT"]["error"], "IN_FLIGHT")
                self.assertEqual(capacity["ETH/USDT"]["error"], "CAPACITY")
                self.assertEqual(list(bot._triage_fetch_tasks), ["BTC/USDT"])

                release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await _fetch_triage_data_async(bot, [])
                self.assertEqual(bot._triage_fetch_tasks, {})

        asyncio.run(run())

    def test_async_fetch_classifies_success_and_failure(self):
        async def run():
            bot = SimpleNamespace(
                _fetch_pair_data=MagicMock(
                    side_effect=[
                        ("BTC/USDT", {"close": [1.0]}, 0.25),
                        RuntimeError("fetch failed"),
                    ]
                ),
                update_radar=MagicMock(),
                log=MagicMock(),
            )

            results = await _fetch_triage_data_async(
                bot,
                [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}],
            )

            self.assertEqual(results["BTC/USDT"]["data"], {"close": [1.0]})
            self.assertEqual(results["BTC/USDT"]["elapsed"], 0.25)
            self.assertEqual(results["ETH/USDT"]["error"], "FETCH_ERROR")
            self.assertEqual(bot._triage_fetch_tasks, {})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
