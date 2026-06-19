from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.learning_paths import DEFAULT_DB_PATH

from .contracts import ExecutionEventRecord, StateSnapshotRecord, TradeRecord

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = Path("/dev/shm/sniper_state.json")
DEFAULT_EVENTS_PATH = ROOT_DIR / "logs" / "execution_events.jsonl"


def parse_iso_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def load_state_snapshot(path: str | Path = DEFAULT_STATE_PATH) -> StateSnapshotRecord | None:
    state_path = Path(path)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return StateSnapshotRecord(
            ts=float(data.get("ts", 0.0) or 0.0),
            mode=str(data.get("mode", "UNKNOWN") or "UNKNOWN"),
            balance=float(data.get("balance", 0.0) or 0.0),
            available_balance=float(data.get("available_balance", 0.0) or 0.0),
            halt_system_active=bool(data.get("halt_system_active", False)),
            circuit_breaker_active=bool(data.get("circuit_breaker_active", False)),
            active_trades_count=int(data.get("active_trades_count", 0) or 0),
            shadow_trades_count=int(data.get("shadow_trades_count", 0) or 0),
            regime=str(data.get("regime", "N/A") or "N/A"),
            sentiment=str(data.get("sentiment", "NEUTRAL") or "NEUTRAL"),
            raw=data,
        )
    except (TypeError, ValueError):
        return None


def load_execution_events(
    path: str | Path = DEFAULT_EVENTS_PATH,
    *,
    limit: int | None = 500,
    event_type: str = "",
    since: datetime | None = None,
) -> list[ExecutionEventRecord]:
    events_path = Path(path)
    if not events_path.exists():
        return []
    records: list[ExecutionEventRecord] = []
    with events_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and str(row.get("event") or "") != event_type:
                continue
            ts = parse_iso_ts(row.get("ts"))
            if since is not None and (ts is None or ts < since):
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            records.append(
                ExecutionEventRecord(
                    ts=str(row.get("ts") or ""),
                    event=str(row.get("event") or ""),
                    payload=payload,
                )
            )
    if limit is not None and limit >= 0:
        return records[-limit:]
    return records


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def load_trades(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    limit: int = 500,
    since: datetime | None = None,
) -> list[TradeRecord]:
    query = (
        "SELECT id, symbol, side, timestamp, open_time, pnl, pnl_percent, reason, is_shadow, "
        "market_regime, entry_confidence, exit_confidence, mae_percent, mfe_percent, "
        "market_snapshot, market_context FROM trades ORDER BY timestamp DESC LIMIT ?"
    )
    conn = _connect(db_path)
    try:
        rows = conn.execute(query, (limit,)).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out: list[TradeRecord] = []
    for row in rows:
        trade = TradeRecord(
            id=int(row["id"]),
            symbol=str(row["symbol"] or ""),
            side=str(row["side"] or ""),
            timestamp=str(row["timestamp"] or ""),
            open_time=row["open_time"],
            pnl=row["pnl"],
            pnl_percent=row["pnl_percent"],
            reason=row["reason"],
            is_shadow=bool(row["is_shadow"]),
            market_regime=row["market_regime"],
            entry_confidence=row["entry_confidence"],
            exit_confidence=row["exit_confidence"],
            mae_percent=row["mae_percent"],
            mfe_percent=row["mfe_percent"],
            market_snapshot=row["market_snapshot"],
            market_context=row["market_context"],
        )
        trade_ts = parse_iso_ts(trade.timestamp)
        if since is not None and (trade_ts is None or trade_ts < since):
            continue
        out.append(trade)
    return out


def load_trade_by_id(db_path: str | Path, trade_id: int) -> TradeRecord | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, symbol, side, timestamp, open_time, pnl, pnl_percent, reason, is_shadow, "
            "market_regime, entry_confidence, exit_confidence, mae_percent, mfe_percent, "
            "market_snapshot, market_context FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return TradeRecord(
        id=int(row["id"]),
        symbol=str(row["symbol"] or ""),
        side=str(row["side"] or ""),
        timestamp=str(row["timestamp"] or ""),
        open_time=row["open_time"],
        pnl=row["pnl"],
        pnl_percent=row["pnl_percent"],
        reason=row["reason"],
        is_shadow=bool(row["is_shadow"]),
        market_regime=row["market_regime"],
        entry_confidence=row["entry_confidence"],
        exit_confidence=row["exit_confidence"],
        mae_percent=row["mae_percent"],
        mfe_percent=row["mfe_percent"],
        market_snapshot=row["market_snapshot"],
        market_context=row["market_context"],
    )


def load_trade_context_snapshots(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, symbol, side, pnl_percent, is_winner, entry_timestamp, exit_timestamp, "
            "is_shadow, context_json FROM trade_context_snapshots ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(row["context_json"]) if row["context_json"] else {}
        except (TypeError, json.JSONDecodeError):
            payload = {}
        snapshots.append(
            {
                "id": int(row["id"]),
                "symbol": row["symbol"],
                "side": row["side"],
                "pnl_percent": row["pnl_percent"],
                "is_winner": row["is_winner"],
                "entry_timestamp": row["entry_timestamp"],
                "exit_timestamp": row["exit_timestamp"],
                "is_shadow": bool(row["is_shadow"]),
                "context": payload,
            }
        )
    return snapshots


def window_start(hours: int) -> datetime:
    return utc_now() - timedelta(hours=hours)
