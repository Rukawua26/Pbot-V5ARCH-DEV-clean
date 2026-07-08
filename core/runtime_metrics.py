import json
import os
import time
from datetime import UTC, datetime

from core.config.portable_paths import get_log_path


def _disabled() -> bool:
    return str(os.getenv("SNIPER_DISABLE_FILE_TELEMETRY", "0")).strip() == "1"


def append_runtime_metric(metric: str, payload: dict) -> None:
    if _disabled():
        return
    try:
        path = get_log_path("runtime_metrics.jsonl")
        os.makedirs(path.parent, exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "metric": str(metric),
            "payload": payload or {},
        }
        max_bytes = int(os.getenv("RUNTIME_METRICS_MAX_BYTES", "5242880"))
        if os.path.exists(path) and os.path.getsize(path) >= max_bytes:
            os.replace(path, f"{path}.1")
        with open(path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


def record_exchange_call_metric(
    op_name: str,
    *,
    attempt: int,
    started_perf: float,
    ok: bool,
    error: Exception | None = None,
) -> None:
    append_runtime_metric(
        "exchange_call",
        {
            "op": str(op_name),
            "attempt": int(attempt),
            "ok": bool(ok),
            "latency_ms": round((time.perf_counter() - started_perf) * 1000.0, 3),
            "error_type": type(error).__name__ if error is not None else "",
            "error": str(error)[:180] if error is not None else "",
        },
    )
