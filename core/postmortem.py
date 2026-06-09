from typing import Any

from config import Config


class PostMortemLabeler:
    LABELS = {
        "STOP_LOSS_VOLATILITY": "SL triggered by volatility wick, then price resumed original direction",
        "STOP_LOSS_TIGHT": "SL too tight for asset noise - price reversed immediately after hit",
        "TREND_REVERSAL": "Entry thesis wrong - price reversed from entry without testing TP",
        "TREND_BREAK": "Trend broke structurally - no recovery after entry",
        "TIMEOUT_STAGNATION": "No price action - trade expired by timeout",
        "TAKE_PROFIT": "TP hit successfully",
        "HARD_SL": "Hard stop loss triggered (risk limit)",
        "DYNAMIC_SL": "Dynamic trailing stop hit",
        "BAILOUT": "Emergency exit by guardian/other system",
        "MANUAL": "Manual close by operator",
        "UNKNOWN": "Unknown exit reason",
    }

    @classmethod
    def classify_exit(
        cls,
        reason: str,
        entry_price: float,
        exit_price: float,
        side: str,
        mae_percent: float,
        mfe_percent: float,
        trade: dict[str, Any],
    ) -> str:
        reason_upper = reason.upper() if reason else ""

        if "TP" in reason_upper or "TAKE_PROFIT" in reason_upper:
            return "TAKE_PROFIT"

        if "HARD SL" in reason_upper:
            return "HARD_SL"

        if "DYNAMIC" in reason_upper:
            return "DYNAMIC_SL"

        if "TRAILING" in reason_upper:
            return "DYNAMIC_SL"

        if "BAILOUT" in reason_upper or "ABORT" in reason_upper:
            return "BAILOUT"

        if "TIMEOUT" in reason_upper:
            return "TIMEOUT_STAGNATION"

        if "SL" in reason_upper or "STOP_LOSS" in reason_upper:
            return cls._classify_sl_cause(
                entry_price,
                exit_price,
                side,
                mae_percent,
                mfe_percent,
                trade,
            )

        if "MANUAL" in reason_upper:
            return "MANUAL"

        return "UNKNOWN"

    @classmethod
    def _classify_sl_cause(
        cls,
        entry_price: float,
        exit_price: float,
        side: str,
        mae_percent: float,
        mfe_percent: float,
        trade: dict[str, Any],
    ) -> str:
        entry_atr = trade.get("entry_atr", 0)
        volatility_at_entry = trade.get("market_snapshot", {}).get("atr_pct", 0)

        if entry_atr > 0 and volatility_at_entry > 0:
            sl_distance_pct = abs(entry_price - exit_price) / entry_price * 100
            noise_ratio = volatility_at_entry * Config.STOP_LOSS_ATR_MODIFIER

            if sl_distance_pct < noise_ratio * 0.5:
                return "STOP_LOSS_TIGHT"

        if mae_percent > 5.0 and mfe_percent < 1.0:
            return "TREND_REVERSAL"

        if mae_percent > 2.0 and mfe_percent > mae_percent * 0.5:
            return "STOP_LOSS_VOLATILITY"

        if mfe_percent < 1.0 and mae_percent < 3.0:
            return "TIMEOUT_STAGNATION"

        return "STOP_LOSS_VOLATILITY"

    @classmethod
    def compute_mae_mfe_at_sl(
        cls,
        trade: dict[str, Any],
        exit_price: float,
    ) -> tuple:
        entry = trade.get("entry", 0)
        side = trade.get("side", "BUY")
        mae_price = trade.get("mae_price", entry)
        mfe_price = trade.get("mfe_price", entry)

        if not entry or not mae_price or not mfe_price:
            return 0.0, 0.0

        if side == "BUY":
            mae_at_sl = ((entry - mae_price) / entry * 100) if mae_price else 0.0
            mfe_at_sl = ((mfe_price - entry) / entry * 100) if mfe_price else 0.0
        else:
            mae_at_sl = ((mae_price - entry) / entry * 100) if mae_price else 0.0
            mfe_at_sl = ((entry - mfe_price) / entry * 100) if mfe_price else 0.0

        return mae_at_sl, mfe_at_sl


def label_exit_reason(
    reason: str,
    entry_price: float,
    exit_price: float,
    side: str,
    mae_percent: float,
    mfe_percent: float,
    trade: dict[str, Any],
    is_adopted: bool = False,
) -> dict[str, Any]:
    label = PostMortemLabeler.classify_exit(
        reason,
        entry_price,
        exit_price,
        side,
        mae_percent,
        mfe_percent,
        trade,
    )

    mae_at_sl, mfe_at_sl = PostMortemLabeler.compute_mae_mfe_at_sl(trade, exit_price)

    return {
        "exit_reason": label,
        "is_adopted": 1 if is_adopted else 0,
        "is_dirty": 1
        if is_adopted and not trade.get("market_snapshot", {}).get("features_json")
        else 0,
        "mae_at_sl": mae_at_sl,
        "mfe_at_sl": mfe_at_sl,
    }
