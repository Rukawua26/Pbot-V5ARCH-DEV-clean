import os
import re
import unittest
from pathlib import Path


class TestImportsGuardrail(unittest.TestCase):
    """
    Guardrail to prevent regression on deprecated root imports.
    Ensures modules in core/ and tools/ don't import from root wrappers.
    """

    DEPRECATED_WRAPPERS = [
        "learning",
        "notifier",
        "dashboard",
        "monitor_trades",
        "ml_optimizer",
        "test_start",
    ]
    TARGET_DIRS = ["core", "tools", "tests"]
    ROOT_DIR = Path(__file__).resolve().parent.parent

    def test_no_legacy_imports_internally(self):
        pattern = re.compile(
            r"^(import\s+("
            + "|".join(self.DEPRECATED_WRAPPERS)
            + r")|from\s+("
            + "|".join(self.DEPRECATED_WRAPPERS)
            + r")\s+import)"
        )

        violations = []

        for target in self.TARGET_DIRS:
            target_path = self.ROOT_DIR / target
            for root, _, files in os.walk(target_path):
                for file in files:
                    if file.endswith(".py") and file != "test_imports_guardrail.py":
                        path = Path(root) / file
                        with open(path, encoding="utf-8") as f:
                            for i, line in enumerate(f, 1):
                                if pattern.match(line.strip()):
                                    # Permit tools modules to import from tools.X but not from X (root)
                                    if "tools." in line:
                                        continue
                                    violations.append(
                                        f"{path.relative_to(self.ROOT_DIR)}:{i} -> {line.strip()}"
                                    )

        if violations:
            self.fail(
                "Legacy root imports detected. Use tools.<module> instead:\n"
                + "\n".join(violations)
            )


if __name__ == "__main__":
    unittest.main()
