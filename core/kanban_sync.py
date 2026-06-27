"""Wrapper asíncrono para la integración con GitHub Projects Kanban.

Solo se rastrean operaciones REALES (PAPER/LIVE). Los trades SHADOW
son simulaciones internas y no se reflejan en el tablero.
"""

import logging
import threading
import time
from typing import Any

from core.trade_keys import find_trade_key
from tools.github_projects_kanban import (
    actualizar_pnl_tarjeta,
    crear_tarjeta_operacion,
    mover_tarjeta,
)

logger = logging.getLogger("SniperAI")


def _is_real_trade(bot, symbol: str) -> bool:
    """Devuelve True solo si el trade es REAL o PAPER (no SHADOW)."""
    with bot.lock:
        trade_key = find_trade_key(bot.active_trades, symbol)
        trade = bot.active_trades.get(trade_key, {}) if trade_key else {}
    return not trade.get("is_shadow", True)


def _safe_update_trade_state(
    bot, symbol: str, key: str, value: Any, trade_key: str | None = None
) -> None:
    with bot.lock:
        resolved_key = trade_key or find_trade_key(bot.active_trades, symbol)
        if resolved_key in bot.active_trades:
            bot.active_trades[resolved_key][key] = value
            # Persistir en BD para sobrevivir reinicios
            with bot.db_lock:
                bot.brain.save_active_trade_state(resolved_key, bot.active_trades[resolved_key])


def async_crear_tarjeta(
    bot,
    symbol: str,
    estrategia: str,
    capital: float,
    is_shadow: bool = False,
    trade_key: str | None = None,
) -> None:
    """Crea la tarjeta en el Kanban. Solo actúa para trades REALES/PAPER."""
    if is_shadow:
        return

    def _run():
        res = crear_tarjeta_operacion(symbol, estrategia, capital)
        if res.get("ok"):
            item_id = res.get("item_id")
            logger.info(f"✅ Kanban: Tarjeta creada para {symbol} → item_id={item_id}")

            # Escribir item_id en el estado del trade ANTES de moverlo
            _safe_update_trade_state(bot, symbol, "kanban_item_id", item_id, trade_key)

            # Leer el estado actual del trade para elegir columna de destino
            with bot.lock:
                resolved_key = trade_key or find_trade_key(bot.active_trades, symbol)
                trade = bot.active_trades.get(resolved_key, {}) if resolved_key else {}
                status = trade.get("status", "")

            target_col = "Posiciones Abiertas" if status == "OPEN" else "Órdenes Pendientes"
            move_res = mover_tarjeta(item_id, target_col)
            if move_res.get("ok"):
                logger.info(f"✅ Kanban: {symbol} movida a '{target_col}'")
            else:
                logger.error(
                    f"Kanban Sync Error [mover tras crear {symbol}]: {move_res.get('error')}"
                )
        else:
            logger.error(f"Kanban Sync Error [crear_tarjeta {symbol}]: {res.get('error')}")

    threading.Thread(target=_run, daemon=True, name=f"kanban-crear-{symbol}").start()


def async_mover_tarjeta(item_id: str, columna_destino: str) -> None:
    """Mueve una tarjeta existente a otra columna del Kanban."""
    if not item_id:
        return

    def _run():
        res = mover_tarjeta(item_id, columna_destino)
        if res.get("ok"):
            logger.info(f"✅ Kanban: item {item_id} movido a '{columna_destino}'")
        else:
            logger.error(
                f"Kanban Sync Error [mover_tarjeta a {columna_destino}]: {res.get('error')}"
            )

    threading.Thread(target=_run, daemon=True, name=f"kanban-mover-{columna_destino}").start()


_last_pnl_updates: dict[str, float] = {}


def async_actualizar_pnl(item_id: str, pnl_actual: float, precio_actual: float) -> None:
    """Actualiza el PnL de la tarjeta. Rate-limited a 1 actualización cada 30 segundos."""
    if not item_id:
        return

    # Rate limiting: máximo 1 update cada 30 segundos por tarjeta
    now = time.time()
    last = _last_pnl_updates.get(item_id, 0.0)
    if now - last < 30.0:
        return
    _last_pnl_updates[item_id] = now

    def _run():
        res = actualizar_pnl_tarjeta(item_id, pnl_actual, precio_actual)
        if not res.get("ok"):
            logger.error(f"Kanban Sync Error [actualizar_pnl item={item_id}]: {res.get('error')}")

    threading.Thread(target=_run, daemon=True, name="kanban-pnl-update").start()
