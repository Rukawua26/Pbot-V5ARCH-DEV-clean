from __future__ import annotations

from enum import Enum


class TradeStatus(str, Enum):
    PENDING_SEND = "PENDING_SEND"
    PENDING_EXCHANGE_OPEN = "PENDING_EXCHANGE_OPEN"
    ENTRY_SENT = "ENTRY_SENT"
    ENTRY_ACK_UNKNOWN = "ENTRY_ACK_UNKNOWN"
    ENTRY_FILLED_AWAITING_POSITION_SYNC = "ENTRY_FILLED_AWAITING_POSITION_SYNC"
    OPEN = "OPEN"
    PARTIAL_FILL_PENDING = "PARTIAL_FILL_PENDING"
    PARTIAL_FILL = "PARTIAL_FILL"
    CLOSING_INITIATED = "CLOSING_INITIATED"
    CLOSED = "CLOSED"
    EXIT_STUCK = "EXIT_STUCK"
    EXIT_SENT = "EXIT_SENT"
    HALTED = "HALTED"
    LOST_IN_TRANSMISSION = "LOST_IN_TRANSMISSION"
    EMERGENCY_CLOSE_PENDING = "EMERGENCY_CLOSE_PENDING"
    PARTIAL_FILL_CANCEL_FAILED = "PARTIAL_FILL_CANCEL_FAILED"
    ORDER_LOOKUP_FAILED = "ORDER_LOOKUP_FAILED"


_ACTIVE_STATES: frozenset[TradeStatus] = frozenset(
    {
        TradeStatus.PENDING_SEND,
        TradeStatus.PENDING_EXCHANGE_OPEN,
        TradeStatus.ENTRY_SENT,
        TradeStatus.ENTRY_ACK_UNKNOWN,
        TradeStatus.ENTRY_FILLED_AWAITING_POSITION_SYNC,
        TradeStatus.OPEN,
        TradeStatus.PARTIAL_FILL_PENDING,
        TradeStatus.PARTIAL_FILL,
        TradeStatus.CLOSING_INITIATED,
        TradeStatus.EXIT_STUCK,
        TradeStatus.EXIT_SENT,
        TradeStatus.LOST_IN_TRANSMISSION,
        TradeStatus.EMERGENCY_CLOSE_PENDING,
        TradeStatus.ORDER_LOOKUP_FAILED,
    }
)

_CLOSED_STATES: frozenset[TradeStatus] = frozenset(
    {
        TradeStatus.CLOSED,
        TradeStatus.HALTED,
    }
)

_VALID_TRANSITIONS: dict[TradeStatus, frozenset[TradeStatus]] = {
    TradeStatus.PENDING_SEND: frozenset(
        {
            TradeStatus.ENTRY_SENT,
            TradeStatus.ENTRY_ACK_UNKNOWN,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.PENDING_EXCHANGE_OPEN: frozenset(
        {
            TradeStatus.OPEN,
            TradeStatus.PARTIAL_FILL_PENDING,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.ENTRY_SENT: frozenset(
        {
            TradeStatus.ENTRY_FILLED_AWAITING_POSITION_SYNC,
            TradeStatus.ENTRY_ACK_UNKNOWN,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.ENTRY_ACK_UNKNOWN: frozenset(
        {
            TradeStatus.ENTRY_FILLED_AWAITING_POSITION_SYNC,
            TradeStatus.OPEN,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.ENTRY_FILLED_AWAITING_POSITION_SYNC: frozenset(
        {
            TradeStatus.OPEN,
            TradeStatus.PARTIAL_FILL_PENDING,
            TradeStatus.LOST_IN_TRANSMISSION,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.OPEN: frozenset(
        {
            TradeStatus.CLOSING_INITIATED,
            TradeStatus.PARTIAL_FILL_PENDING,
            TradeStatus.EXIT_STUCK,
            TradeStatus.LOST_IN_TRANSMISSION,
        }
    ),
    TradeStatus.PARTIAL_FILL_PENDING: frozenset(
        {
            TradeStatus.PARTIAL_FILL,
            TradeStatus.OPEN,
            TradeStatus.CLOSING_INITIATED,
        }
    ),
    TradeStatus.PARTIAL_FILL: frozenset(
        {
            TradeStatus.OPEN,
            TradeStatus.CLOSING_INITIATED,
            TradeStatus.PARTIAL_FILL_PENDING,
        }
    ),
    TradeStatus.CLOSING_INITIATED: frozenset(
        {
            TradeStatus.CLOSED,
            TradeStatus.EXIT_STUCK,
            TradeStatus.OPEN,
        }
    ),
    TradeStatus.EXIT_STUCK: frozenset(
        {
            TradeStatus.HALTED,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.EXIT_SENT: frozenset(
        {
            TradeStatus.CLOSED,
            TradeStatus.CLOSING_INITIATED,
        }
    ),
    TradeStatus.LOST_IN_TRANSMISSION: frozenset(
        {
            TradeStatus.OPEN,
            TradeStatus.CLOSED,
        }
    ),
    TradeStatus.EMERGENCY_CLOSE_PENDING: frozenset(
        {
            TradeStatus.CLOSED,
            TradeStatus.HALTED,
        }
    ),
    TradeStatus.PARTIAL_FILL_CANCEL_FAILED: frozenset(
        {
            TradeStatus.HALTED,
            TradeStatus.CLOSING_INITIATED,
        }
    ),
    TradeStatus.ORDER_LOOKUP_FAILED: frozenset(
        {
            TradeStatus.OPEN,
            TradeStatus.CLOSED,
            TradeStatus.HALTED,
        }
    ),
    TradeStatus.CLOSED: frozenset(),
    TradeStatus.HALTED: frozenset(),
}


def is_active(status: str | TradeStatus) -> bool:
    if isinstance(status, str):
        try:
            status = TradeStatus(status)
        except ValueError:
            return False
    return status in _ACTIVE_STATES


def is_closed(status: str | TradeStatus) -> bool:
    if isinstance(status, str):
        try:
            status = TradeStatus(status)
        except ValueError:
            return False
    return status in _CLOSED_STATES


def validate_transition(current: str | TradeStatus, next_status: str | TradeStatus) -> bool:
    if isinstance(current, str):
        try:
            current = TradeStatus(current)
        except ValueError:
            return True
    if isinstance(next_status, str):
        try:
            next_status = TradeStatus(next_status)
        except ValueError:
            return True

    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    return next_status in allowed


def wrap_status(status: str) -> str:
    return status


def open_trade_statuses() -> frozenset[str]:
    return frozenset(s.value for s in _ACTIVE_STATES)


def closing_statuses() -> frozenset[str]:
    return frozenset(
        {
            TradeStatus.CLOSING_INITIATED.value,
            TradeStatus.PARTIAL_FILL.value,
            TradeStatus.PARTIAL_FILL_PENDING.value,
        }
    )
