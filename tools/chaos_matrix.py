#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from unittest.mock import patch

import ccxt

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.execution_service import ExecutionService
from tests.mocks.binance_chaos import (
    AmbiguousIocExchange,
    ChaseLimitNoFillExchange,
    ClientOrderLookupExchange,
    ConcurrentTimeoutExchange,
    ExchangeUnavailableOnce,
    NoPriceExchange,
    RateLimitedCloseExchange,
)


@dataclass(frozen=True)
class ChaosScenario:
    scenario_id: str
    description: str
    expected_outcome: str
    critical_invariant: str


CHAOS_MATRIX: tuple[ChaosScenario, ...] = (
    ChaosScenario(
        scenario_id="create_ack_timeout_recovered_by_client_id",
        description="Timeout after create_order ack but recoverable via clientOrderId lookup",
        expected_outcome="Order recovered without duplicate exposure",
        critical_invariant="Single logical entry; no duplicate create attempts",
    ),
    ChaosScenario(
        scenario_id="ioc_ambiguous_fill_confirmed",
        description="IOC order initially ambiguous but confirmed by later fetch_order",
        expected_outcome="Order transitions to closed/filled cleanly",
        critical_invariant="Ambiguous fill resolved without orphan state",
    ),
    ChaosScenario(
        scenario_id="chase_limit_hard_floor_stuck",
        description="Close position chase exhausts all steps and leaves order stuck in book",
        expected_outcome="exit_state=STUCK and manual intervention required",
        critical_invariant="No silent success when exposure remains unresolved",
    ),
    ChaosScenario(
        scenario_id="no_price_market_exit_escalation",
        description="Ticker price unavailable until no-price threshold escalates to market exit",
        expected_outcome="Single market reduce-only exit after threshold",
        critical_invariant="Escalation bounded and explicit",
    ),
    ChaosScenario(
        scenario_id="concurrent_timeout_restore",
        description="Concurrent cancel_order calls must restore exchange timeout safely",
        expected_outcome="All calls see overridden timeout, exchange timeout restored after completion",
        critical_invariant="No timeout leakage across threads",
    ),
    ChaosScenario(
        scenario_id="order_lookup_not_found",
        description="clientOrderId lookup returns OrderNotFound after ambiguous create",
        expected_outcome="Lookup resolves to None rather than transport failure",
        critical_invariant="OrderNotFound is not escalated as duplicate exposure by itself",
    ),
    ChaosScenario(
        scenario_id="exchange_502_retry_recovers",
        description="Exchange 502/ExchangeNotAvailable during read recovers on bounded retry",
        expected_outcome="Read succeeds after one retry",
        critical_invariant="Transient exchange outage does not corrupt order state",
    ),
    ChaosScenario(
        scenario_id="rate_limit_close_retries_reduce_only",
        description="Rate limit during close path is retried before reduce-only close",
        expected_outcome="Close order is created after bounded retry",
        critical_invariant="Close retry remains bounded and does not duplicate exposure",
    ),
)


def _service(exchange):
    service = ExecutionService("k", "s")
    service.exchange = exchange
    service.set_weight_tracker(None)
    return service


def run_chaos_matrix() -> dict:
    results = []

    service = _service(
        ClientOrderLookupExchange(
            lookup_order={
                "orderId": "ord-1",
                "status": "FILLED",
                "clientOrderId": "cid-1",
                "executedQty": "0.1",
                "avgPrice": "101.0",
            }
        )
    )
    order = service.create_precision_order("BTC/USDT", "BUY", 0.1, 100.0, client_order_id="cid-1")
    results.append(
        {
            "scenario_id": "create_ack_timeout_recovered_by_client_id",
            "passed": bool(order and order.get("id") == "ord-1" and service.exchange.create_attempts == 1),
            "details": {"order_id": (order or {}).get("id"), "create_attempts": service.exchange.create_attempts},
        }
    )

    service = _service(AmbiguousIocExchange())
    with patch("core.execution_service.Config.ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS", 0.5), patch(
        "core.execution_service.time.sleep", return_value=None
    ):
        order = service.create_precision_order("BTC/USDT", "BUY", 0.1, 100.0, client_order_id="cid-2")
    results.append(
        {
            "scenario_id": "ioc_ambiguous_fill_confirmed",
            "passed": bool(order and order.get("status") == "closed" and service.exchange.fetch_attempts == 1),
            "details": {"status": (order or {}).get("status"), "fetch_attempts": service.exchange.fetch_attempts},
        }
    )

    service = _service(ChaseLimitNoFillExchange())
    with patch("core.execution_service.time.sleep", return_value=None):
        order = service.close_position("BTC/USDT", side="BUY", amount=0.1)
    results.append(
        {
            "scenario_id": "chase_limit_hard_floor_stuck",
            "passed": bool(order and order.get("exit_state") == "STUCK"),
            "details": {"exit_state": (order or {}).get("exit_state")},
        }
    )

    service = _service(NoPriceExchange())
    with patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", True), patch(
        "core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1
    ), patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 1), patch(
        "core.execution_service.time.monotonic",
        side_effect=[10.0, 10.2, 12.5, 12.5, 12.5, 12.5],
    ):
        service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)
    escalated = second if second is not None else third
    results.append(
        {
            "scenario_id": "no_price_market_exit_escalation",
            "passed": bool(escalated and escalated.get("type") == "market" and service.exchange.market_exit_calls == 1),
            "details": {"type": (escalated or {}).get("type"), "market_exit_calls": service.exchange.market_exit_calls},
        }
    )

    service = _service(ConcurrentTimeoutExchange())
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(lambda idx: service.cancel_order("BTC/USDT", f"ord-{idx}"), range(4)))
    passed = all(row.get("timeout_seen") == 20000 for row in rows) and service.exchange.timeout == 9000
    results.append(
        {
            "scenario_id": "concurrent_timeout_restore",
            "passed": passed,
            "details": {"seen_timeouts": list(service.exchange.seen_timeouts), "final_timeout": service.exchange.timeout},
        }
    )

    service = _service(ClientOrderLookupExchange(lookup_error=ccxt.OrderNotFound("Order does not exist")))
    lookup = service.fetch_order_by_client_id("BTC/USDT", "cid-missing")
    results.append(
        {
            "scenario_id": "order_lookup_not_found",
            "passed": lookup is None,
            "details": {"lookup": lookup},
        }
    )

    service = _service(ExchangeUnavailableOnce())
    ticker = service.fetch_ticker("BTC/USDT")
    results.append(
        {
            "scenario_id": "exchange_502_retry_recovers",
            "passed": bool(ticker and ticker.get("last") == 100.0 and service.exchange.fetch_attempts == 2),
            "details": {"fetch_attempts": service.exchange.fetch_attempts, "last": (ticker or {}).get("last")},
        }
    )

    service = _service(RateLimitedCloseExchange())
    with patch("core.execution_service.time.sleep", return_value=None):
        close = service.close_position("BTC/USDT", side="BUY", amount=0.1)
    results.append(
        {
            "scenario_id": "rate_limit_close_retries_reduce_only",
            "passed": bool(close and close.get("status") == "closed" and service.exchange.cancel_attempts == 2),
            "details": {"cancel_attempts": service.exchange.cancel_attempts, "status": (close or {}).get("status")},
        }
    )

    return {
        "matrix": [asdict(item) for item in CHAOS_MATRIX],
        "results": results,
        "summary": {
            "scenarios": len(results),
            "passed": sum(1 for row in results if row["passed"]),
            "failed": sum(1 for row in results if not row["passed"]),
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Chaos matrix runner for execution/runtime invariants")
    parser.add_argument("--output", default="reports/chaos_matrix_report.json")
    args = parser.parse_args()

    report = run_chaos_matrix()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
