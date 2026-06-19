#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from core.backtester import VectorBacktester
from core.strategy.shocks import next_shock_distance_pct
from tools.walk_forward_backtest import BacktestParams, load_candles_csv


@dataclass(frozen=True)
class FidelityParams:
    symbol: str
    limit: int
    timeframe: str
    max_time_delta_seconds: int
    strategy_mode: str
    score_pass_threshold: float
    apply_shock_veto: bool
    shock_min_dist_pct: float
    apply_market_breadth_veto: bool
    apply_mtf_veto: bool
    apply_kava_veto: bool
    apply_runtime_confidence_gate: bool
    shadow_min_threshold: float


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows, _stats = _load_jsonl_with_stats(path)
    return rows


def _load_jsonl_with_stats(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    stats = {"lines": 0, "malformed": 0, "empty": 0}
    if not path.exists():
        return rows, stats
    with path.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            stats["lines"] += 1
            line = line.strip()
            if not line:
                stats["empty"] += 1
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                stats["malformed"] += 1
                continue
    return rows, stats


def _runtime_label(payload: dict[str, Any]) -> str:
    side = str(payload.get("side") or "").upper()
    mode = str(payload.get("mode") or "").upper()
    filter_passed = payload.get("filter_passed")
    if mode in {"REAL", "SHADOW"}:
        return side if side in {"BUY", "SELL"} else "EXECUTE"
    if filter_passed is True:
        return side if side in {"BUY", "SELL"} else "EXECUTE"
    return "NONE"


def extract_runtime_decisions(
    events: list[dict[str, Any]],
    *,
    symbol: str,
    limit: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    symbol_norm = symbol.upper()
    for event in events:
        event_name = str(event.get("event") or "")
        if event_name not in {"FILTER_APPLIED", "SIGNAL_ANALYZED"}:
            continue
        payload = event.get("payload") or {}
        if str(payload.get("symbol") or "").upper() != symbol_norm:
            continue
        ts = pd.to_datetime(event.get("ts"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        label = _runtime_label(payload)
        rows.append(
            {
                "ts": ts,
                "event": event_name,
                "symbol": payload.get("symbol"),
                "side": str(payload.get("side") or "").upper(),
                "runtime_label": label,
                "runtime_action": "EXECUTE" if label in {"BUY", "SELL"} else "NONE",
                "runtime_side": label if label in {"BUY", "SELL"} else "NONE",
                "prob_final": float(payload.get("prob_final", 0.0) or 0.0),
                "filter_passed": payload.get("filter_passed"),
                "filter_reason": payload.get("filter_reason"),
                "mode": payload.get("mode"),
            }
        )
    if not rows:
        return pd.DataFrame()
    frame = (
        pd.DataFrame(rows)
        .sort_values("ts")
        .drop_duplicates(subset=["ts", "event", "runtime_label"], keep="last")
    )
    if limit > 0:
        frame = frame.tail(limit)
    return frame.reset_index(drop=True)


def _apply_shock_vetoes(
    frame: pd.DataFrame,
    candles: pd.DataFrame,
    *,
    min_dist_pct: float,
) -> pd.DataFrame:
    out = frame.copy()
    out["proxy_raw_label"] = out["proxy_label"]
    out["proxy_raw_action"] = out["proxy_action"]
    out["proxy_veto_reason"] = None
    out["proxy_shock_dist_pct"] = None
    out["proxy_shock_level"] = None

    candles_work = candles.copy().reset_index(drop=True)
    candles_work["time"] = pd.to_datetime(candles_work["time"], utc=True, errors="coerce")
    for idx, row in out.iterrows():
        side = str(row.get("proxy_action") or "")
        if side not in {"BUY", "SELL"}:
            continue
        candle_time = pd.to_datetime(row.get("time"), utc=True, errors="coerce")
        if pd.isna(candle_time):
            continue
        history = candles_work[candles_work["time"] <= candle_time]
        shock_dist, shock_level = next_shock_distance_pct(
            history,
            side,
            pivot_window=int(getattr(Config, "SHOCK_PIVOT_WINDOW", 3)),
            lookback_bars=int(getattr(Config, "SHOCK_LOOKBACK_BARS", 240)),
        )
        out.at[idx, "proxy_shock_dist_pct"] = shock_dist
        out.at[idx, "proxy_shock_level"] = shock_level
        if shock_dist is not None and float(shock_dist) < float(min_dist_pct):
            out.at[idx, "proxy_label"] = "NONE"
            out.at[idx, "proxy_action"] = "NONE"
            out.at[idx, "proxy_veto_reason"] = (
                f"SHOCK DEMASIADO CERCA ({float(shock_dist):.2f}% < {float(min_dist_pct):.2f}%)"
            )
    return out


def _market_breadth_fear_candles(
    events: list[dict[str, Any]],
    *,
    timeframe: str,
) -> set[pd.Timestamp]:
    candles: set[pd.Timestamp] = set()
    for event in events:
        if str(event.get("event") or "") != "FILTER_APPLIED":
            continue
        payload = event.get("payload") or {}
        reason = _reason_bucket(payload.get("filter_reason"))
        if reason != "MARKET_BREADTH_FEAR":
            continue
        ts = pd.to_datetime(event.get("ts"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        candles.add(ts.floor(timeframe))
    return candles


def _mtf_veto_candles(
    events: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
) -> set[tuple[pd.Timestamp, str]]:
    candles: set[tuple[pd.Timestamp, str]] = set()
    symbol_norm = symbol.upper()
    for event in events:
        if str(event.get("event") or "") != "MTF_FILTER":
            continue
        payload = event.get("payload") or {}
        if str(payload.get("symbol") or "").upper() != symbol_norm:
            continue
        side = str(payload.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        reason = str(payload.get("reason") or "").upper()
        weight = float(payload.get("weight", 1.0) or 0.0)
        if weight > 0.0 and "MTF_VETO" not in reason:
            continue
        ts = pd.to_datetime(event.get("ts"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        candles.add((ts.floor(timeframe), side))
    return candles


def _filter_reason_veto_candles(
    events: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    reason_bucket: str,
) -> set[tuple[pd.Timestamp, str]]:
    candles: set[tuple[pd.Timestamp, str]] = set()
    symbol_norm = symbol.upper()
    for event in events:
        if str(event.get("event") or "") != "FILTER_APPLIED":
            continue
        payload = event.get("payload") or {}
        if str(payload.get("symbol") or "").upper() != symbol_norm:
            continue
        if _reason_bucket(payload.get("filter_reason")) != reason_bucket:
            continue
        side = str(payload.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        ts = pd.to_datetime(event.get("ts"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        candles.add((ts.floor(timeframe), side))
    return candles


def _append_proxy_veto_reason(current: Any, reason: str) -> str:
    existing = str(current or "").strip()
    return f"{existing}; {reason}" if existing else reason


def _apply_market_breadth_vetoes(
    frame: pd.DataFrame,
    fear_candles: set[pd.Timestamp],
    *,
    timeframe: str,
) -> pd.DataFrame:
    out = frame.copy()
    out["proxy_market_breadth_fear_active"] = False
    if "proxy_raw_label" not in out.columns:
        out["proxy_raw_label"] = out["proxy_label"]
    if "proxy_raw_action" not in out.columns:
        out["proxy_raw_action"] = out["proxy_action"]
    if "proxy_veto_reason" not in out.columns:
        out["proxy_veto_reason"] = None
    for idx, row in out.iterrows():
        candle_time = pd.to_datetime(row.get("time"), utc=True, errors="coerce")
        if pd.isna(candle_time):
            continue
        if candle_time.floor(timeframe) not in fear_candles:
            continue
        out.at[idx, "proxy_market_breadth_fear_active"] = True
        if str(row.get("proxy_action") or "") != "BUY":
            continue
        out.at[idx, "proxy_label"] = "NONE"
        out.at[idx, "proxy_action"] = "NONE"
        out.at[idx, "proxy_veto_reason"] = _append_proxy_veto_reason(
            row.get("proxy_veto_reason"),
            "MARKET_BREADTH_FEAR",
        )
    return out


def _apply_mtf_vetoes(
    frame: pd.DataFrame,
    mtf_veto_candles: set[tuple[pd.Timestamp, str]],
    *,
    timeframe: str,
) -> pd.DataFrame:
    out = frame.copy()
    out["proxy_mtf_veto_active"] = False
    for idx, row in out.iterrows():
        side = str(row.get("proxy_action") or "")
        if side not in {"BUY", "SELL"}:
            continue
        candle_time = pd.to_datetime(row.get("time"), utc=True, errors="coerce")
        if pd.isna(candle_time):
            continue
        if (candle_time.floor(timeframe), side) not in mtf_veto_candles:
            continue
        out.at[idx, "proxy_mtf_veto_active"] = True
        out.at[idx, "proxy_label"] = "NONE"
        out.at[idx, "proxy_action"] = "NONE"
        out.at[idx, "proxy_veto_reason"] = _append_proxy_veto_reason(
            row.get("proxy_veto_reason"),
            "MTF_VETO",
        )
    return out


def _apply_side_specific_vetoes(
    frame: pd.DataFrame,
    veto_candles: set[tuple[pd.Timestamp, str]],
    *,
    timeframe: str,
    reason: str,
    active_column: str,
) -> pd.DataFrame:
    out = frame.copy()
    out[active_column] = False
    for idx, row in out.iterrows():
        side = str(row.get("proxy_action") or "")
        if side not in {"BUY", "SELL"}:
            continue
        candle_time = pd.to_datetime(row.get("time"), utc=True, errors="coerce")
        if pd.isna(candle_time):
            continue
        if (candle_time.floor(timeframe), side) not in veto_candles:
            continue
        out.at[idx, active_column] = True
        out.at[idx, "proxy_label"] = "NONE"
        out.at[idx, "proxy_action"] = "NONE"
        out.at[idx, "proxy_veto_reason"] = _append_proxy_veto_reason(
            row.get("proxy_veto_reason"),
            reason,
        )
    return out


def _proxy_frame(
    candles: pd.DataFrame,
    params: BacktestParams,
    strategy_mode: str,
    *,
    apply_shock_veto: bool,
    shock_min_dist_pct: float,
    apply_market_breadth_veto: bool,
    market_breadth_fear_candles: set[pd.Timestamp] | None,
    apply_mtf_veto: bool,
    mtf_veto_candles: set[tuple[pd.Timestamp, str]] | None,
    apply_kava_veto: bool,
    kava_veto_candles: set[tuple[pd.Timestamp, str]] | None,
    timeframe: str,
) -> pd.DataFrame:
    frame = VectorBacktester(candles).signal_frame(
        alma_offset=params.alma_offset,
        alma_sigma=params.alma_sigma,
        z_score_threshold=params.z_score_threshold,
        entropy_bins=params.entropy_bins,
        adx_threshold=params.adx_threshold,
        strategy_mode=strategy_mode,
    )
    frame = frame.sort_values("time").reset_index(drop=True)
    frame["proxy_action"] = frame["proxy_label"].where(
        frame["proxy_label"].isin(["BUY", "SELL"]), "NONE"
    )
    frame["proxy_raw_label"] = frame["proxy_label"]
    frame["proxy_raw_action"] = frame["proxy_action"]
    frame["proxy_veto_reason"] = None
    frame["proxy_market_breadth_fear_active"] = False
    frame["proxy_mtf_veto_active"] = False
    frame["proxy_kava_veto_active"] = False
    frame["proxy_shock_dist_pct"] = None
    frame["proxy_shock_level"] = None
    if apply_shock_veto:
        frame = _apply_shock_vetoes(
            frame,
            candles,
            min_dist_pct=shock_min_dist_pct,
        )
    if apply_market_breadth_veto:
        frame = _apply_market_breadth_vetoes(
            frame,
            market_breadth_fear_candles or set(),
            timeframe=timeframe,
        )
    if apply_mtf_veto:
        frame = _apply_mtf_vetoes(
            frame,
            mtf_veto_candles or set(),
            timeframe=timeframe,
        )
    if apply_kava_veto:
        frame = _apply_side_specific_vetoes(
            frame,
            kava_veto_candles or set(),
            timeframe=timeframe,
            reason="VETO_KAVA",
            active_column="proxy_kava_veto_active",
        )
    return frame


def align_runtime_to_proxy(
    runtime: pd.DataFrame,
    proxy: pd.DataFrame,
    *,
    timeframe: str,
    max_time_delta_seconds: int,
) -> pd.DataFrame:
    if runtime.empty or proxy.empty:
        return pd.DataFrame()
    runtime_work = runtime.copy()
    proxy_work = proxy.copy()
    runtime_work["candle_time"] = runtime_work["ts"].dt.floor(timeframe)
    collapsed_rows: list[dict[str, Any]] = []
    for candle_time, group in runtime_work.sort_values("ts").groupby("candle_time"):
        signal_rows = (
            group[group.get("event", "") == "SIGNAL_ANALYZED"]
            if "event" in group.columns
            else group.iloc[0:0]
        )
        filter_rows = (
            group[group.get("event", "") == "FILTER_APPLIED"]
            if "event" in group.columns
            else group.iloc[0:0]
        )
        final_row = signal_rows.tail(1).iloc[0] if not signal_rows.empty else group.tail(1).iloc[0]
        filter_row = filter_rows.tail(1).iloc[0] if not filter_rows.empty else None
        side = str(
            final_row.get("side")
            or final_row.get("runtime_side")
            or final_row.get("runtime_label")
            or ""
        ).upper()
        mode = str(final_row.get("mode") or "").upper()
        runtime_label = side if mode in {"REAL", "SHADOW"} and side in {"BUY", "SELL"} else "NONE"
        collapsed = final_row.to_dict()
        collapsed["candle_time"] = candle_time
        collapsed["runtime_label"] = runtime_label
        collapsed["runtime_action"] = runtime_label if runtime_label in {"BUY", "SELL"} else "NONE"
        collapsed["runtime_side"] = side if side in {"BUY", "SELL"} else "NONE"
        if filter_row is not None:
            collapsed["filter_passed"] = filter_row.get("filter_passed")
            collapsed["filter_reason"] = filter_row.get("filter_reason")
            collapsed["filter_prob_final"] = filter_row.get("prob_final")
            filter_passed = filter_row.get("filter_passed")
            filter_side = str(filter_row.get("side") or side).upper()
            if filter_passed == True and filter_side in {"BUY", "SELL"}:  # noqa: E712
                collapsed["runtime_label"] = filter_side
                collapsed["runtime_action"] = filter_side
                collapsed["runtime_side"] = filter_side
            elif filter_passed == False:  # noqa: E712
                collapsed["runtime_label"] = "NONE"
                collapsed["runtime_action"] = "NONE"
        collapsed_rows.append(collapsed)
    runtime_work = pd.DataFrame(collapsed_rows).reset_index(drop=True)
    proxy_work["candle_time"] = pd.to_datetime(proxy_work["time"], utc=True).dt.floor(timeframe)
    proxy_columns = [
        "candle_time",
        "time",
        "proxy_label",
        "proxy_action",
        "proxy_raw_label",
        "proxy_raw_action",
        "proxy_veto_reason",
        "proxy_market_breadth_fear_active",
        "proxy_mtf_veto_active",
        "proxy_kava_veto_active",
        "proxy_shock_dist_pct",
        "proxy_shock_level",
        "score",
        "signal_source_score",
        "signal_source_raw_signal",
        "mt_vote",
        "sr_vote",
        "adx",
        "close",
    ]
    for column in proxy_columns:
        if column not in proxy_work.columns:
            proxy_work[column] = None
    merged = runtime_work.merge(
        proxy_work[proxy_columns],
        on="candle_time",
        how="left",
    )
    merged = merged.dropna(subset=["time", "proxy_label"]).copy()
    if merged.empty:
        return merged
    merged["time_delta_seconds"] = (
        (merged["ts"] - pd.to_datetime(merged["time"], utc=True)).dt.total_seconds().abs()
    )
    return merged[merged["time_delta_seconds"] <= max_time_delta_seconds].reset_index(drop=True)


def apply_runtime_confidence_gate(
    aligned: pd.DataFrame,
    *,
    shadow_min_threshold: float,
) -> pd.DataFrame:
    if aligned.empty:
        return aligned
    out = aligned.copy()
    if "proxy_veto_reason" not in out.columns:
        out["proxy_veto_reason"] = None
    probs = pd.to_numeric(out.get("prob_final"), errors="coerce")
    mask = out["proxy_action"].isin(["BUY", "SELL"]) & probs.lt(float(shadow_min_threshold))
    for idx in out[mask].index:
        out.at[idx, "proxy_label"] = "NONE"
        out.at[idx, "proxy_action"] = "NONE"
        out.at[idx, "proxy_veto_reason"] = _append_proxy_veto_reason(
            out.at[idx, "proxy_veto_reason"],
            "BELOW_SHADOW_THRESHOLD",
        )
    return out


def _confusion(rows: pd.DataFrame, left: str, right: str) -> dict[str, int]:
    counter = Counter()
    for _, row in rows.iterrows():
        counter[f"{row[left]}->{row[right]}"] += 1
    return dict(sorted(counter.items()))


def _reason_bucket(raw_reason: Any) -> str:
    text = str(raw_reason or "").strip()
    if not text or text.lower() == "nan":
        return "NO_REASON"
    upper = text.upper()
    if "MARKET_BREADTH_FEAR" in upper:
        return "MARKET_BREADTH_FEAR"
    if "SHOCK DEMASIADO CERCA" in upper:
        return "SHOCK DEMASIADO CERCA"
    for token in (
        "MTF_VETO",
        "MARKET_BREADTH_FEAR",
        "SHOCK DEMASIADO CERCA",
        "COHERENCIA",
        "VETO_KAVA",
        "RANGE_VETO",
        "OI_DELTA",
        "CVD",
        "SPREAD",
        "CAPITAL",
        "RISK",
    ):
        if token in upper:
            return token
    return upper[:80]


def _counter_from_series(values: pd.Series) -> dict[str, int]:
    counter = Counter(str(value) for value in values)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _counter_from_multireason_series(values: pd.Series) -> dict[str, int]:
    counter = Counter()
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() == "nan":
            continue
        for item in text.split(";"):
            bucket = _reason_bucket(item.strip())
            if bucket != "NO_REASON":
                counter[bucket] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def summarize_fidelity(aligned: pd.DataFrame) -> dict[str, Any]:
    if aligned.empty:
        return {
            "samples": 0,
            "fidelity_score": 0.0,
            "action_agreement_rate": 0.0,
            "side_agreement_rate": 0.0,
            "runtime_execute_rate": 0.0,
            "proxy_execute_rate": 0.0,
            "label_confusion": {},
            "action_confusion": {},
        }
    samples = len(aligned)
    action_agree = aligned["runtime_action"].eq(aligned["proxy_action"])
    false_positive_mask = aligned["runtime_action"].eq("NONE") & aligned["proxy_action"].isin(
        ["BUY", "SELL"]
    )
    false_negative_mask = aligned["runtime_action"].isin(["BUY", "SELL"]) & aligned[
        "proxy_action"
    ].eq("NONE")
    both_execute = aligned["runtime_action"].isin(["BUY", "SELL"]) & aligned["proxy_action"].isin(
        ["BUY", "SELL"]
    )
    if bool(both_execute.any()):
        side_agreement_rate = float(
            aligned.loc[both_execute, "runtime_side"]
            .eq(aligned.loc[both_execute, "proxy_label"])
            .mean()
        )
        side_agreement_value: float | None = side_agreement_rate
        action_agreement_rate = float(action_agree.mean())
        fidelity_score = (action_agreement_rate * 0.7) + (side_agreement_rate * 0.3)
    else:
        side_agreement_value = None
        action_agreement_rate = float(action_agree.mean())
        fidelity_score = action_agreement_rate
    filter_reason = (
        aligned["filter_reason"]
        if "filter_reason" in aligned.columns
        else pd.Series([None] * len(aligned), index=aligned.index)
    )
    proxy_veto_reason = (
        aligned["proxy_veto_reason"]
        if "proxy_veto_reason" in aligned.columns
        else pd.Series([None] * len(aligned), index=aligned.index)
    )
    runtime_veto_buckets = filter_reason.loc[aligned["runtime_action"].eq("NONE")].map(
        _reason_bucket
    )
    runtime_prob = pd.to_numeric(
        aligned["prob_final"]
        if "prob_final" in aligned.columns
        else pd.Series([None] * len(aligned), index=aligned.index),
        errors="coerce",
    )
    proxy_false_positive_diagnostics: Counter[str] = Counter()
    for idx, row in aligned.loc[false_positive_mask].iterrows():
        runtime_side = str(row.get("runtime_side") or "").upper()
        proxy_side = str(row.get("proxy_action") or "").upper()
        if (
            runtime_side in {"BUY", "SELL"}
            and proxy_side in {"BUY", "SELL"}
            and runtime_side != proxy_side
        ):
            proxy_false_positive_diagnostics["DIRECTION_MISMATCH"] += 1
        prob = runtime_prob.loc[idx] if idx in runtime_prob.index else None
        if (
            prob is not None
            and not pd.isna(prob)
            and float(prob) < float(getattr(Config, "SHADOW_MODE_MIN", 55.0))
        ):
            proxy_false_positive_diagnostics["BELOW_SHADOW_THRESHOLD"] += 1
        reason = _reason_bucket(row.get("filter_reason"))
        if reason not in {"NO_REASON", "FILTER PASS (V118-PRO)", "HMM_RANGE_PENALTY"}:
            proxy_false_positive_diagnostics[f"RUNTIME_VETO_{reason}"] += 1
    modeled_proxy_veto_buckets = proxy_veto_reason.map(_reason_bucket)
    breadth_active = (
        aligned["proxy_market_breadth_fear_active"].fillna(False).astype(bool)
        if "proxy_market_breadth_fear_active" in aligned.columns
        else pd.Series([False] * len(aligned), index=aligned.index)
    )
    mtf_active = (
        aligned["proxy_mtf_veto_active"].fillna(False).astype(bool)
        if "proxy_mtf_veto_active" in aligned.columns
        else pd.Series([False] * len(aligned), index=aligned.index)
    )
    kava_active = (
        aligned["proxy_kava_veto_active"].fillna(False).astype(bool)
        if "proxy_kava_veto_active" in aligned.columns
        else pd.Series([False] * len(aligned), index=aligned.index)
    )
    proxy_raw_action = (
        aligned["proxy_raw_action"]
        if "proxy_raw_action" in aligned.columns
        else aligned["proxy_action"]
    )
    proxy_had_trade_intent = proxy_raw_action.isin(["BUY", "SELL"])
    exogenous_unmodeled = runtime_veto_buckets[
        aligned["proxy_action"].isin(["BUY", "SELL"])
        & proxy_had_trade_intent
        & (
            (runtime_veto_buckets.isin({"MARKET_BREADTH_FEAR"}) & ~breadth_active)
            | (runtime_veto_buckets.isin({"MTF_VETO"}) & ~mtf_active)
            | (runtime_veto_buckets.isin({"VETO_KAVA"}) & ~kava_active)
        )
    ]
    environment_reasons = {}
    if int(breadth_active.sum()) > 0:
        environment_reasons["MARKET_BREADTH_FEAR"] = int(breadth_active.sum())
    if int(mtf_active.sum()) > 0:
        environment_reasons["MTF_VETO"] = int(mtf_active.sum())
    if int(kava_active.sum()) > 0:
        environment_reasons["VETO_KAVA"] = int(kava_active.sum())
    return {
        "samples": int(samples),
        "fidelity_score": round(float(fidelity_score), 6),
        "action_agreement_rate": round(action_agreement_rate, 6),
        "side_agreement_rate": (
            round(side_agreement_value, 6) if side_agreement_value is not None else None
        ),
        "runtime_execute_rate": round(
            float(aligned["runtime_action"].isin(["BUY", "SELL"]).mean()), 6
        ),
        "proxy_execute_rate": round(float(aligned["proxy_action"].isin(["BUY", "SELL"]).mean()), 6),
        "label_confusion": _confusion(aligned, "runtime_label", "proxy_label"),
        "action_confusion": _confusion(aligned, "runtime_action", "proxy_action"),
        "false_positive_count": int(false_positive_mask.sum()),
        "false_negative_count": int(false_negative_mask.sum()),
        "proxy_false_positive_reasons": _counter_from_series(
            filter_reason.loc[false_positive_mask].map(_reason_bucket)
        ),
        "proxy_false_positive_diagnostics": dict(proxy_false_positive_diagnostics.most_common()),
        "runtime_veto_reasons": _counter_from_series(runtime_veto_buckets),
        "proxy_modeled_veto_reasons": _counter_from_series(
            modeled_proxy_veto_buckets[modeled_proxy_veto_buckets.ne("NO_REASON")]
        ),
        "proxy_modeled_veto_reason_components": _counter_from_multireason_series(proxy_veto_reason),
        "proxy_modeled_environment_reasons": environment_reasons,
        "exogenous_veto_reasons_not_modeled": _counter_from_series(exogenous_unmodeled),
    }


def run_fidelity_audit(
    *,
    events_path: Path,
    candles_path: Path,
    output_path: Path,
    params: BacktestParams,
    fidelity_params: FidelityParams,
) -> dict[str, Any]:
    events, event_load_stats = _load_jsonl_with_stats(events_path)
    candles = load_candles_csv(candles_path)
    runtime = extract_runtime_decisions(
        events,
        symbol=fidelity_params.symbol,
        limit=fidelity_params.limit,
    )
    market_breadth_fear_candles = _market_breadth_fear_candles(
        events,
        timeframe=fidelity_params.timeframe,
    )
    mtf_veto_candles = _mtf_veto_candles(
        events,
        symbol=fidelity_params.symbol,
        timeframe=fidelity_params.timeframe,
    )
    kava_veto_candles = _filter_reason_veto_candles(
        events,
        symbol=fidelity_params.symbol,
        timeframe=fidelity_params.timeframe,
        reason_bucket="VETO_KAVA",
    )
    proxy = _proxy_frame(
        candles,
        params,
        fidelity_params.strategy_mode,
        apply_shock_veto=fidelity_params.apply_shock_veto,
        shock_min_dist_pct=fidelity_params.shock_min_dist_pct,
        apply_market_breadth_veto=fidelity_params.apply_market_breadth_veto,
        market_breadth_fear_candles=market_breadth_fear_candles,
        apply_mtf_veto=fidelity_params.apply_mtf_veto,
        mtf_veto_candles=mtf_veto_candles,
        apply_kava_veto=fidelity_params.apply_kava_veto,
        kava_veto_candles=kava_veto_candles,
        timeframe=fidelity_params.timeframe,
    )
    aligned = align_runtime_to_proxy(
        runtime,
        proxy,
        timeframe=fidelity_params.timeframe,
        max_time_delta_seconds=fidelity_params.max_time_delta_seconds,
    )
    if fidelity_params.apply_runtime_confidence_gate:
        aligned = apply_runtime_confidence_gate(
            aligned,
            shadow_min_threshold=fidelity_params.shadow_min_threshold,
        )
    summary = summarize_fidelity(aligned)
    report = {
        "params": {
            "backtest": asdict(params),
            "fidelity": asdict(fidelity_params),
            "events_path": str(events_path),
            "candles_path": str(candles_path),
        },
        "summary": summary,
        "event_load_stats": event_load_stats,
        "aligned_samples": aligned.tail(50).to_dict(orient="records") if not aligned.empty else [],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime vs VectorBacktester fidelity audit")
    parser.add_argument("--events", default="logs/execution_events.jsonl")
    parser.add_argument("--candles", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", default="reports/fidelity_audit.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--max-time-delta-seconds", type=int, default=3900)
    parser.add_argument("--strategy-mode", default="mt_sr_regime")
    parser.add_argument("--z-score-threshold", type=float, default=1.6)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--stop-loss-pct", type=float, default=1.2)
    parser.add_argument("--take-profit-pct", type=float, default=2.0)
    parser.add_argument("--score-pass-threshold", type=float, default=0.80)
    parser.add_argument("--no-shock-veto", action="store_true")
    parser.add_argument("--no-market-breadth-veto", action="store_true")
    parser.add_argument("--no-mtf-veto", action="store_true")
    parser.add_argument("--no-kava-veto", action="store_true")
    parser.add_argument("--no-runtime-confidence-gate", action="store_true")
    parser.add_argument(
        "--shock-min-dist-pct",
        type=float,
        default=float(getattr(Config, "SHOCK_MIN_DIST_PCT", 0.4)),
    )
    args = parser.parse_args()

    params = BacktestParams(
        alma_offset=0.85,
        alma_sigma=6.0,
        z_score_threshold=args.z_score_threshold,
        entropy_bins=8,
        adx_threshold=args.adx_threshold,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
    )
    fidelity_params = FidelityParams(
        symbol=args.symbol,
        limit=args.limit,
        timeframe=args.timeframe,
        max_time_delta_seconds=args.max_time_delta_seconds,
        strategy_mode=args.strategy_mode,
        score_pass_threshold=args.score_pass_threshold,
        apply_shock_veto=not args.no_shock_veto,
        shock_min_dist_pct=args.shock_min_dist_pct,
        apply_market_breadth_veto=not args.no_market_breadth_veto,
        apply_mtf_veto=not args.no_mtf_veto,
        apply_kava_veto=not args.no_kava_veto,
        apply_runtime_confidence_gate=not args.no_runtime_confidence_gate,
        shadow_min_threshold=float(getattr(Config, "SHADOW_MODE_MIN", 55.0)),
    )
    report = run_fidelity_audit(
        events_path=Path(args.events),
        candles_path=Path(args.candles),
        output_path=Path(args.output),
        params=params,
        fidelity_params=fidelity_params,
    )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0 if float(report["summary"]["fidelity_score"]) >= args.score_pass_threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
