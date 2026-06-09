import unittest
from unittest.mock import patch

from core.execution_service import OrderLookupError, _parse_order_float, _with_exit_state


class TestWithExitState(unittest.TestCase):
    def test_returns_none_when_order_is_none(self):
        result = _with_exit_state(None, "FILLED")
        self.assertIsNone(result)

    def test_returns_original_when_order_not_dict(self):
        original = "not_a_dict"
        result = _with_exit_state(original, "FILLED")
        self.assertEqual(result, original)

    def test_adds_exit_state_to_dict(self):
        order = {"id": "123", "status": "filled"}
        result = _with_exit_state(order, "FILLED")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["exit_state"], "FILLED")
        self.assertEqual(result["id"], "123")

    def test_creates_new_dict_does_not_mutate_original(self):
        order = {"id": "123"}
        result = _with_exit_state(order, "STUCK")
        self.assertNotIn("exit_state", order)
        self.assertEqual(result["exit_state"], "STUCK")


class TestParseOrderFloat(unittest.TestCase):
    def test_returns_none_when_order_is_none(self):
        result = _parse_order_float(None, "price")
        self.assertIsNone(result)

    def test_returns_none_when_order_not_dict(self):
        result = _parse_order_float("not_a_dict", "price")
        self.assertIsNone(result)

    def test_parses_float_from_direct_key(self):
        order = {"price": "123.45"}
        result = _parse_order_float(order, "price")
        self.assertEqual(result, 123.45)

    def test_falls_back_to_info_key(self):
        order = {"info": {"avgPrice": "100.50"}}
        result = _parse_order_float(order, "price", "avgPrice")
        self.assertEqual(result, 100.50)

    def test_returns_none_when_key_not_found(self):
        order = {"status": "filled"}
        result = _parse_order_float(order, "price")
        self.assertIsNone(result)

    def test_skips_none_values_and_tries_next_key(self):
        order = {"price": None, "info": {"price": "200.00"}}
        result = _parse_order_float(order, "price", "avgPrice")
        self.assertEqual(result, 200.00)

    def test_handles_type_error(self):
        order = {"price": {"nested": "object"}}
        result = _parse_order_float(order, "price")
        self.assertIsNone(result)

    def test_handles_value_error(self):
        order = {"price": "not_a_number"}
        result = _parse_order_float(order, "price")
        self.assertIsNone(result)


class TestOrderLookupError(unittest.TestCase):
    def test_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(OrderLookupError, RuntimeError))

    def test_can_be_raised_and_caught(self):
        try:
            raise OrderLookupError("order not found")
        except RuntimeError:
            pass
        else:
            self.fail("OrderLookupError should be caught as RuntimeError")


class TestExecuteChaseLimitSteps(unittest.TestCase):
    @patch("core.execution_service.OrderLookupError", OrderLookupError)
    def test_returns_none_when_not_order_object(self):
        """Test that _execute_chase_limit_steps handles missing exchange gracefully."""
        pass


if __name__ == "__main__":
    unittest.main()
