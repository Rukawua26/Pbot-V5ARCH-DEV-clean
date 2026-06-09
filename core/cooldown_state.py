import threading
from datetime import datetime

from core.time_utils import monotonic_now, parse_datetime_utc, utc_now

_cooldown_lock = threading.RLock()

COOLDOWN_META_KEY = "cooldown_pairs_utc"


def _to_utc_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return parse_datetime_utc(value)
    try:
        return parse_datetime_utc(value)
    except Exception:
        return None


def persist_cooldowns(bot) -> None:
    with _cooldown_lock:
        if not hasattr(bot, "brain") or bot.brain is None:
            return
        normalized = {}
        for symbol, until in (getattr(bot, "cooldown_pairs", {}) or {}).items():
            dt = _to_utc_datetime(until)
            if dt is not None and dt > utc_now():
                normalized[str(symbol)] = dt.isoformat()
        setter = getattr(bot.brain, "set_metadata_json", None)
        if callable(setter):
            setter(COOLDOWN_META_KEY, normalized)


def load_cooldowns(bot) -> None:
    with _cooldown_lock:
        getter = getattr(getattr(bot, "brain", None), "get_metadata_json", None)
        if not callable(getter):
            return

        raw = getter(COOLDOWN_META_KEY, default={}) or {}
        now_utc = utc_now()
        now_mono = monotonic_now()
        restored = {}
        deadlines = {}

        for symbol, value in raw.items():
            dt = _to_utc_datetime(value)
            if dt is None or dt <= now_utc:
                continue
            restored[str(symbol)] = dt
            deadlines[str(symbol)] = now_mono + max(0.0, (dt - now_utc).total_seconds())

        bot.cooldown_pairs = restored
        bot.cooldown_deadlines_mono = deadlines


def set_symbol_cooldown(bot, symbol: str, until_utc) -> None:
    with _cooldown_lock:
        dt = _to_utc_datetime(until_utc)
        if dt is None:
            return

        if not hasattr(bot, "cooldown_pairs") or bot.cooldown_pairs is None:
            bot.cooldown_pairs = {}
        if not hasattr(bot, "cooldown_deadlines_mono") or bot.cooldown_deadlines_mono is None:
            bot.cooldown_deadlines_mono = {}

        bot.cooldown_pairs[symbol] = dt
        bot.cooldown_deadlines_mono[symbol] = monotonic_now() + max(
            0.0, (dt - utc_now()).total_seconds()
        )
    persist_cooldowns(bot)


def clear_symbol_cooldown(bot, symbol: str) -> None:
    with _cooldown_lock:
        if hasattr(bot, "cooldown_pairs") and symbol in bot.cooldown_pairs:
            del bot.cooldown_pairs[symbol]
        if hasattr(bot, "cooldown_deadlines_mono") and symbol in bot.cooldown_deadlines_mono:
            del bot.cooldown_deadlines_mono[symbol]
    persist_cooldowns(bot)


def is_symbol_in_cooldown(bot, symbol: str):
    with _cooldown_lock:
        now_mono = monotonic_now()
        now_utc = utc_now()

        cooldown_pairs = getattr(bot, "cooldown_pairs", {}) or {}
        deadlines = getattr(bot, "cooldown_deadlines_mono", {}) or {}

        if symbol not in cooldown_pairs:
            return False, 0

        until = _to_utc_datetime(cooldown_pairs.get(symbol))
        if until is None:
            clear_symbol_cooldown(bot, symbol)
            return False, 0

        deadline = deadlines.get(symbol)
        if deadline is None:
            if until <= now_utc:
                clear_symbol_cooldown(bot, symbol)
                return False, 0
            deadline = now_mono + max(0.0, (until - now_utc).total_seconds())
            deadlines[symbol] = deadline
            bot.cooldown_deadlines_mono = deadlines

        remaining_seconds = max(0.0, deadline - now_mono)
        if remaining_seconds <= 0.0:
            clear_symbol_cooldown(bot, symbol)
            return False, 0

        remaining_minutes = int(remaining_seconds / 60.0) + 1
        return True, remaining_minutes


def cleanup_expired_cooldowns(bot) -> None:
    with _cooldown_lock:
        now_utc = utc_now()
        now_mono = monotonic_now()
        changed = False
        for symbol, until in list((getattr(bot, "cooldown_pairs", {}) or {}).items()):
            deadline = (getattr(bot, "cooldown_deadlines_mono", {}) or {}).get(symbol)
            if deadline is not None:
                if float(deadline) > now_mono:
                    continue
                changed = True
                if symbol in bot.cooldown_pairs:
                    del bot.cooldown_pairs[symbol]
                if hasattr(bot, "cooldown_deadlines_mono") and symbol in bot.cooldown_deadlines_mono:
                    del bot.cooldown_deadlines_mono[symbol]
                continue

            dt = _to_utc_datetime(until)
            if dt is None or dt <= now_utc:
                changed = True
                if symbol in bot.cooldown_pairs:
                    del bot.cooldown_pairs[symbol]
                if hasattr(bot, "cooldown_deadlines_mono") and symbol in bot.cooldown_deadlines_mono:
                    del bot.cooldown_deadlines_mono[symbol]
        if changed:
            persist_cooldowns(bot)
