"""Tests for core/execution_safety.hard_sl_ack_looks_valid.

These tests verify defensive validation of HARD SL ACK received from the
exchange. The invariant: an ACK structurally invalid must not be accepted
as protection, even when truthy, to honor "no real position without HARD SL".
"""

import unittest

from core.execution_safety import hard_sl_ack_looks_valid, sl_side_for_trade_side


def _valid_ack(symbol="BTC/USDT", sl_side="sell", amount=0.5) -> dict:
    return {
        "id": "stop-123",
        "symbol": symbol,
        "type": "STOP_MARKET",
        "side": sl_side,
        "amount": amount,
        "status": "open",
        "info": {"reduceOnly": True},
    }


class HardSlAckValidationTest(unittest.TestCase):
    def test_valid_ack_passes(self):
        ok, reason = hard_sl_ack_looks_valid(
            _valid_ack(),
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertTrue(ok, msg=reason)
        self.assertEqual(reason, "")

    def test_not_dict_fails(self):
        ok, reason = hard_sl_ack_looks_valid(
            None,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_NOT_DICT")

    def test_missing_id_fails(self):
        ack = _valid_ack()
        ack["id"] = ""
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_MISSING_ID")

    def test_missing_symbol_fails(self):
        ack = _valid_ack()
        del ack["symbol"]
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_MISSING_SYMBOL")

    def test_symbol_mismatch_fails(self):
        ack = _valid_ack(symbol="ETH/USDT")
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("SYMBOL_MISMATCH", reason)

    def test_missing_side_fails(self):
        ack = _valid_ack()
        del ack["side"]
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_MISSING_SIDE")

    def test_side_mismatch_fails(self):
        ack = _valid_ack(sl_side="buy")
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("SIDE_MISMATCH", reason)

    def test_reduce_only_false_fails(self):
        ack = _valid_ack()
        ack["info"]["reduceOnly"] = False
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_NOT_REDUCE_ONLY")

    def test_missing_or_false_string_reduce_only_fails(self):
        for reduce_only in (None, "false"):
            with self.subTest(reduce_only=reduce_only):
                ack = _valid_ack()
                if reduce_only is None:
                    del ack["info"]["reduceOnly"]
                else:
                    ack["info"]["reduceOnly"] = reduce_only
                ok, reason = hard_sl_ack_looks_valid(
                    ack,
                    expected_symbol="BTC/USDT",
                    expected_sl_side="sell",
                    expected_amount=0.5,
                )
                self.assertFalse(ok)
                self.assertEqual(reason, "HARD_SL_ACK_NOT_REDUCE_ONLY")

    def test_terminal_status_fails(self):
        ack = _valid_ack()
        ack["status"] = "rejected"
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("NOT_ACTIVE", reason)

    def test_missing_status_fails(self):
        ack = _valid_ack()
        del ack["status"]
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("NOT_ACTIVE", reason)

    def test_wrong_order_type_fails(self):
        ack = _valid_ack()
        ack["type"] = "limit"
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("INVALID_TYPE", reason)

    def test_amount_deviation_beyond_tolerance_fails(self):
        ack = _valid_ack(amount=0.8)
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertIn("AMOUNT_MISMATCH", reason)

    def test_amount_within_tolerance_passes(self):
        ack = _valid_ack(amount=0.50001)
        ok, _ = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertTrue(ok)

    def test_missing_amount_fails(self):
        ack = _valid_ack()
        del ack["amount"]
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_AMOUNT_UNPARSEABLE")

    def test_small_amount_undercoverage_fails_relative_tolerance(self):
        ack = _valid_ack(amount=0.00091)
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.001,
        )
        self.assertFalse(ok)
        self.assertIn("AMOUNT_MISMATCH", reason)

    def test_unparseable_amount_fails(self):
        ack = _valid_ack()
        ack["amount"] = "not-a-number"
        ok, reason = hard_sl_ack_looks_valid(
            ack,
            expected_symbol="BTC/USDT",
            expected_sl_side="sell",
            expected_amount=0.5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "HARD_SL_ACK_AMOUNT_UNPARSEABLE")

    def test_boolean_amount_fails(self):
        for amount in (True, False):
            with self.subTest(amount=amount):
                ack = _valid_ack()
                ack["amount"] = amount
                ack["filled"] = 1.0
                ok, reason = hard_sl_ack_looks_valid(
                    ack,
                    expected_symbol="BTC/USDT",
                    expected_sl_side="sell",
                    expected_amount=1.0,
                )
                self.assertFalse(ok)
                self.assertEqual(reason, "HARD_SL_ACK_AMOUNT_UNPARSEABLE")


class SlSideMappingTest(unittest.TestCase):
    def test_buy_trade_remaps_to_sell_sl(self):
        self.assertEqual(sl_side_for_trade_side("BUY"), "sell")
        self.assertEqual(sl_side_for_trade_side("buy"), "sell")

    def test_sell_trade_remaps_to_buy_sl(self):
        self.assertEqual(sl_side_for_trade_side("SELL"), "buy")
        self.assertEqual(sl_side_for_trade_side("sell"), "buy")

    def test_non_canonical_input_falls_back_to_buy(self):
        # No utilizado en runtime, pero garantizar determinismo
        self.assertEqual(sl_side_for_trade_side("X"), "buy")


if __name__ == "__main__":
    unittest.main()
