import unittest
from pathlib import Path

import numpy as np

from tools.train_models import build_walk_forward_windows


class WalkForwardValidationTest(unittest.TestCase):
    def test_builds_monthly_rolling_windows(self):
        timestamps = np.array(
            [
                "2026-01-05T00:00:00",
                "2026-02-05T00:00:00",
                "2026-03-05T00:00:00",
                "2026-04-05T00:00:00",
                "2026-05-05T00:00:00",
            ],
            dtype="datetime64[ns]",
        )

        windows = build_walk_forward_windows(timestamps, train_months=3, val_months=1)

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["train_months"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(windows[0]["val_months"], ["2026-04"])
        self.assertEqual(windows[0]["train_idx"].tolist(), [0, 1, 2])
        self.assertEqual(windows[0]["val_idx"].tolist(), [3])
        self.assertEqual(windows[1]["train_months"], ["2026-02", "2026-03", "2026-04"])
        self.assertEqual(windows[1]["val_months"], ["2026-05"])

    def test_returns_no_windows_when_history_is_too_short(self):
        timestamps = np.array(
            ["2026-01-05T00:00:00", "2026-02-05T00:00:00"],
            dtype="datetime64[ns]",
        )

        windows = build_walk_forward_windows(timestamps, train_months=3, val_months=1)

        self.assertEqual(windows, [])

    def test_multi_month_validation_windows_do_not_overlap_by_default(self):
        timestamps = np.array(
            [f"2026-{month:02d}-05T00:00:00" for month in range(1, 9)],
            dtype="datetime64[ns]",
        )

        windows = build_walk_forward_windows(timestamps, train_months=3, val_months=2)

        self.assertEqual(windows[0]["val_months"], ["2026-04", "2026-05"])
        self.assertEqual(windows[1]["val_months"], ["2026-06", "2026-07"])
        self.assertTrue(set(windows[0]["val_months"]).isdisjoint(windows[1]["val_months"]))

    def test_training_pipeline_does_not_use_random_train_test_split(self):
        source = Path("tools/train_models.py").read_text(encoding="utf-8")

        self.assertNotIn("train_test_split", source)


if __name__ == "__main__":
    unittest.main()
