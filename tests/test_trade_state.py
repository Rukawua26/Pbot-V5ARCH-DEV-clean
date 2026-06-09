import unittest

from core.trade_state import (
    TradeStatus,
    closing_statuses,
    is_active,
    is_closed,
    open_trade_statuses,
    validate_transition,
)


class TradeStateTest(unittest.TestCase):
    def test_active_and_closed_status_sets_are_disjoint(self):
        active = open_trade_statuses()
        closed = {TradeStatus.CLOSED.value, TradeStatus.HALTED.value}

        self.assertTrue(active)
        self.assertTrue(active.isdisjoint(closed))

    def test_active_statuses_accept_enum_and_string_values(self):
        self.assertTrue(is_active(TradeStatus.OPEN))
        self.assertTrue(is_active("ENTRY_ACK_UNKNOWN"))
        self.assertFalse(is_active("UNKNOWN_STATUS"))

    def test_closed_statuses_accept_enum_and_string_values(self):
        self.assertTrue(is_closed(TradeStatus.CLOSED))
        self.assertTrue(is_closed("HALTED"))
        self.assertFalse(is_closed("OPEN"))

    def test_valid_transitions_allow_expected_order_lifecycle(self):
        self.assertTrue(validate_transition("PENDING_SEND", "ENTRY_SENT"))
        self.assertTrue(validate_transition("ENTRY_SENT", "ENTRY_FILLED_AWAITING_POSITION_SYNC"))
        self.assertTrue(validate_transition("ENTRY_FILLED_AWAITING_POSITION_SYNC", "OPEN"))
        self.assertTrue(validate_transition("OPEN", "CLOSING_INITIATED"))
        self.assertTrue(validate_transition("CLOSING_INITIATED", "CLOSED"))

    def test_valid_transitions_reject_closed_state_reactivation(self):
        self.assertFalse(validate_transition("CLOSED", "OPEN"))
        self.assertFalse(validate_transition("HALTED", "OPEN"))

    def test_unknown_status_transitions_remain_legacy_tolerant(self):
        self.assertTrue(validate_transition("LEGACY_UNKNOWN", "OPEN"))
        self.assertTrue(validate_transition("OPEN", "LEGACY_UNKNOWN"))

    def test_closing_statuses_cover_partial_fill_close_path(self):
        self.assertEqual(
            closing_statuses(),
            frozenset(
                {
                    TradeStatus.CLOSING_INITIATED.value,
                    TradeStatus.PARTIAL_FILL.value,
                    TradeStatus.PARTIAL_FILL_PENDING.value,
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
