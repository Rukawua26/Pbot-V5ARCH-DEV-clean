"""Minimal import stubs used only by the manual Risk Engine mutation audit."""

import sys
from types import ModuleType


class _CrashPredictor:
    pass


class _Strategy:
    pass


tools_module = ModuleType("tools")
tools_module.__path__ = []
crash_predictor_module = ModuleType("tools.crash_predictor")
crash_predictor_module.CrashPredictor = _CrashPredictor
strategy_module = ModuleType("tools.strategy")
strategy_module.Strategy = _Strategy

sys.modules.setdefault("tools", tools_module)
sys.modules["tools.crash_predictor"] = crash_predictor_module
sys.modules["tools.strategy"] = strategy_module
