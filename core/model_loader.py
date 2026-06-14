from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class UnsafeModelPathError(ValueError):
    """Raised when a model path is not safe to load."""


class ModelHashMismatchError(ValueError):
    """Raised when a model sidecar hash does not match file contents."""


class UnsafeScriptPathError(ValueError):
    """Raised when a script path is not safe to execute."""


def resolve_script_path(path: str | os.PathLike[str]) -> Path:
    """Resolve and validate a script path is within the repo root.

    Prevents path traversal when executing tools via subprocess.
    """
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as error:
        raise UnsafeScriptPathError(f"Script path outside repo root: {resolved}") from error
    if resolved.is_symlink():
        raise UnsafeScriptPathError(f"Script path must not be a symlink: {resolved}")
    return resolved


def _resolve_model_path(path: str | os.PathLike[str]) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as error:
        raise UnsafeModelPathError(f"Model path outside repo root: {resolved}") from error
    if resolved.is_symlink():
        raise UnsafeModelPathError(f"Model path must not be a symlink: {resolved}")
    return resolved


def _read_expected_sha256(path: Path) -> str | None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.exists():
        return None
    expected = sidecar.read_text(encoding="utf-8").strip().split()[0]
    return expected.lower() or None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_hash(path: str | os.PathLike[str]) -> None:
    resolved = _resolve_model_path(path)
    expected = _read_expected_sha256(resolved)
    if not expected:
        raise ModelHashMismatchError(
            f"No SHA-256 sidecar found for {resolved}. "
            f"Create {resolved}.sha256 with the expected hash to load this model securely."
        )
    actual = _sha256_file(resolved)
    if actual.lower() != expected:
        raise ModelHashMismatchError(
            f"Model hash mismatch for {resolved}: expected={expected} actual={actual}"
        )


def safe_pickle_load(path: str | os.PathLike[str]) -> Any:
    """Load a local model pickle only from an expected repository path."""
    resolved = _resolve_model_path(path)
    verify_model_hash(resolved)
    with resolved.open("rb") as file_obj:
        return pickle.load(file_obj)
