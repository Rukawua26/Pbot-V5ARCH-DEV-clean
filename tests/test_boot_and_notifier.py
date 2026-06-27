import io
import time
import unittest
from unittest.mock import MagicMock, patch

import tools.notifier as notifier
from tools.notifier import NotificationQueue, Priority, SatelliteNotifier, send_telegram_photo


class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class BootAndNotifierTest(unittest.TestCase):
    def setUp(self):
        from tools.notifier import get_queue

        q = get_queue()
        q.running = False

    @patch("core.bot_app.acquire_single_instance_lock", return_value=True)
    @patch("core.bot_app.Bot", side_effect=RuntimeError("boot failed"))
    def test_run_entrypoint_exits_non_zero_on_fatal_boot(self, _mocked_bot, _mocked_lock):
        from core.bot_app import run_entrypoint

        with self.assertRaises(SystemExit) as ctx:
            run_entrypoint()

        self.assertEqual(ctx.exception.code, 1)

    @patch("tools.notifier.telegram_post")
    def test_notification_queue_falls_back_without_markdown(self, mocked_post):
        def _post_response(_method, **kwargs):
            payload = kwargs.get("json") or kwargs.get("data") or {}
            if payload.get("parse_mode") == "Markdown":
                return _Response(400, "Bad Request: can't parse entities")
            return _Response(200, "ok")

        mocked_post.side_effect = _post_response
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0)
        queue.running = False

        ok = queue._send_with_retry(
            "sendMessage",
            {"json": {"chat_id": "1", "text": "BTC_[test]", "parse_mode": "Markdown"}},
            1,
        )

        self.assertTrue(ok)
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(
            mocked_post.call_args_list[1].kwargs["json"], {"chat_id": "1", "text": "BTC_[test]"}
        )
        queue.stop()

    @patch("tools.notifier.Config.TELEGRAM_CHAT_ID", "1")
    @patch("tools.notifier.Config.TELEGRAM_TOKEN", "token")
    def test_send_telegram_photo_enqueues_shared_queue(self):
        queue = MagicMock()
        with patch("tools.notifier.get_queue", return_value=queue):
            send_telegram_photo("caption_[x]", io.BytesIO(b"png"))

        queue.send_photo.assert_called_once_with("caption_[x]", b"png", priority=Priority.INFO)

    @patch("tools.notifier.telegram_post")
    def test_notification_queue_photo_falls_back_without_markdown(self, mocked_post):
        def _post_response(_method, **kwargs):
            payload = kwargs.get("json") or kwargs.get("data") or {}
            if payload.get("parse_mode") == "Markdown":
                return _Response(400, "Bad Request: can't parse entities")
            return _Response(200, "ok")

        mocked_post.side_effect = _post_response
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0)
        queue.running = False

        ok = queue._send_with_retry(
            "sendPhoto",
            {
                "data": {"chat_id": "1", "caption": "caption_[x]", "parse_mode": "Markdown"},
                "files": {"photo": ("sniper.png", b"png", "image/png")},
            },
            1,
        )

        self.assertTrue(ok)
        self.assertEqual(mocked_post.call_count, 2)
        self.assertNotIn("parse_mode", mocked_post.call_args_list[1].kwargs["data"])
        queue.stop()

    @patch("tools.notifier.telegram_post", return_value=_Response(200, "ok"))
    def test_notification_queue_rate_limits_messages_and_photos(self, mocked_post):
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0.05)
        queue.running = False

        start = time.time()
        first = queue._send_with_retry(
            "sendMessage",
            {"json": {"chat_id": "1", "text": "hello", "parse_mode": "Markdown"}},
            1,
        )
        second = queue._send_with_retry(
            "sendPhoto",
            {
                "data": {"chat_id": "1", "caption": "cap", "parse_mode": "Markdown"},
                "files": {"photo": ("sniper.png", b"png", "image/png")},
            },
            1,
        )
        elapsed = time.time() - start

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertGreaterEqual(elapsed, 0.05)
        queue.stop()

    @patch("tools.notifier.Config.TELEGRAM_CHAT_ID", "1")
    @patch("tools.notifier.Config.TELEGRAM_TOKEN", "token")
    @patch("tools.notifier.threading.Thread")
    def test_notification_queue_keeps_fifo_inside_priority(self, _mocked_thread):
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0)
        queue.running = False
        queue.send("first", Priority.INFO)
        queue.send("critical", Priority.CRITICAL)
        queue.send("second", Priority.INFO)

        first_item = queue.queue.get_nowait()
        second_item = queue.queue.get_nowait()
        third_item = queue.queue.get_nowait()

        self.assertEqual(first_item[2], "sendMessage")
        self.assertEqual(first_item[3]["json"]["text"], "critical")
        self.assertEqual(second_item[3]["json"]["text"], "first")
        self.assertEqual(third_item[3]["json"]["text"], "second")
        queue.stop()

    @patch("tools.notifier.Config.TELEGRAM_CHAT_ID", "")
    @patch("tools.notifier.Config.TELEGRAM_TOKEN", "")
    @patch("builtins.print")
    @patch("tools.notifier.threading.Thread")
    def test_missing_telegram_config_warns_once(self, _mocked_thread, mocked_print):
        notifier._telegram_config_warning_sent = False
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0)
        queue.running = False

        queue.send("first", Priority.INFO)
        queue.send("second", Priority.INFO)

        self.assertEqual(mocked_print.call_count, 1)
        self.assertIn("Telegram no configurado", mocked_print.call_args.args[0])
        self.assertTrue(queue.queue.empty())
        queue.stop()
        notifier._telegram_config_warning_sent = False

    def test_satellite_notifier_dispatches_callbacks(self):
        received = []
        satellite = SatelliteNotifier()

        satellite.register(lambda event, payload: received.append((event, payload)))
        satellite.notify("fvg.gap_detected", {"symbol": "BTC/USDT"})

        self.assertEqual(received, [("fvg.gap_detected", {"symbol": "BTC/USDT"})])

    def test_satellite_notifier_isolates_callback_failures(self):
        received = []
        satellite = SatelliteNotifier(
            callbacks=[
                lambda _event, _payload: (_ for _ in ()).throw(RuntimeError("boom")),
                lambda event, payload: received.append((event, payload)),
            ]
        )

        satellite.notify("event", {"ok": True})

        self.assertEqual(received, [("event", {"ok": True})])

    def test_shadow_logger_does_not_start_until_used(self):
        from tools.learning import LazyShadowLogger

        logger = LazyShadowLogger(":memory:")

        self.assertFalse(logger.is_started())
        self.assertFalse(logger.is_trading_halted())
        self.assertFalse(logger.is_started())

        logger.log({"type": "TEST", "data": {}})

        self.assertTrue(logger.is_started())
        logger.stop()


if __name__ == "__main__":
    unittest.main()
