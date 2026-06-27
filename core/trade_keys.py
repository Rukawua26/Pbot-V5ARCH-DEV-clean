from __future__ import annotations

from core.symbol_utils import normalize_position_symbol


def normalize_trade_side(side: str | None) -> str:
    value = str(side or "").upper()
    if value in {"LONG", "BUY"}:
        return "BUY"
    if value in {"SHORT", "SELL"}:
        return "SELL"
    return value


def make_trade_key(symbol: str, side: str | None = None, *, force_side: bool = False) -> str:
    normalized_symbol = normalize_position_symbol(symbol)
    normalized_side = normalize_trade_side(side)
    if force_side and normalized_symbol and normalized_side in {"BUY", "SELL"}:
        return f"{normalized_symbol}|{normalized_side}"
    return normalized_symbol


def make_state_trade_key(state: dict, *, force_side: bool = False) -> str:
    explicit_key = str(state.get("trade_key") or "")
    if explicit_key:
        return explicit_key
    return make_trade_key(state.get("symbol", ""), state.get("side"), force_side=force_side)


def split_trade_key(key: str) -> tuple[str, str | None]:
    raw = str(key or "")
    if "|" not in raw:
        return normalize_position_symbol(raw), None
    symbol, side = raw.rsplit("|", 1)
    return normalize_position_symbol(symbol), normalize_trade_side(side)


def find_trade_key(active_trades: dict, symbol: str, side: str | None = None) -> str | None:
    normalized_symbol = normalize_position_symbol(symbol)
    normalized_side = normalize_trade_side(side)
    if not normalized_symbol:
        return None
    if normalized_side in {"BUY", "SELL"}:
        side_key = make_trade_key(normalized_symbol, normalized_side, force_side=True)
        trade = active_trades.get(side_key)
        if isinstance(trade, dict) and normalize_trade_side(trade.get("side")) == normalized_side:
            return side_key
    trade = active_trades.get(normalized_symbol)
    if isinstance(trade, dict):
        if (
            normalized_side not in {"BUY", "SELL"}
            or normalize_trade_side(trade.get("side")) == normalized_side
        ):
            return normalized_symbol
    matches = []
    for key, candidate in active_trades.items():
        if not isinstance(candidate, dict):
            continue
        if normalize_position_symbol(candidate.get("symbol", key)) != normalized_symbol:
            continue
        if (
            normalized_side in {"BUY", "SELL"}
            and normalize_trade_side(candidate.get("side")) != normalized_side
        ):
            continue
        matches.append(str(key))
    if len(matches) == 1:
        return matches[0]
    return None


def has_trade(active_trades: dict, symbol: str, side: str | None = None) -> bool:
    return find_trade_key(active_trades, symbol, side) is not None
