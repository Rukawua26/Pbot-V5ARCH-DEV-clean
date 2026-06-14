from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.learning_paths import DEFAULT_DB_PATH

from .collector import collect_runtime_dataset
from .loaders import DEFAULT_EVENTS_PATH, DEFAULT_STATE_PATH, load_trade_by_id, parse_iso_ts
from .storage import (
    fetch_trade_annotations,
    list_advisory_snapshots,
    save_advisory_snapshot,
    write_report_artifact,
)


def _blocked_reason_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter()
    for event in events:
        if event.get("event") in {"FILTER_APPLIED", "RANGE_VETO", "MTF_FILTER", "MARKOV_REGIME_DECISION"}:
            payload = event.get("payload") or {}
            if event.get("event") == "FILTER_APPLIED" and payload.get("filter_passed", True):
                continue
            reason = str(payload.get("filter_reason") or payload.get("reason") or event.get("event") or "UNKNOWN")
            counts[reason] += 1
    return [{"reason": key, "count": value} for key, value in counts.most_common(10)]


def build_daily_report(dataset: dict[str, Any]) -> dict[str, Any]:
    trades = dataset.get("trades") or []
    state = dataset.get("state") or {}
    shadow_closed = [trade for trade in trades if trade.get("is_shadow") and trade.get("pnl_percent") is not None]
    real_closed = [trade for trade in trades if not trade.get("is_shadow") and trade.get("pnl_percent") is not None]
    report = {
        "report_type": "daily",
        "generated_at": dataset.get("generated_at"),
        "window_hours": dataset.get("window_hours"),
        "state": {
            "mode": state.get("mode"),
            "regime": state.get("regime"),
            "sentiment": state.get("sentiment"),
            "halt_system_active": state.get("halt_system_active"),
            "circuit_breaker_active": state.get("circuit_breaker_active"),
        },
        "summary": dataset.get("summary") or {},
        "blocked_reason_counts": _blocked_reason_summary(dataset.get("events") or []),
        "shadow_closed": len(shadow_closed),
        "real_closed": len(real_closed),
        "research": dataset.get("research") or {},
    }
    report["summary_text"] = (
        f"Ventana {report['window_hours']}h | trades={report['summary'].get('trade_count', 0)} | "
        f"shadow={report['summary'].get('shadow_trade_count', 0)} | real={report['summary'].get('real_trade_count', 0)}"
    )
    return report


def build_weekly_report(dataset: dict[str, Any]) -> dict[str, Any]:
    report = build_daily_report(dataset)
    report["report_type"] = "weekly"
    report["window_hours"] = dataset.get("window_hours")
    research = report.get("research") or {}
    shadow_vs_real = research.get("shadow_vs_real") or {}
    report["focus"] = {
        "shadow_vs_real": shadow_vs_real,
        "top_clusters": (research.get("context_clusters") or {}).get("clusters", [])[:5],
        "top_veto_reasons": (research.get("veto_impact") or {}).get("reason_counts", [])[:5],
    }
    return report


def build_postmortem_report(
    trade_id: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    trade = load_trade_by_id(db_path, trade_id)
    if trade is None:
        return None
    annotations = fetch_trade_annotations(db_path, trade_id=trade_id, limit=10)
    pnl_pct = float(trade.pnl_percent or 0.0)
    severity = "high" if pnl_pct < -2.0 else "medium" if pnl_pct < 0 else "low"
    report = {
        "report_type": "postmortem",
        "generated_at": datetime.now(UTC).isoformat(),
        "trade": trade.to_dict(),
        "severity": severity,
        "mode": "SHADOW" if trade.is_shadow else "REAL",
        "narrative": (
            f"Trade {trade.id} {trade.symbol} {trade.side} terminó con {pnl_pct:+.2f}% "
            f"en régimen {trade.market_regime or 'UNKNOWN'}."
        ),
        "annotations": annotations,
        "advice": [],
    }
    if trade.is_shadow:
        report["advice"].append("El dato SHADOW sirve para calibración previa antes de mover reglas a REAL.")
    if pnl_pct < 0:
        report["advice"].append("Revisar veto reasons y contexto cercano para ver si hubo degradación evitable.")
    if trade.reason:
        report["advice"].append(f"Exit/close reason observada: {trade.reason}")
    return report


def build_advisories(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    advisories: list[dict[str, Any]] = []
    research = dataset.get("research") or {}
    shadow_vs_real = research.get("shadow_vs_real") or {}
    state = dataset.get("state") or {}
    top_veto = ((research.get("veto_impact") or {}).get("reason_counts") or [])[:3]
    if state.get("halt_system_active"):
        advisories.append(
            {
                "advisory_type": "runtime_status",
                "summary": "HALT activo: mantener inteligencia en modo observación y priorizar recovery manual.",
                "payload": {"halt_system_active": True, "mode": state.get("mode")},
                "filename": "runtime_status_advisory.json",
            }
        )
    if top_veto:
        advisories.append(
            {
                "advisory_type": "event_risk_watchlist",
                "summary": "Principales motivos de veto detectados en la ventana reciente.",
                "payload": {"top_veto_reasons": top_veto},
                "filename": "event_risk_watchlist.json",
            }
        )
    if shadow_vs_real:
        advisories.append(
            {
                "advisory_type": "shadow_real_gap",
                "summary": "Comparativa SHADOW vs REAL para calibración consultiva.",
                "payload": shadow_vs_real,
                "filename": "shadow_real_gap.json",
            }
        )
    return advisories


def build_and_store_advisories(
    dataset: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for advisory in build_advisories(dataset):
        artifact_path = write_report_artifact(advisory["filename"], advisory["payload"])
        save_advisory_snapshot(
            advisory["advisory_type"],
            advisory["summary"],
            advisory["payload"],
            artifact_path=artifact_path,
            db_path=db_path,
        )
        stored.append({
            "advisory_type": advisory["advisory_type"],
            "summary": advisory["summary"],
            "artifact_path": artifact_path,
            "payload": advisory["payload"],
        })
    return stored


def generate_full_intelligence_cycle(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    events_path: str | Path = DEFAULT_EVENTS_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> dict[str, Any]:
    daily_dataset = collect_runtime_dataset(db_path=db_path, events_path=events_path, state_path=state_path, hours=24)
    weekly_dataset = collect_runtime_dataset(db_path=db_path, events_path=events_path, state_path=state_path, hours=24 * 7)
    daily_report = build_daily_report(daily_dataset)
    weekly_report = build_weekly_report(weekly_dataset)
    daily_path = write_report_artifact("daily_report.json", daily_report)
    weekly_path = write_report_artifact("weekly_report.json", weekly_report)
    advisories = build_and_store_advisories(daily_dataset, db_path=db_path)
    return {
        "daily_report": daily_report,
        "daily_path": daily_path,
        "weekly_report": weekly_report,
        "weekly_path": weekly_path,
        "advisories": advisories,
    }


def read_report_artifact(name: str) -> dict[str, Any] | None:
    path = Path(__file__).resolve().parents[2] / "reports" / "intelligence" / name
    if not path.exists():
        return None
    try:
        return __import__("json").loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
