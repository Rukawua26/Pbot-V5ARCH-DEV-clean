from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.learning_paths import DEFAULT_DB_PATH

from .contracts import AdvisoryArtifact


ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports" / "intelligence"


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_intelligence_tables(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_context_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                symbol TEXT,
                regime TEXT,
                mode TEXT,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_context_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                mode TEXT NOT NULL,
                context_label TEXT NOT NULL,
                risk_label TEXT NOT NULL,
                narrative TEXT NOT NULL,
                origin TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(trade_id, origin)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS advisory_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                advisory_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                artifact_path TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def write_report_artifact(name: str, payload: dict[str, Any]) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def save_advisory_snapshot(
    advisory_type: str,
    summary: str,
    payload: dict[str, Any],
    *,
    artifact_path: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    ensure_intelligence_tables(db_path)
    conn = _connect(db_path)
    created_at = datetime.now(UTC).isoformat()
    try:
        cur = conn.execute(
            "INSERT INTO advisory_snapshots (advisory_type, created_at, summary, artifact_path, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (advisory_type, created_at, summary, artifact_path, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_advisory_snapshots(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    advisory_type: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_intelligence_tables(db_path)
    conn = _connect(db_path)
    try:
        if advisory_type:
            rows = conn.execute(
                "SELECT * FROM advisory_snapshots WHERE advisory_type = ? ORDER BY id DESC LIMIT ?",
                (advisory_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM advisory_snapshots ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        artifact = AdvisoryArtifact(
            advisory_type=str(row["advisory_type"]),
            created_at=str(row["created_at"]),
            summary=str(row["summary"]),
            payload=payload,
            artifact_path=row["artifact_path"],
        )
        item = artifact.to_dict()
        item["id"] = int(row["id"])
        out.append(item)
    return out


def upsert_trade_annotation(
    trade_id: int,
    symbol: str,
    mode: str,
    context_label: str,
    risk_label: str,
    narrative: str,
    payload: dict[str, Any],
    *,
    origin: str = "intelligence_v1",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    ensure_intelligence_tables(db_path)
    conn = _connect(db_path)
    created_at = datetime.now(UTC).isoformat()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO trade_context_annotations "
            "(trade_id, symbol, mode, context_label, risk_label, narrative, origin, created_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade_id,
                symbol,
                mode,
                context_label,
                risk_label,
                narrative,
                origin,
                created_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_trade_annotations(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    trade_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_intelligence_tables(db_path)
    conn = _connect(db_path)
    try:
        if trade_id is None:
            rows = conn.execute(
                "SELECT * FROM trade_context_annotations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_context_annotations WHERE trade_id = ? ORDER BY id DESC LIMIT ?",
                (trade_id, limit),
            ).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        items.append(
            {
                "id": int(row["id"]),
                "trade_id": int(row["trade_id"]),
                "symbol": str(row["symbol"]),
                "mode": str(row["mode"]),
                "context_label": str(row["context_label"]),
                "risk_label": str(row["risk_label"]),
                "narrative": str(row["narrative"]),
                "origin": str(row["origin"]),
                "created_at": str(row["created_at"]),
                "payload": payload,
            }
        )
    return items
