import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.replay_signal_alerts_counterfactual import (
    MAX_FEATURES_JSON_CHARS,
    MAX_TOTAL_FEATURES_JSON_CHARS,
    _load_rows,
    _write_output,
    build_report,
    classify,
)


def _row(side, reason, regime="BULL_TREND", **features):
    return {
        "id": 1,
        "ts": "2026-08-12T12:00:00",
        "symbol": "BTC/USDT",
        "alert_type": side,
        "execution_mode": "BOOTSTRAP_NONE",
        "status": "DISCARDED",
        "features_json": "{}",
        "features_state": "valid",
        "features": {
            "filter_passed": False,
            "filter_reason": reason,
            "btc_regime": regime,
            **features,
        },
    }


class ReplaySignalAlertsCounterfactualTest(unittest.TestCase):
    def _database(self, path):
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE signal_alerts (
                id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, alert_type TEXT,
                execution_mode TEXT, status TEXT, features_json TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO signal_alerts VALUES (1, ?, ?, ?, ?, ?, ?)",
            (
                "2026-08-12T12:00:00",
                "BTC/USDT",
                "BUY",
                "BOOTSTRAP_NONE",
                "DISCARDED",
                json.dumps({"filter_reason": "BULL_TREND_ENTRY_VETO", "btc_regime": "BULL_TREND"}),
            ),
        )
        connection.commit()
        connection.close()

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.db"
            with self.assertRaises(FileNotFoundError):
                _load_rows(path, latest=1, since=None, until=None, strict=True)
            self.assertFalse(path.exists())

    def test_loading_does_not_modify_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            before = path.read_bytes()

            rows = _load_rows(path, latest=1, since=None, until=None, strict=True)

            self.assertEqual(len(rows), 1)
            self.assertEqual(path.read_bytes(), before)

    def test_output_cannot_alias_source_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            before = path.read_bytes()
            aliases = [path, Path(tmp) / "signals-link.db", Path(tmp) / "signals-hard.db"]
            aliases[1].symlink_to(path)
            os.link(path, aliases[2])

            for output in aliases:
                with self.subTest(output=output.name):
                    with self.assertRaises(ValueError):
                        _write_output(output, path, "{}")
                    self.assertEqual(path.read_bytes(), before)

    def test_output_cannot_overwrite_sqlite_sidecars_or_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            for suffix in ("-wal", "-shm", "-journal"):
                with self.subTest(suffix=suffix):
                    sidecar = Path(f"{path}{suffix}")
                    sidecar.write_bytes(b"sqlite-state")
                    alias = Path(tmp) / f"sidecar-alias-{suffix[1:]}"
                    os.link(sidecar, alias)

                    for output in (sidecar, alias):
                        with self.assertRaises(ValueError):
                            _write_output(output, path, "{}")
                    self.assertEqual(sidecar.read_bytes(), b"sqlite-state")

    def test_output_writes_report_to_distinct_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            output = Path(tmp) / "report.json"
            self._database(path)

            _write_output(output, path, '{"ok": true}')

            self.assertEqual(output.read_text(encoding="utf-8"), '{"ok": true}\n')

    def test_valid_empty_and_invalid_features_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            connection = sqlite3.connect(path)
            connection.execute("UPDATE signal_alerts SET features_json = '{}' WHERE id = 1")
            connection.commit()
            connection.close()

            rows = _load_rows(path, latest=1, since=None, until=None, strict=True)
            report = build_report(rows, ["observed"], path)
            self.assertEqual(report["coverage"]["valid_feature_objects"], 1)
            self.assertEqual(report["coverage"]["nonempty_feature_objects"], 0)

            connection = sqlite3.connect(path)
            connection.execute("UPDATE signal_alerts SET features_json = '{bad' WHERE id = 1")
            connection.commit()
            connection.close()
            rows = _load_rows(path, latest=1, since=None, until=None, strict=False)
            report = build_report(rows, ["observed"], path)
            self.assertEqual(report["coverage"]["invalid_feature_objects"], 1)
            with self.assertRaises(ValueError):
                _load_rows(path, latest=1, since=None, until=None, strict=True)

    def test_oversized_feature_is_not_materialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE signal_alerts SET features_json = ? WHERE id = 1",
                ("x" * (MAX_FEATURES_JSON_CHARS + 1),),
            )
            connection.commit()
            connection.close()

            rows = _load_rows(path, latest=1, since=None, until=None, strict=False)
            self.assertEqual(rows[0]["features_state"], "invalid")
            self.assertIsNone(rows[0]["features_json"])
            with self.assertRaises(ValueError):
                _load_rows(path, latest=1, since=None, until=None, strict=True)

    def test_total_feature_size_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            payload = json.dumps({"data": "x" * (MAX_FEATURES_JSON_CHARS - 20)})
            connection = sqlite3.connect(path)
            connection.execute("DELETE FROM signal_alerts")
            rows_needed = MAX_TOTAL_FEATURES_JSON_CHARS // len(payload) + 1
            connection.executemany(
                "INSERT INTO signal_alerts "
                "(ts, symbol, alert_type, execution_mode, status, features_json) "
                "VALUES (?, 'BTC/USDT', 'BUY', 'NONE', 'DISCARDED', ?)",
                ((f"2026-08-12T12:00:{index:02d}", payload) for index in range(rows_needed)),
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(ValueError, "total features_json size limit"):
                _load_rows(path, latest=None, since=None, until=None, strict=True)

    def test_timestamp_window_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.db"
            self._database(path)
            with self.assertRaisesRegex(ValueError, "valid ISO-8601"):
                _load_rows(path, latest=None, since="not-a-date", until=None, strict=True)
            with self.assertRaisesRegex(ValueError, "earlier than"):
                _load_rows(
                    path,
                    latest=None,
                    since="2026-08-13T00:00:00",
                    until="2026-08-12T00:00:00",
                    strict=True,
                )

    def test_directional_bull_veto_releases_buy_as_censored(self):
        row = _row("BUY", "BULL_TREND_ENTRY_VETO (BULL_TREND)", paper_mode=True)
        self.assertEqual(classify(row, "bull-directional"), "RELEASED_UNKNOWN_DOWNSTREAM")

    def test_directional_bull_veto_censors_buy_when_runtime_mode_is_missing(self):
        row = _row("BUY", "BULL_TREND_ENTRY_VETO (BULL_TREND)")
        self.assertEqual(classify(row, "bull-directional"), "RUNTIME_MODE_CENSORED")

    def test_directional_bull_veto_keeps_real_buy_blocked(self):
        row = _row("BUY", "BULL_TREND_ENTRY_VETO (BULL_TREND)", paper_mode=False)
        self.assertEqual(classify(row, "bull-directional"), "REMAINS_BLOCKED_BULL_REAL")

    def test_bull_off_releases_both_sides_as_censored(self):
        for side in ("BUY", "SELL"):
            with self.subTest(side=side):
                row = _row(side, "BULL_TREND_ENTRY_VETO (BULL_TREND)")
                self.assertEqual(classify(row, "bull-off"), "RELEASED_UNKNOWN_DOWNSTREAM")

    def test_directional_bull_veto_keeps_sell_blocked(self):
        row = _row("SELL", "BULL_TREND_ENTRY_VETO (BULL_TREND)")
        self.assertEqual(classify(row, "bull-directional"), "REMAINS_BLOCKED_BULL_COUNTER")

    def test_directional_bull_scenario_does_not_relabel_other_vetos(self):
        row = _row("SELL", "VETO_KAVA: RIESGO EXCESIVO")
        self.assertEqual(classify(row, "bull-directional"), "UNCHANGED_BLOCKED")

    def test_coherence_off_uses_bootstrap_candidate_without_claiming_execution(self):
        row = _row(
            "BUY",
            "COHERENCIA: BUY bloqueado en régimen BAJISTA",
            regime="BEAR_TREND",
            bootstrap_ready_shadow=True,
            bootstrap_ready_real=False,
        )
        self.assertEqual(classify(row, "coherence-off"), "ELIGIBLE_SHADOW_CANDIDATE")

    def test_observed_bootstrap_filter_pass_is_still_no_fire(self):
        row = _row("BUY", "", filter_passed=True)
        self.assertEqual(classify(row, "observed"), "RECORDED_NO_FIRE")

    def test_observed_reports_terminal_execution_status(self):
        row = _row("BUY", "", filter_passed=True)
        row["execution_mode"] = "SHADOW"
        row["status"] = "EXECUTED"
        self.assertEqual(classify(row, "observed"), "RECORDED_EXECUTED")

    def test_report_states_read_only_and_limitations(self):
        report = build_report(
            [_row("BUY", "BULL_TREND_ENTRY_VETO (BULL_TREND)")],
            ["observed", "bull-directional"],
            Path("signals.db"),
        )
        self.assertTrue(report["source"]["read_only"])
        self.assertEqual(report["source"]["db"], "signals.db")
        self.assertIn("released_rows_are_downstream_censored", report["limitations"])
        self.assertNotIn("EXECUTED", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
