from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ThresholdSpec:
    name: str
    value: float | int
    rationale: str
    last_updated: str
    owner: str
    min_value: float | int | None = None
    max_value: float | int | None = None
    unit: str = "raw"


THRESHOLDS: dict[str, ThresholdSpec] = {
    "CORRELATION_VETO_WINDOW": ThresholdSpec(
        name="CORRELATION_VETO_WINDOW",
        value=30,
        rationale="Reduce false correlation vetoes from short noisy windows and require more persistent alignment between agents.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=5,
        max_value=500,
        unit="votes",
    ),
    "CORRELATION_VETO_ABS_MIN": ThresholdSpec(
        name="CORRELATION_VETO_ABS_MIN",
        value=0.90,
        rationale="Only veto when agent correlation is extreme enough to indicate redundancy rather than regime alignment noise.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0.5,
        max_value=0.999,
        unit="correlation",
    ),
    "CONSENSUS_NN_CONFIDENCE_MIN": ThresholdSpec(
        name="CONSENSUS_NN_CONFIDENCE_MIN",
        value=0.40,
        rationale="Neural consensus should influence final probability only when confidence exceeds a minimum useful threshold.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0.0,
        max_value=1.0,
        unit="probability",
    ),
    "SHADOW_GATE_MIN_RUNTIME_SAMPLES": ThresholdSpec(
        name="SHADOW_GATE_MIN_RUNTIME_SAMPLES",
        value=30,
        rationale="Require enough runtime samples to avoid promoting from a trivially short shadow session.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=1,
        max_value=100000,
        unit="samples",
    ),
    "SHADOW_GATE_MIN_FILLED_ORDERS": ThresholdSpec(
        name="SHADOW_GATE_MIN_FILLED_ORDERS",
        value=10,
        rationale="Promotion requires a minimum amount of filled trades to avoid false confidence from inactivity.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=0,
        max_value=100000,
        unit="orders",
    ),
    "SHADOW_GATE_MAX_ACK_UNKNOWN_RATE": ThresholdSpec(
        name="SHADOW_GATE_MAX_ACK_UNKNOWN_RATE",
        value=0.02,
        rationale="Ambiguous acknowledgements must stay rare before considering promotion beyond shadow.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=0.0,
        max_value=1.0,
        unit="ratio",
    ),
    "SHADOW_GATE_MAX_RSS_MB": ThresholdSpec(
        name="SHADOW_GATE_MAX_RSS_MB",
        value=800.0,
        rationale="Shadow validation should fail if memory usage suggests instability or leaks under prolonged runtime.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=50.0,
        max_value=5000.0,
        unit="mb",
    ),
    "SHADOW_GATE_MAX_CPU_PCT": ThresholdSpec(
        name="SHADOW_GATE_MAX_CPU_PCT",
        value=95.0,
        rationale="Runtime should not saturate CPU persistently during a healthy validation session.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=1.0,
        max_value=100.0,
        unit="percent",
    ),
    "SHADOW_GATE_MAX_GUARDIAN_BUSY_PCT": ThresholdSpec(
        name="SHADOW_GATE_MAX_GUARDIAN_BUSY_PCT",
        value=95.0,
        rationale="Guardian should retain scheduling headroom; near-constant busy loops indicate operational stress.",
        last_updated="2026-05-10",
        owner="ops_phase_shadow",
        min_value=1.0,
        max_value=100.0,
        unit="percent",
    ),
    "STRATEGY_GATE_MIN_PROFIT_FACTOR": ThresholdSpec(
        name="STRATEGY_GATE_MIN_PROFIT_FACTOR",
        value=1.2,
        rationale="Candidate strategy must clear a minimum PF barrier before promotion review.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0.0,
        max_value=10.0,
        unit="ratio",
    ),
    "STRATEGY_GATE_MAX_DRAWDOWN": ThresholdSpec(
        name="STRATEGY_GATE_MAX_DRAWDOWN",
        value=0.20,
        rationale="Validation drawdown must remain inside a conservative bound before escalation.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0.0,
        max_value=1.0,
        unit="ratio",
    ),
    "STRATEGY_GATE_MIN_POSITIVE_WINDOWS_RATIO": ThresholdSpec(
        name="STRATEGY_GATE_MIN_POSITIVE_WINDOWS_RATIO",
        value=0.50,
        rationale="At least half of validation windows should remain positive to reject unstable parameter luck.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0.0,
        max_value=1.0,
        unit="ratio",
    ),
    "STRATEGY_GATE_MIN_CANDIDATE_DELTA_PF": ThresholdSpec(
        name="STRATEGY_GATE_MIN_CANDIDATE_DELTA_PF",
        value=0.0,
        rationale="Candidate must not degrade profit factor versus the chosen baseline.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=-10.0,
        max_value=10.0,
        unit="delta_pf",
    ),
    "STRATEGY_GATE_MIN_CANDIDATE_DELTA_RETURN_PCT": ThresholdSpec(
        name="STRATEGY_GATE_MIN_CANDIDATE_DELTA_RETURN_PCT",
        value=0.0,
        rationale="Candidate should not underperform the baseline in net return unless explicitly tolerated.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=-1000.0,
        max_value=1000.0,
        unit="percent",
    ),
    "STRATEGY_GATE_MIN_REGIME_TRADES": ThresholdSpec(
        name="STRATEGY_GATE_MIN_REGIME_TRADES",
        value=10,
        rationale="Ignore regime scorecard conclusions from statistically trivial sample counts.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=0,
        max_value=100000,
        unit="trades",
    ),
    "STRATEGY_GATE_MIN_REGIME_EXPECTANCY_PCT": ThresholdSpec(
        name="STRATEGY_GATE_MIN_REGIME_EXPECTANCY_PCT",
        value=0.0,
        rationale="Regimes with enough trades should not show negative expectancy when used for promotion decisions.",
        last_updated="2026-05-10",
        owner="strategy_phase_1",
        min_value=-100.0,
        max_value=100.0,
        unit="percent",
    ),
    "STRATEGY_GATE_MIN_FIDELITY_SCORE": ThresholdSpec(
        name="STRATEGY_GATE_MIN_FIDELITY_SCORE",
        value=0.80,
        rationale="Runtime-vs-proxy fidelity must be high enough before using backtest reports as promotion evidence.",
        last_updated="2026-05-11",
        owner="strategy_phase_fidelity",
        min_value=0.0,
        max_value=1.0,
        unit="ratio",
    ),
    "STRATEGY_GATE_MIN_FIDELITY_SAMPLES": ThresholdSpec(
        name="STRATEGY_GATE_MIN_FIDELITY_SAMPLES",
        value=20,
        rationale="Fidelity audit should include enough aligned runtime samples to avoid a trivial pass.",
        last_updated="2026-05-11",
        owner="strategy_phase_fidelity",
        min_value=0,
        max_value=100000,
        unit="samples",
    ),
    "PROMOTION_GATE_MAX_HALT_ACTIONS": ThresholdSpec(
        name="PROMOTION_GATE_MAX_HALT_ACTIONS",
        value=0,
        rationale="A promotion candidate should not require hard halts during the evaluated shadow window.",
        last_updated="2026-05-10",
        owner="ops_phase_promotion",
        min_value=0,
        max_value=100000,
        unit="events",
    ),
    "PROMOTION_GATE_MAX_QUARANTINE_ACTIONS": ThresholdSpec(
        name="PROMOTION_GATE_MAX_QUARANTINE_ACTIONS",
        value=3,
        rationale="A small number of quarantines can be tolerated; frequent quarantines indicate unstable execution quality.",
        last_updated="2026-05-10",
        owner="ops_phase_promotion",
        min_value=0,
        max_value=100000,
        unit="events",
    ),
    "PROMOTION_GATE_MAX_RISK_DECISION_PER_INTENT": ThresholdSpec(
        name="PROMOTION_GATE_MAX_RISK_DECISION_PER_INTENT",
        value=2.0,
        rationale="Too many risk decisions per intent suggests the bot survives mostly by vetoing rather than executing stable edge.",
        last_updated="2026-05-10",
        owner="ops_phase_promotion",
        min_value=0.0,
        max_value=100.0,
        unit="ratio",
    ),
}


def get_threshold(name: str) -> ThresholdSpec:
    try:
        return THRESHOLDS[name]
    except KeyError as error:
        raise KeyError(f"Unknown threshold: {name}") from error


def threshold_value(name: str):
    return get_threshold(name).value


def validate_thresholds() -> list[str]:
    failures: list[str] = []
    for name, spec in THRESHOLDS.items():
        value = spec.value
        if spec.min_value is not None and value < spec.min_value:
            failures.append(f"{name}={value!r} < min_value={spec.min_value!r}")
        if spec.max_value is not None and value > spec.max_value:
            failures.append(f"{name}={value!r} > max_value={spec.max_value!r}")
        if not spec.rationale.strip():
            failures.append(f"{name} missing rationale")
        if not spec.owner.strip():
            failures.append(f"{name} missing owner")
        if not spec.last_updated.strip():
            failures.append(f"{name} missing last_updated")
    return failures


def export_thresholds() -> list[dict[str, Any]]:
    return [asdict(spec) for spec in THRESHOLDS.values()]
