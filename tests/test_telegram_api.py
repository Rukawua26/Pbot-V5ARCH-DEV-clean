import unittest
from unittest.mock import MagicMock, patch

from core.telegram_api import (
    sanitize_telegram_error,
    telegram_api_url,
    telegram_get_json,
    telegram_post,
)


class TestTelegramApiUrl(unittest.TestCase):
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "my_token_123")
    def test_builds_url(self):
        url = telegram_api_url("sendMessage")
        self.assertEqual(url, "https://api.telegram.org/botmy_token_123/sendMessage")

    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "")
    def test_empty_token(self):
        url = telegram_api_url("getMe")
        self.assertEqual(url, "https://api.telegram.org/bot/getMe")

    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "token")
    def test_strips_leading_slash(self):
        url = telegram_api_url("/sendMessage")
        self.assertEqual(url, "https://api.telegram.org/bottoken/sendMessage")


class TestSanitizeTelegramError(unittest.TestCase):
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "secret123")
    def test_replaces_token_in_message(self):
        msg = sanitize_telegram_error("Error with token secret123 here")
        self.assertIn("***", msg)
        self.assertNotIn("secret123", msg)

    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "secret123")
    def test_no_token_in_message_stays_same(self):
        msg = sanitize_telegram_error("Some other error")
        self.assertEqual(msg, "Some other error")

    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "")
    def test_empty_token_passthrough(self):
        msg = sanitize_telegram_error("Error message")
        self.assertEqual(msg, "Error message")


class TestTelegramGetJson(unittest.TestCase):
    @patch("core.telegram_api.requests.get")
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "tok")
    def test_success(self, mock_get):
        mock_get.return_value.json.return_value = {"ok": True}
        result = telegram_get_json("getMe")
        self.assertEqual(result, {"ok": True})

    @patch("core.telegram_api.requests.get")
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "tok")
    def test_exception_raises_runtime_error(self, mock_get):
        mock_get.side_effect = ConnectionError("token in error tok")
        with self.assertRaises(RuntimeError):
            telegram_get_json("getMe")


class TestTelegramPost(unittest.TestCase):
    @patch("core.telegram_api.requests.post")
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "tok")
    def test_success_returns_response(self, mock_post):
        resp = MagicMock(status_code=200)
        mock_post.return_value = resp
        result = telegram_post("sendMessage", json={"chat_id": "1"})
        self.assertEqual(result.status_code, 200)

    @patch("core.telegram_api.requests.post")
    @patch("core.telegram_api.Config.TELEGRAM_TOKEN", "tok")
    def test_exception_raises_runtime_error(self, mock_post):
        mock_post.side_effect = ConnectionError("err")
        with self.assertRaises(RuntimeError):
            telegram_post("sendMessage", json={"chat_id": "1"})


if __name__ == "__main__":
    unittest.main()
