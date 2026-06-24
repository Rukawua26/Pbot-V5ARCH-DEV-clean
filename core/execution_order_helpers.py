from __future__ import annotations

import hashlib
from typing import cast

from core.types import CCXTOrder


def _with_exit_state(order: CCXTOrder | dict | None, exit_state: str) -> CCXTOrder | None:
    if not isinstance(order, dict):
        return order
    enriched = dict(order)
    enriched["exit_state"] = exit_state
    return cast(CCXTOrder, enriched)


def _parse_order_float(order: CCXTOrder | dict | None, *keys: str) -> float | None:
    if not isinstance(order, dict):
        return None
    for key in keys:
        value = order.get(key)
        if value is None:
            info_val = order.get("info")
            info = info_val if isinstance(info_val, dict) else {}
            value = info.get(key)
        try:
            if value is not None:
                return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _exit_client_order_id(symbol: str, exit_side: str, label: str, step_idx: int) -> str:
    raw = f"{symbol}|{exit_side}|{label}|{step_idx}"
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=10).hexdigest().upper()
    return f"X_{digest}"


def _execute_chase_limit_steps(
    execution_service,
    symbol: str,
    exit_side: str,
    amount: float,
    current_price: float,
    params: dict,
    client_order_label: str = "close",
) -> CCXTOrder | None:
    chase_steps = [0.98, 0.97, 0.96, 0.95]
    timeout_per_step = 2

    last_order = None
    for step_idx, step_mult in enumerate(chase_steps):
        limit_price = (
            current_price * step_mult if exit_side == "sell" else current_price * (2 - step_mult)
        )
        try:
            limit_price = execution_service.exchange.price_to_precision(symbol, limit_price)
            step_params = dict(params or {})
            step_params.setdefault(
                "newClientOrderId",
                _exit_client_order_id(symbol, exit_side, client_order_label, step_idx),
            )
            client_order_id = step_params["newClientOrderId"]
            try:
                order = execution_service._call_exchange(
                    "close_position_create_order",
                    lambda: execution_service.exchange.create_order(
                        symbol, "limit", exit_side, amount, limit_price, step_params
                    ),
                    retries=1,
                    timeout_s=20.0,
                )
                execution_service._track_api_weight("create_order", 1, "trading")
            except Exception as create_err:
                try:
                    recovered = execution_service.fetch_order_by_client_id(symbol, client_order_id)
                    if recovered:
                        execution_service.logger.warning(
                            f"⚠️ Chase step {step_idx + 1} recuperado por clientOrderId tras error: "
                            f"{client_order_id}"
                        )
                        order = recovered
                    else:
                        raise create_err
                except Exception:
                    raise create_err
            last_order = order

            if execution_service._wait_order_filled(
                symbol, order["id"], timeout_s=timeout_per_step
            ):
                execution_service.logger.info(
                    f"✅ CHASE_LIMIT OK {symbol} @ {limit_price} "
                    f"(step {step_idx + 1}/{len(chase_steps)})"
                )
                execution_service._no_price_exit_state.pop(symbol, None)
                return _with_exit_state(order, "FILLED")

            execution_service.logger.warning(
                f"⏳ Chase step {step_idx + 1} timeout {symbol} @ {limit_price}, persiguiendo..."
            )
            if step_idx < len(chase_steps) - 1:
                try:
                    execution_service._call_exchange(
                        "close_position_cancel_order",
                        lambda: execution_service.exchange.cancel_order(order["id"], symbol),
                        retries=2,
                        timeout_s=15.0,
                    )
                except Exception as cancel_err:
                    execution_service.logger.warning(
                        f"⚠️ Cancel falló en chase step {step_idx + 1} {symbol}: {cancel_err}. "
                        f"Verificando estado antes de continuar..."
                    )
                    try:
                        open_orders = (
                            execution_service._call_exchange_account(
                                "chase_verify_open_orders",
                                lambda: execution_service.exchange.fetch_open_orders(symbol),
                                retries=1,
                                timeout_s=10.0,
                            )
                            or []
                        )
                        still_open = any(
                            o.get("id") == order.get("id")
                            for o in open_orders
                            if isinstance(o, dict)
                        )
                        if still_open:
                            execution_service.logger.critical(
                                f"🚨 CHASE_CANCEL_AMBIGUOUS {symbol}: orden {order.get('id')} "
                                f"sigue abierta tras cancel fallido. Marcando STUCK."
                            )
                            return _with_exit_state(order, "STUCK")
                    except Exception as verify_err:
                        execution_service.logger.warning(
                            f"⚠️ No se pudo verificar estado de orden tras cancel ambiguo {symbol}: {verify_err}"
                        )

        except Exception as step_err:
            execution_service.logger.warning(
                f"⚠️ Chase step {step_idx + 1} falló {symbol}: {step_err}"
            )
            continue

    return last_order
