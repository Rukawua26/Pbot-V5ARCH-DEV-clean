#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config.thresholds import threshold_value
from tools.ablation_backtest import run_ablation_backtest
from tools.gate_history import append_gate_result
from tools.regime_scorecard import compute_regime_scorecard
from tools.walk_forward_backtest import (
    BacktestParams,
    load_candles_csv,
    run_walk_forward_backtest,
)


def evaluate_strategy_report(
    walk_forward: dict,
    ablation: dict,
    regime_rows: list[dict],
    fidelity: dict | None = None,
    *,
    min_profit_factor: float,
    max_drawdown: float,
    min_positive_windows_ratio: float,
    min_candidate_delta_pf: float,
    min_candidate_delta_return_pct: float,
    min_regime_trades: int,
    min_regime_expectancy_pct: float,
    min_fidelity_score: float = 0.0,
    min_fidelity_samples: int = 0,
) -> dict:
    failures: list[str] = []

    summary = (walk_forward or {}).get("summary") or {}
    windows = int(summary.get("windows", 0) or 0)
    positive_windows = int(summary.get("positive_validation_windows", 0) or 0)
    avg_pf = float(summary.get("avg_validation_profit_factor", 0.0) or 0.0)
    max_dd = float(summary.get("max_validation_drawdown", 1.0) or 1.0)
    total_val_trades = int(summary.get("total_validation_trades", 0) or 0)
    positive_ratio = (positive_windows / windows) if windows > 0 else 0.0

    if windows <= 0:
        failures.append("walk_forward.windows == 0")
    if total_val_trades <= 0:
        failures.append("walk_forward.total_validation_trades == 0")
    if avg_pf < min_profit_factor:
        failures.append(
            f"walk_forward.avg_validation_profit_factor {avg_pf:.4f} < {min_profit_factor:.4f}"
        )
    if max_dd > max_drawdown:
        failures.append(
            f"walk_forward.max_validation_drawdown {max_dd:.4f} > {max_drawdown:.4f}"
        )
    if positive_ratio < min_positive_windows_ratio:
        failures.append(
            f"walk_forward.positive_window_ratio {positive_ratio:.4f} < {min_positive_windows_ratio:.4f}"
        )

    candidate = (ablation or {}).get("candidate") or {}
    delta = candidate.get("delta_vs_baseline") or {}
    delta_pf = float(delta.get("profit_factor", 0.0) or 0.0)
    delta_ret = float(delta.get("net_return_pct", 0.0) or 0.0)
    if delta_pf < min_candidate_delta_pf:
        failures.append(
            f"ablation.candidate.delta_vs_baseline.profit_factor {delta_pf:.4f} < {min_candidate_delta_pf:.4f}"
        )
    if delta_ret < min_candidate_delta_return_pct:
        failures.append(
            f"ablation.candidate.delta_vs_baseline.net_return_pct {delta_ret:.4f} < {min_candidate_delta_return_pct:.4f}"
        )

    scored_regimes = [row for row in regime_rows if int(row.get("trades", 0) or 0) >= min_regime_trades]
    if not scored_regimes:
        failures.append(
            f"regime_scorecard has no regimes with trades >= {min_regime_trades}"
        )
    else:
        bad_regimes = [
            row for row in scored_regimes
            if float(row.get("expectancy_pct", 0.0) or 0.0) < min_regime_expectancy_pct
        ]
        for row in bad_regimes:
            failures.append(
                f"regime {row.get('regime')} expectancy {float(row.get('expectancy_pct', 0.0)):.4f} < {min_regime_expectancy_pct:.4f}"
            )

    fidelity_summary = (fidelity or {}).get("summary") or {}
    fidelity_score = float(fidelity_summary.get("weighted_fidelity_score", 0.0) or 0.0)
    fidelity_samples = int(fidelity_summary.get("total_samples", 0) or 0)
    if fidelity is not None:
        if fidelity_samples < min_fidelity_samples:
            failures.append(
                f"fidelity.total_samples {fidelity_samples} < {min_fidelity_samples}"
            )
        if fidelity_score < min_fidelity_score:
            failures.append(
                f"fidelity.weighted_fidelity_score {fidelity_score:.4f} < {min_fidelity_score:.4f}"
            )

    return {
        "passed": not failures,
        "failures": failures,
        "metrics": {
            "walk_forward_positive_window_ratio": positive_ratio,
            "walk_forward_avg_profit_factor": avg_pf,
            "walk_forward_max_drawdown": max_dd,
            "candidate_delta_profit_factor": delta_pf,
            "candidate_delta_net_return_pct": delta_ret,
            "scored_regimes": len(scored_regimes),
            "fidelity_weighted_score": fidelity_score,
            "fidelity_samples": fidelity_samples,
        },
    }


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Combined strategy validation report")
    parser.add_argument("--candles", required=True)
    parser.add_argument("--db", default="sniper_brain.db")
    parser.add_argument("--output", default="reports/strategy_validation_report.json")
    parser.add_argument("--fidelity-report", default="")
    parser.add_argument(
        "--min-fidelity-score",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_FIDELITY_SCORE")),
    )
    parser.add_argument(
        "--min-fidelity-samples",
        type=int,
        default=int(threshold_value("STRATEGY_GATE_MIN_FIDELITY_SAMPLES")),
    )
    parser.add_argument("--baseline-mode", default="equal_weight")
    parser.add_argument("--candidate-mode", default="mt_sr_regime")
    parser.add_argument("--z-score-threshold", type=float, default=1.6)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--stop-loss-pct", type=float, default=1.2)
    parser.add_argument("--take-profit-pct", type=float, default=2.0)
    parser.add_argument(
        "--min-profit-factor",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_PROFIT_FACTOR")),
    )
    parser.add_argument(
        "--max-drawdown",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MAX_DRAWDOWN")),
    )
    parser.add_argument(
        "--min-positive-windows-ratio",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_POSITIVE_WINDOWS_RATIO")),
    )
    parser.add_argument(
        "--min-candidate-delta-pf",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_CANDIDATE_DELTA_PF")),
    )
    parser.add_argument(
        "--min-candidate-delta-return-pct",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_CANDIDATE_DELTA_RETURN_PCT")),
    )
    parser.add_argument(
        "--min-regime-trades",
        type=int,
        default=int(threshold_value("STRATEGY_GATE_MIN_REGIME_TRADES")),
    )
    parser.add_argument(
        "--min-regime-expectancy-pct",
        type=float,
        default=float(threshold_value("STRATEGY_GATE_MIN_REGIME_EXPECTANCY_PCT")),
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

    candles = load_candles_csv(Path(args.candles))
    walk_forward = run_walk_forward_backtest(candles)
    ablation = run_ablation_backtest(
        candles,
        params,
        baseline_mode=args.baseline_mode,
        candidate_mode=args.candidate_mode,
    )

    regime_rows = []
    db_path = Path(args.db)
    if db_path.exists():
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            regime_rows = [asdict(row) for row in compute_regime_scorecard(conn, "1=1")]
        finally:
            conn.close()

    fidelity = None
    if args.fidelity_report:
        fidelity_path = Path(args.fidelity_report)
        if fidelity_path.exists():
            fidelity = json.loads(fidelity_path.read_text(encoding="utf-8"))
        else:
            fidelity = {"summary": {"weighted_fidelity_score": 0.0, "total_samples": 0}}

    verdict = evaluate_strategy_report(
        walk_forward,
        ablation,
        regime_rows,
        fidelity,
        min_profit_factor=args.min_profit_factor,
        max_drawdown=args.max_drawdown,
        min_positive_windows_ratio=args.min_positive_windows_ratio,
        min_candidate_delta_pf=args.min_candidate_delta_pf,
        min_candidate_delta_return_pct=args.min_candidate_delta_return_pct,
        min_regime_trades=args.min_regime_trades,
        min_regime_expectancy_pct=args.min_regime_expectancy_pct,
        min_fidelity_score=args.min_fidelity_score,
        min_fidelity_samples=args.min_fidelity_samples,
    )

    report = {
        "params": asdict(params),
        "walk_forward": walk_forward,
        "ablation": ablation,
        "regime_scorecard": regime_rows,
        "fidelity": fidelity,
        "verdict": verdict,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "walk_forward": walk_forward["summary"],
                "candidate": ablation["candidate"],
                "fidelity": (fidelity or {}).get("summary"),
                "verdict": verdict,
            },
            indent=2,
            sort_keys=True,
        )
    )
    append_gate_result(
        root / "logs" / "gate_history.jsonl",
        gate="strategy_validation",
        passed=bool(verdict["passed"]),
        failures=list(verdict["failures"]),
        metadata={
            "baseline_mode": args.baseline_mode,
            "candidate_mode": args.candidate_mode,
            "candles": str(Path(args.candles)),
            "output": str(output_path),
            "fidelity_report": args.fidelity_report,
        },
    )
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
