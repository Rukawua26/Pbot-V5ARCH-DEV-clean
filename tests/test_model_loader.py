import hashlib
import pickle
import tempfile
import unittest
from pathlib import Path

from core.model_loader import (
    ROOT,
    ModelHashMismatchError,
    UnsafeModelPathError,
    safe_pickle_load,
)


class ModelLoaderTest(unittest.TestCase):
    def test_safe_pickle_load_allows_repo_local_model(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            model_path = Path(tmp) / "model.pkl"
            payload = pickle.dumps({"ok": True})
            model_path.write_bytes(payload)
            model_path.with_suffix(".pkl.sha256").write_text(
                hashlib.sha256(payload).hexdigest(), encoding="utf-8"
            )

            model = safe_pickle_load(model_path)

        self.assertEqual(model, {"ok": True})

    def test_safe_pickle_load_rejects_path_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            model_path.write_bytes(pickle.dumps({"ok": True}))

            with self.assertRaises(UnsafeModelPathError):
                safe_pickle_load(model_path)

    def test_safe_pickle_load_rejects_missing_sha256_sidecar(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            model_path = Path(tmp) / "model.pkl"
            model_path.write_bytes(pickle.dumps({"ok": True}))

            with self.assertRaises(ModelHashMismatchError):
                safe_pickle_load(model_path)

    def test_safe_pickle_load_validates_sha256_sidecar(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            model_path = Path(tmp) / "model.pkl"
            payload = pickle.dumps({"ok": True})
            model_path.write_bytes(payload)
            model_path.with_suffix(".pkl.sha256").write_text(
                hashlib.sha256(payload).hexdigest(), encoding="utf-8"
            )

            model = safe_pickle_load(model_path)

        self.assertEqual(model, {"ok": True})

    def test_safe_pickle_load_rejects_sha256_mismatch(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            model_path = Path(tmp) / "model.pkl"
            model_path.write_bytes(pickle.dumps({"ok": True}))
            model_path.with_suffix(".pkl.sha256").write_text("0" * 64, encoding="utf-8")

            with self.assertRaises(ModelHashMismatchError):
                safe_pickle_load(model_path)


if __name__ == "__main__":
    unittest.main()
