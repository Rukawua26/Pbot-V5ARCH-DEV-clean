from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ExecutionEventRecord:
    ts: str
    event: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StateSnapshotRecord:
    ts: float
    mode: str
    balance: float
    available_balance: float
    halt_system_active: bool
    circuit_breaker_active: bool
    active_trades_count: int
    shadow_trades_count: int
    regime: str
    sentiment: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TradeRecord:
    id: int
    symbol: str
    side: str
    timestamp: str
    open_time: str | None
    pnl: float | None
    pnl_percent: float | None
    reason: str | None
    is_shadow: bool
    market_regime: str | None
    entry_confidence: float | None
    exit_confidence: float | None
    mae_percent: float | None
    mfe_percent: float | None
    market_snapshot: str | None
    market_context: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AdvisoryArtifact:
    advisory_type: str
    created_at: str
    summary: str
    payload: dict[str, Any]
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
