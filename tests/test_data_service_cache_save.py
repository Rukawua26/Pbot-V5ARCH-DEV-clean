import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from core.data_service import DataService


class DataServiceCacheSaveTest(unittest.TestCase):
    def test_async_cache_save_skips_when_save_already_running(self):
        service = DataService(exchange=None)
        service.data_cache = {
            "BTC/USDT_1h": pd.DataFrame(
                {
                    "time": [1, 2, 3],
                    "open": [1.0, 2.0, 3.0],
                    "high": [1.0, 2.0, 3.0],
                    "low": [1.0, 2.0, 3.0],
                    "close": [1.0, 2.0, 3.0],
                }
            )
        }

        running_future = MagicMock()
        running_future.done.return_value = False
        service._cache_save_future = running_future

        with patch.object(service, "_cache_save_executor") as executor:
            scheduled = service.save_cache_async()

        self.assertFalse(scheduled)
        executor.submit.assert_not_called()

    def test_async_cache_save_submits_snapshot_copy(self):
        service = DataService(exchange=None)
        service.data_cache = {
            "BTC/USDT_1h": pd.DataFrame(
                {
                    "time": [1, 2],
                    "open": [1.0, 2.0],
                    "high": [1.0, 2.0],
                    "low": [1.0, 2.0],
                    "close": [1.0, 2.0],
                }
            )
        }

        with patch.object(service, "_cache_save_executor") as executor:
            future = MagicMock()
            future.done.return_value = True
            executor.submit.return_value = future

            scheduled = service.save_cache_async()

        self.assertTrue(scheduled)
        executor.submit.assert_called_once()
        _, snapshot = executor.submit.call_args.args
        self.assertIn("BTC/USDT_1h", snapshot)
        self.assertIsNot(snapshot["BTC/USDT_1h"], service.data_cache["BTC/USDT_1h"])

    def test_async_maturity_save_skips_when_save_already_running(self):
        service = DataService(exchange=None)
        service.maturity_cache = {"BTC/USDT": True}

        running_future = MagicMock()
        running_future.done.return_value = False
        service._maturity_save_future = running_future

        with patch.object(service, "_maturity_save_executor") as executor:
            scheduled = service.save_maturity_cache_async()

        self.assertFalse(scheduled)
        executor.submit.assert_not_called()

    def test_async_maturity_save_respects_debounce(self):
        service = DataService(exchange=None)
        service.maturity_cache = {"BTC/USDT": True}
        service._maturity_last_save_ts = 123.0

        with (
            patch("core.data_service.time.time", return_value=124.0),
            patch.object(service, "_maturity_save_executor") as executor,
        ):
            scheduled = service.save_maturity_cache_async()

        self.assertFalse(scheduled)
        executor.submit.assert_not_called()

    def test_async_maturity_save_submits_snapshot_copy(self):
        service = DataService(exchange=None)
        service.maturity_cache = {"BTC/USDT": True}

        with patch.object(service, "_maturity_save_executor") as executor:
            future = MagicMock()
            future.done.return_value = True
            executor.submit.return_value = future

            scheduled = service.save_maturity_cache_async(force=True)

        self.assertTrue(scheduled)
        executor.submit.assert_called_once()
        _, snapshot = executor.submit.call_args.args
        self.assertEqual(snapshot, {"BTC/USDT": True})
        self.assertIsNot(snapshot, service.maturity_cache)


if __name__ == "__main__":
    unittest.main()
