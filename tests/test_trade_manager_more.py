import unittest
from unittest.mock import MagicMock


class TestClampLeverage(unittest.TestCase):
    def test_import_function(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        self.assertTrue(callable(_clamp_leverage_1_to_10))

    def test_clamps_zero_to_one(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(0)
        self.assertEqual(result, 1)

    def test_clamps_negative_to_one(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(-5)
        self.assertEqual(result, 1)

    def test_clamps_above_ten_to_ten(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(50)
        self.assertEqual(result, 10)

    def test_leave_valid_leverage_unchanged(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        for lev in [1, 5, 10]:
            self.assertEqual(_clamp_leverage_1_to_10(lev), lev)

    def test_handles_float_input(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(7.5)
        self.assertEqual(result, 7)

    def test_handles_invalid_string(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10("invalid")
        self.assertEqual(result, 10)

    def test_handles_none(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(None)
        self.assertEqual(result, 10)


class TestOrderLooksFilled(unittest.TestCase):
    def test_returns_false_when_not_dict(self):
        from core.trade_manager import _order_looks_filled

        self.assertFalse(_order_looks_filled("not_a_dict"))

    def test_returns_false_when_none(self):
        from core.trade_manager import _order_looks_filled

        self.assertFalse(_order_looks_filled(None))

    def test_returns_true_when_status_closed(self):
        from core.trade_manager import _order_looks_filled

        order = {"status": "closed"}
        self.assertTrue(_order_looks_filled(order))

    def test_returns_true_when_status_filled(self):
        from core.trade_manager import _order_looks_filled

        order = {"status": "filled"}
        self.assertTrue(_order_looks_filled(order))

    def test_checks_info_status(self):
        from core.trade_manager import _order_looks_filled

        order = {"info": {"status": "FILLED"}}
        self.assertTrue(_order_looks_filled(order))


class TestSanitizeContext(unittest.TestCase):
    def test_returns_dict_when_context_is_dict(self):
        from core.trade_manager import _sanitize_context

        bot = MagicMock()
        bot.data_service = None  # Force fallback
        context = {"key": "value"}
        result = _sanitize_context(bot, context)
        self.assertEqual(result, {"key": "value"})

    def test_returns_empty_dict_when_context_not_dict(self):
        from core.trade_manager import _sanitize_context

        bot = MagicMock()
        bot.data_service = None
        result = _sanitize_context(bot, "not_a_dict")
        self.assertEqual(result, {})

    def test_uses_data_service_sanitizer_when_available(self):
        from core.trade_manager import _sanitize_context

        bot = MagicMock()
        bot.data_service.sanitize_context.return_value = {"sanitized": True}
        result = _sanitize_context(bot, {"key": "value"})
        self.assertEqual(result, {"sanitized": True})


class TestModuleAvailable(unittest.TestCase):
    def test_returns_true_for_builtin_module(self):
        from core.trade_manager import _module_available

        self.assertTrue(_module_available("sys"))

    def test_returns_false_for_nonexistent_module(self):
        from core.trade_manager import _module_available

        self.assertFalse(_module_available("this_module_does_not_exist_12345"))


if __name__ == "__main__":
    unittest.main()
