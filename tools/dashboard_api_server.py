import hashlib
import hmac
import json
import os
import sqlite3
import stat
import threading
import time
from collections import deque

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.learning_paths import DEFAULT_DB_PATH
from tools.intelligence.report_builder import (
    build_postmortem_report,
    generate_full_intelligence_cycle,
    read_report_artifact,
)
from tools.intelligence.storage import (
    ensure_intelligence_tables,
    fetch_trade_annotations,
    list_advisory_snapshots,
)

API_KEY = os.getenv("SNIPER_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "SNIPER_API_KEY no configurada. Establece una clave segura para el dashboard API."
    )
if len(API_KEY) < 16:
    raise RuntimeError("SNIPER_API_KEY debe tener al menos 16 caracteres.")
CONTROL_API_KEY = os.getenv("SNIPER_CONTROL_API_KEY")
if CONTROL_API_KEY is not None and len(CONTROL_API_KEY) < 16:
    raise RuntimeError("SNIPER_CONTROL_API_KEY debe tener al menos 16 caracteres.")
READ_SESSION_COOKIE = "sniper_dashboard_read_session"
READ_SESSION_TOKEN = hmac.new(
    API_KEY.encode("utf-8"), b"sniper-dashboard-read-session-v1", hashlib.sha256
).hexdigest()
STATE_FILE = "/dev/shm/sniper_state.json"
CMD_DIR = "/dev/shm/sniper_cmd"
LOG_FILE = "sniper.log"
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "static")
DB_PATH = DEFAULT_DB_PATH
EXEC_EVENTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "execution_events.jsonl"
)
ALLOWED_ORIGINS = os.getenv("SNIPER_DASHBOARD_ORIGINS", "http://127.0.0.1:8000").split(",")
ALLOWED_COMMANDS = frozenset({"/pause", "/resume", "/panic", "/recover_halt"})
RATE_LIMIT_REQUESTS = int(os.getenv("SNIPER_DASHBOARD_RATE_LIMIT_REQUESTS", "240"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("SNIPER_DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", "60"))
MAX_BODY_BYTES = 64 * 1024
_rate_limit_state: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()

app = FastAPI(title="Sniper AI API")
ensure_intelligence_tables(DB_PATH)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_headers=["*"],
    allow_methods=["*"],
)


def _clamp_limit(value: int, default: int = 100, maximum: int = 500) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = default
    return max(1, min(raw, maximum))


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        timestamps = [
            ts
            for ts in _rate_limit_state.get(client_ip, [])
            if now - ts < RATE_LIMIT_WINDOW_SECONDS
        ]
        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(429, "Rate limit exceeded")
        timestamps.append(now)
        _rate_limit_state[client_ip] = timestamps


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    try:
        _check_rate_limit(client_ip)
    except HTTPException as error:
        return JSONResponse(
            {"detail": error.detail},
            status_code=error.status_code,
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )
    content_length = request.headers.get("content-length")
    try:
        body_size = int(content_length) if content_length else 0
    except ValueError:
        body_size = MAX_BODY_BYTES + 1
    if body_size > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class Command(BaseModel):
    action: str = Field(min_length=1, max_length=64)


def verify_key(req: Request):
    supplied = str(req.headers.get("X-API-Key") or "")
    cookie = str(req.cookies.get(READ_SESSION_COOKIE) or "")
    if not (
        hmac.compare_digest(supplied, API_KEY) or hmac.compare_digest(cookie, READ_SESSION_TOKEN)
    ):
        raise HTTPException(401, "Unauthorized")


def verify_control_key(req: Request):
    if not CONTROL_API_KEY:
        raise HTTPException(503, "Dashboard control API key not configured")
    supplied = str(req.headers.get("X-Control-API-Key") or "")
    if not hmac.compare_digest(supplied, CONTROL_API_KEY):
        raise HTTPException(401, "Unauthorized")


def _ensure_safe_cmd_dir() -> None:
    os.makedirs(CMD_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(CMD_DIR)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise HTTPException(500, "Unsafe command directory")
    if st.st_uid != os.getuid():
        raise HTTPException(500, "Unsafe command directory owner")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HTTPException(500, "Unsafe command directory permissions")


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        raise HTTPException(404, "Dashboard HTML not found")
    response = FileResponse(path)
    response.set_cookie(
        READ_SESSION_COOKIE,
        READ_SESSION_TOKEN,
        httponly=True,
        samesite="strict",
        max_age=12 * 60 * 60,
    )
    return response


@app.get("/api/v1/health")
def health():
    """Healthcheck avanzado: verifica freshness del snapshot, flags criticos y estado runtime.

    No requiere API key para que Docker/healthcheck pueda sondearlo sin credenciales.
    """
    if not os.path.exists(STATE_FILE):
        return JSONResponse(
            {
                "status": "unhealthy",
                "reason": "NO_SNAPSHOT",
                "state_age_s": None,
                "ws_reconciliation_in_progress": False,
                "halt_system_active": False,
            },
            status_code=503,
        )

    try:
        snapshot_ts = os.path.getmtime(STATE_FILE)
        state_age_s = round(time.time() - snapshot_ts, 1)
    except OSError:
        return JSONResponse(
            {
                "status": "unhealthy",
                "reason": "STAT_ERROR",
                "state_age_s": None,
            },
            status_code=503,
        )

    ws_flag = False
    halt_flag = False
    circuit_flag = False
    paused_flag = False
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
            ws_flag = bool(state.get("ws_reconciliation_in_progress", False))
            halt_flag = bool(state.get("halt_system_active", False))
            circuit_flag = bool(state.get("circuit_breaker_active", False))
            paused_flag = bool(state.get("is_paused", False))
    except (json.JSONDecodeError, OSError):
        return JSONResponse(
            {
                "status": "unhealthy",
                "reason": "SNAPSHOT_CORRUPT",
                "state_age_s": state_age_s,
            },
            status_code=503,
        )

    if state_age_s > 10.0:
        status = "unhealthy"
        reason = "STALE_SNAPSHOT"
    elif state_age_s > 3.0 or halt_flag or circuit_flag:
        status = "degraded"
        reason = "STALE_OR_PROTECTION_ACTIVE"
    else:
        status = "healthy"
        reason = "OK"

    return {
        "status": status,
        "reason": reason,
        "state_age_s": state_age_s,
        "ws_reconciliation_in_progress": ws_flag,
        "halt_system_active": halt_flag,
        "circuit_breaker_active": circuit_flag,
        "is_paused": paused_flag,
        "timestamp": time.time(),
    }


@app.get("/api/v1/state")
def get_state(_=Depends(verify_key)):
    if not os.path.exists(STATE_FILE):
        raise HTTPException(503, "State not available")
    with open(STATE_FILE) as f:
        data = json.load(f)
    data["state_age_s"] = round(time.time() - os.path.getmtime(STATE_FILE), 1)
    return data


@app.get("/api/v1/consensus")
def get_consensus(limit: int = 50, _=Depends(verify_key)):
    limit = _clamp_limit(limit, default=50, maximum=100)
    if not os.path.exists(STATE_FILE):
        raise HTTPException(503, "State not available")
    with open(STATE_FILE) as f:
        data = json.load(f)
    consensus = data.get("consensus") or {}
    rounds = list(consensus.get("rounds") or [])[:limit]
    risk_summary = consensus.get("risk_summary") or {
        "halt_active": bool(data.get("halt_system_active", False)),
        "integrity_lock": bool(data.get("integrity_lock_active", False)),
        "circuit_breaker": bool(data.get("circuit_breaker_active", False)),
        "paused": bool(data.get("is_paused", False)),
        "ws_reconciliation_in_progress": bool(data.get("ws_reconciliation_in_progress", False)),
    }
    return {
        "latest": rounds[0] if rounds else None,
        "rounds": rounds,
        "total": len(rounds),
        "risk_summary": risk_summary,
        "state_age_s": round(time.time() - os.path.getmtime(STATE_FILE), 1),
    }


@app.get("/api/v1/logs")
def get_logs(lines: int = 50, _=Depends(verify_key)):
    lines = max(1, min(int(lines), 500))
    try:
        if not os.path.exists(LOG_FILE):
            return {"lines": []}
        with open(LOG_FILE, encoding="utf-8", errors="replace") as handle:
            return {"lines": [line.rstrip("\n") for line in deque(handle, maxlen=lines)]}
    except Exception as e:
        raise HTTPException(502, f"Log tail failed: {e}")


@app.post("/api/v1/command")
def send_command(cmd: Command, _=Depends(verify_control_key)):
    action = cmd.action.strip()
    if action not in ALLOWED_COMMANDS:
        raise HTTPException(400, "Command not allowed")
    _ensure_safe_cmd_dir()
    path = os.path.join(CMD_DIR, "command.json")
    data = {"commands": [{"action": action, "ts": time.time()}]}
    tmp = os.path.join(CMD_DIR, f"command.json.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return {"ok": True, "action": action}


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/v1/trades")
def get_trades(limit: int = 100, type: str = "all", _=Depends(verify_key)):
    limit = _clamp_limit(limit)
    conn = _get_db()
    try:
        where_clause = ""
        params: tuple = ()
        if type == "real":
            where_clause = " WHERE is_shadow = ?"
            params = (0,)
        elif type == "shadow":
            where_clause = " WHERE is_shadow = ?"
            params = (1,)
        total = conn.execute(f"SELECT COUNT(*) FROM trades{where_clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM trades{where_clause} ORDER BY timestamp DESC LIMIT ?",
            params + (limit,),
        ).fetchall()
        trades = [{k: r[k] for k in r.keys()} for r in rows]
        return {"trades": trades, "total": total}
    finally:
        conn.close()


@app.get("/api/v1/blocked")
def get_blocked(limit: int = 100, _=Depends(verify_key)):
    limit = _clamp_limit(limit)
    events = []
    if not os.path.exists(EXEC_EVENTS_PATH):
        return {"blocked": [], "total": 0}
    with open(EXEC_EVENTS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = e.get("event", "")
            payload = e.get("payload", {})
            if ev == "FILTER_APPLIED" and not payload.get("filter_passed", True):
                events.append(
                    {
                        "ts": e["ts"],
                        "symbol": payload.get("symbol", ""),
                        "side": payload.get("side", ""),
                        "reason": payload.get("filter_reason", "UNKNOWN"),
                        "prob_final": payload.get("prob_final"),
                        "btc_regime": payload.get("btc_regime"),
                        "event_type": "FILTER",
                    }
                )
            elif ev == "RANGE_VETO":
                events.append(
                    {
                        "ts": e["ts"],
                        "symbol": payload.get("symbol", ""),
                        "side": payload.get("side", ""),
                        "reason": "RANGE_VETO",
                        "prob_final": None,
                        "btc_regime": payload.get("btc_regime"),
                        "event_type": "RANGE_VETO",
                    }
                )
            elif ev == "MTF_FILTER" and payload.get("reason", "").startswith("MTF_VETO"):
                events.append(
                    {
                        "ts": e["ts"],
                        "symbol": payload.get("symbol", ""),
                        "side": payload.get("side", ""),
                        "reason": payload.get("reason", "MTF_VETO"),
                        "prob_final": payload.get("prob_before"),
                        "btc_regime": None,
                        "event_type": "MTF_VETO",
                    }
                )
            elif ev == "MARKOV_REGIME_DECISION" and not payload.get("filter_passed", True):
                events.append(
                    {
                        "ts": e["ts"],
                        "symbol": payload.get("symbol", ""),
                        "side": payload.get("side", ""),
                        "reason": f"MARKOV_{payload.get('decision', 'VETO')}",
                        "prob_final": None,
                        "btc_regime": payload.get("btc_regime"),
                        "event_type": "MARKOV_VETO",
                    }
                )
    events.reverse()
    events = events[:limit]

    reason_counts = {}
    for ev in events:
        r = ev["reason"]
        reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "blocked": events,
        "total": len(events),
        "reason_counts": [
            {"reason": k, "count": v} for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])
        ],
    }


@app.get("/api/v1/trade-stats")
def get_trade_stats(_=Depends(verify_key)):
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl < 0").fetchone()[0]
        shadow = conn.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 1").fetchone()[0]
        real = conn.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 0").fetchone()[0]
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / total * 100), 1) if total > 0 else 0.0,
            "shadow": shadow,
            "real": real,
        }
    finally:
        conn.close()


@app.get("/api/v1/equity")
def get_equity(_=Depends(verify_key)):
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT timestamp, balance FROM equity_history ORDER BY timestamp ASC"
        ).fetchall()
        return {"points": [{"ts": r[0], "balance": r[1]} for r in rows]}
    finally:
        conn.close()


@app.get("/api/v1/exec-events")
def get_exec_events(event_type: str = "", event_limit: int = 200, _=Depends(verify_key)):
    event_limit = _clamp_limit(event_limit, default=200)
    if not os.path.exists(EXEC_EVENTS_PATH):
        return {"events": [], "total": 0}
    matches = []
    with open(EXEC_EVENTS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
                if not event_type or e.get("event") == event_type:
                    matches.append(e)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    matches.reverse()
    return {"events": matches[:event_limit], "total": len(matches)}


@app.get("/api/v1/intelligence/daily")
def get_intelligence_daily(_=Depends(verify_key)):
    report = read_report_artifact("daily_report.json")
    if report is None:
        raise HTTPException(404, "Daily intelligence report not available")
    return report


@app.get("/api/v1/intelligence/weekly")
def get_intelligence_weekly(_=Depends(verify_key)):
    report = read_report_artifact("weekly_report.json")
    if report is None:
        raise HTTPException(404, "Weekly intelligence report not available")
    return report


@app.get("/api/v1/intelligence/advisories")
def get_intelligence_advisories(advisory_type: str = "", limit: int = 20, _=Depends(verify_key)):
    limit = _clamp_limit(limit, default=20)
    advisories = list_advisory_snapshots(DB_PATH, advisory_type=advisory_type, limit=limit)
    return {"advisories": advisories, "total": len(advisories)}


@app.get("/api/v1/intelligence/annotations")
def get_intelligence_annotations(
    trade_id: int | None = None, limit: int = 50, _=Depends(verify_key)
):
    limit = _clamp_limit(limit, default=50)
    annotations = fetch_trade_annotations(DB_PATH, trade_id=trade_id, limit=limit)
    return {"annotations": annotations, "total": len(annotations)}


@app.get("/api/v1/intelligence/postmortem/{trade_id}")
def get_intelligence_postmortem(trade_id: int, _=Depends(verify_key)):
    report = build_postmortem_report(trade_id, db_path=DB_PATH)
    if report is None:
        raise HTTPException(404, "Trade not found")
    return report


@app.post("/api/v1/intelligence/generate")
def generate_intelligence(_=Depends(verify_key)):
    result = generate_full_intelligence_cycle(db_path=DB_PATH)
    return {
        "ok": True,
        "daily_path": result.get("daily_path"),
        "weekly_path": result.get("weekly_path"),
        "advisories": len(result.get("advisories") or []),
    }
