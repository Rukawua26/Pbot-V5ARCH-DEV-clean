import unittest

import numpy as np
import pandas as pd

from tools.train_nn_1h import build_labels, chronological_split_indices


class TrainNN1HTemporalTest(unittest.TestCase):
    def test_chronological_split_keeps_embargo_gap(self):
        train_idx, val_idx = chronological_split_indices(200, val_fraction=0.2, embargo_bars=24)

        self.assertLess(train_idx.max(), val_idx.min())
        self.assertGreaterEqual(val_idx.min() - train_idx.max(), 24)

    def test_build_labels_limits_future_horizon(self):
        df = pd.DataFrame(
            {
                "close": [100.0, 100.0, 100.0, 100.0],
                "high": [100.0, 100.0, 100.0, 103.0],
                "low": [100.0, 100.0, 100.0, 100.0],
                "mt_score": [50.0, 50.0, 50.0, 50.0],
                "sr_score": [50.0, 50.0, 50.0, 50.0],
                "lb_score": [50.0, 50.0, 50.0, 50.0],
            }
        )

        X_short, y_short = build_labels(df, sl_pct=1.0, tp_pct=2.0, max_horizon_bars=1)
        X_long, y_long = build_labels(df, sl_pct=1.0, tp_pct=2.0, max_horizon_bars=3)

        self.assertEqual(len(X_short), 0)
        self.assertEqual(len(y_short), 0)
        self.assertGreater(len(X_long), 0)
        self.assertTrue(np.all(y_long == 1))


if __name__ == "__main__":
    unittest.main()
