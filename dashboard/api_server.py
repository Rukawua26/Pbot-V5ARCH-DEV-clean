import json
import hmac
import os
import time
import sqlite3
from collections import deque

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import threading

API_KEY = os.getenv("SNIPER_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "SNIPER_API_KEY no configurada. "
        "Establece una clave segura para el dashboard API."
    )
if len(API_KEY) < 16:
    raise RuntimeError("SNIPER_API_KEY debe tener al menos 16 caracteres.")

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_rate_limit_state: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()

def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        timestamps = _rate_limit_state.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(429, "Rate limit exceeded")
        timestamps.append(now)
        _rate_limit_state[client_ip] = timestamps

def rate_limited(req: Request):
    client_ip = req.client.host if req.client else "unknown"
    _check_rate_limit(client_ip)
STATE_FILE = "/dev/shm/sniper_state.json"
CMD_DIR = "/dev/shm/sniper_cmd"
LOG_FILE = "sniper.log"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sniper_brain.db")
EXEC_EVENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "execution_events.jsonl")
ALLOWED_ORIGINS = os.getenv("SNIPER_DASHBOARD_ORIGINS", "http://127.0.0.1:8000").split(",")
ALLOWED_COMMANDS = frozenset({"/pause", "/resume", "/panic", "/recover_halt"})

app = FastAPI(title="Sniper AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_headers=["*"],
    allow_methods=["*"],
)


class Command(BaseModel):
    action: str = Field(min_length=1, max_length=64)


def verify_key(req: Request):
    _check_rate_limit(req.client.host if req.client else "unknown")
    supplied = str(req.headers.get("X-API-Key") or "")
    if not hmac.compare_digest(supplied, API_KEY):
        raise HTTPException(401, "Unauthorized")


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        raise HTTPException(404, "Dashboard HTML not found")
    return FileResponse(path)


@app.get("/api/v1/health")
def health():
    if os.path.exists(STATE_FILE):
        age = time.time() - os.path.getmtime(STATE_FILE)
        return {
            "status": "ok",
            "state_age_s": round(age, 1),
            "alive": age < 30,
        }
    return {"status": "degraded", "state_age_s": None, "alive": False}


@app.get("/api/v1/state")
def get_state(_=Depends(verify_key)):
    if not os.path.exists(STATE_FILE):
        raise HTTPException(503, "State not available")
    with open(STATE_FILE) as f:
        data = json.load(f)
    data["state_age_s"] = round(time.time() - os.path.getmtime(STATE_FILE), 1)
    return data


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
def send_command(cmd: Command, _=Depends(verify_key)):
    action = cmd.action.strip()
    if action not in ALLOWED_COMMANDS:
        raise HTTPException(400, "Command not allowed")
    os.makedirs(CMD_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(CMD_DIR, "command.json")
    data = {"commands": [{"action": action, "ts": time.time()}]}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return {"ok": True, "action": action}


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/v1/trades")
def get_trades(limit: int = 100, type: str = "all", _=Depends(verify_key)):
    conn = _get_db()
    where = ""
    if type == "real":
        where = " WHERE is_shadow = 0"
    elif type == "shadow":
        where = " WHERE is_shadow = 1"
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM trades{where}"
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        trades = [{k: r[k] for k in r.keys()} for r in rows]
        return {"trades": trades, "total": total}
    finally:
        conn.close()


@app.get("/api/v1/blocked")
def get_blocked(limit: int = 100, _=Depends(verify_key)):
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
                events.append({
                    "ts": e["ts"],
                    "symbol": payload.get("symbol", ""),
                    "side": payload.get("side", ""),
                    "reason": payload.get("filter_reason", "UNKNOWN"),
                    "prob_final": payload.get("prob_final"),
                    "btc_regime": payload.get("btc_regime"),
                    "event_type": "FILTER",
                })
            elif ev == "RANGE_VETO":
                events.append({
                    "ts": e["ts"],
                    "symbol": payload.get("symbol", ""),
                    "side": payload.get("side", ""),
                    "reason": "RANGE_VETO",
                    "prob_final": None,
                    "btc_regime": payload.get("btc_regime"),
                    "event_type": "RANGE_VETO",
                })
            elif ev == "MTF_FILTER" and payload.get("reason", "").startswith("MTF_VETO"):
                events.append({
                    "ts": e["ts"],
                    "symbol": payload.get("symbol", ""),
                    "side": payload.get("side", ""),
                    "reason": payload.get("reason", "MTF_VETO"),
                    "prob_final": payload.get("prob_before"),
                    "btc_regime": None,
                    "event_type": "MTF_VETO",
                })
            elif ev == "MARKOV_REGIME_DECISION" and not payload.get("filter_passed", True):
                events.append({
                    "ts": e["ts"],
                    "symbol": payload.get("symbol", ""),
                    "side": payload.get("side", ""),
                    "reason": f"MARKOV_{payload.get('decision', 'VETO')}",
                    "prob_final": None,
                    "btc_regime": payload.get("btc_regime"),
                    "event_type": "MARKOV_VETO",
                })
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
            {"reason": k, "count": v}
            for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])
        ],
    }


@app.get("/api/v1/trade-stats")
def get_trade_stats(_=Depends(verify_key)):
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        winners = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0").fetchone()[0]
        losers = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl < 0").fetchone()[0]
        total_pnl_usd = conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades").fetchone()[0]
        total_win_pnl = conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE pnl > 0").fetchone()[0]
        total_loss_pnl = abs(conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE pnl < 0").fetchone()[0])
        profit_factor = round(total_win_pnl / total_loss_pnl, 2) if total_loss_pnl > 0 else total_win_pnl

        stats = {
            "total": total,
            "shadow": conn.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 1").fetchone()[0],
            "real": conn.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 0").fetchone()[0],
            "winners": winners,
            "losers": losers,
            "win_rate": round((winners / total * 100) if total > 0 else 0, 2),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "total_pnl_pct": round(
                conn.execute(
                    "SELECT COALESCE(AVG(pnl_percent), 0) FROM trades WHERE pnl IS NOT NULL"
                ).fetchone()[0],
                2,
            ),
            "avg_win": round(
                conn.execute("SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE pnl > 0").fetchone()[0], 4
            ),
            "avg_loss": round(
                conn.execute("SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE pnl < 0").fetchone()[0], 4
            ),
            "avg_win_pct": round(
                conn.execute(
                    "SELECT COALESCE(AVG(pnl_percent), 0) FROM trades WHERE pnl > 0"
                ).fetchone()[0],
                2,
            ),
            "avg_loss_pct": round(
                conn.execute(
                    "SELECT COALESCE(AVG(pnl_percent), 0) FROM trades WHERE pnl < 0"
                ).fetchone()[0],
                2,
            ),
            "best_trade_pct": round(
                conn.execute("SELECT COALESCE(MAX(pnl_percent), 0) FROM trades").fetchone()[0], 2
            ),
            "worst_trade_pct": round(
                conn.execute("SELECT COALESCE(MIN(pnl_percent), 0) FROM trades").fetchone()[0], 2
            ),
            "profit_factor": profit_factor,
        }
        reasons = conn.execute("""
            SELECT COALESCE(NULLIF(reason, ''), exit_reason, 'OTHER') as reason,
                   COUNT(*) as cnt,
                   COALESCE(AVG(pnl), 0) as avg_pnl,
                   COALESCE(AVG(pnl_percent), 0) as avg_pnl_pct,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses
            FROM trades
            GROUP BY reason ORDER BY cnt DESC
        """).fetchall()
        stats["exit_reasons"] = [
            {
                "reason": r[0],
                "count": r[1],
                "avg_pnl": round(r[2], 4),
                "avg_pnl_pct": round(r[3], 2),
                "wins": r[4],
                "losses": r[5],
            }
            for r in reasons
        ]
        return stats
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
def get_exec_events(
    event_type: str = "", event_limit: int = 200, _=Depends(verify_key)
):
    if not os.path.exists(EXEC_EVENTS_PATH):
        return {"events": [], "total": 0}
    matches = []
    with open(EXEC_EVENTS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not event_type or e.get("event") == event_type:
                matches.append(e)
    matches.reverse()
    matches = matches[:event_limit]
    return {"events": matches, "total": len(matches)}
