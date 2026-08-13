import tempfile
import unittest
from pathlib import Path

from tools.learning import Brain


class LearningStorageTest(unittest.TestCase):
    def test_brain_sqlite_uses_normal_synchronous(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "brain.db"
            brain = Brain(str(db_path))
            conn = brain._get_conn()
            try:
                synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(int(synchronous), 1)

    def test_signal_alert_status_updates_after_trade_id_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))
            brain.log_signal_alert(
                symbol="BTC/USDT",
                alert_type="BUY",
                execution_mode="PAPER",
                entry_client_order_id="entry-1",
            )

            updated = brain.update_signal_alert_status("entry-1", "EXECUTED")
            conn = brain._get_conn()
            try:
                status = conn.execute(
                    "SELECT status FROM signal_alerts WHERE entry_client_order_id = ?",
                    ("entry-1",),
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(updated, 1)
        self.assertEqual(status, "EXECUTED")


if __name__ == "__main__":
    unittest.main()
