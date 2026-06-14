#!/usr/bin/env python3
from __future__ import annotations

import json

from .collector import collect_runtime_dataset


def main() -> int:
    dataset = collect_runtime_dataset(hours=24 * 7)
    print(json.dumps(dataset.get("research", {}).get("shadow_vs_real", {}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
