"""
SNIPER AI v118-PRO - STRATEGY ENGINE (MODULAR ARCHITECTURE)
==========================================================
- Orquestación TRINITY: Tendencia (MT), Estructura (SR), IA (G)
- Sistema Decoupled: Agentes en core/strategy/agents/
- Consenso Neuronal mediante StrategyOrchestrator
- Utilidades centralizadas en StrategyUtils
"""

import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from config import Config
from core.strategy.orchestrator import StrategyOrchestrator
from core.strategy.utils import StrategyUtils

# Imports condicionales para plugins externos
try:
    from tools.ultimate_ml import UltimateMLSystem

    ULTIMATE_ML_AVAILABLE = True
except ImportError:
    ULTIMATE_ML_AVAILABLE = False

try:
    from tools.crash_predictor import CrashPredictor

    CRASH_PREDICTOR_AVAILABLE = True
except ImportError:
    CRASH_PREDICTOR_AVAILABLE = False

logger = logging.getLogger("SniperAI")


class Strategy:
    """
    [PUNTO DE ENTRADA ESTRATEGIA v118]
    Actúa como interfaz de alto nivel, delegando la complejidad
    al StrategyOrchestrator y StrategyUtils.
    """

    _orchestrator = StrategyOrchestrator()
    _ultimate_ml: Optional[Any] = None
    _crash_predictor: Optional[Any] = None
    _inmature_blacklist: Dict[str, datetime] = {}

    @classmethod
    def analyze(
        cls,
        df_main,
        df_1h,
        brain_instance,
        symbol="Asset",
        order_book=None,
        ghost_model=None,
        scaler=None,
        btc_delta_tf=0.0,
        min_score=None,
        funding_rate=0.0,
        df_4h=None,
        **kwargs,
    ):
        base_df = df_main
        btc_delta_tf = kwargs.get("btc_delta_tf", btc_delta_tf)

        precio = (
            base_df["close"].iloc[-1]
            if base_df is not None and not base_df.empty
            else 0.0
        )
        now = datetime.now()

        # 1. Validación de Madurez (Utilidad)
        if symbol in cls._inmature_blacklist:
            if now < cls._inmature_blacklist[symbol]:
                return (
                    "NEUTRAL",
                    "NONE",
                    precio,
                    0.0,
                    {"error": "COOLDOWN_INMATURO"},
                    {},
                )
            else:
                cls._inmature_blacklist.pop(symbol, None)

        # 2. Preprocesamiento (Utilidad)
        base_df = StrategyUtils.preprocess_data(base_df, mode="full")
        if base_df is None:
            cls._inmature_blacklist[symbol] = now + timedelta(minutes=3)
            return "NEUTRAL", "NONE", precio, 0.0, {"error": "VETO_DATA_GATE"}, {}

        df_1h = StrategyUtils.preprocess_data(df_1h, mode="trend")
        df_4h = StrategyUtils.preprocess_data(df_4h, mode="trend")

        # 3. Extracción de Contexto
        rsi = (
            base_df["rsi_raw"].iloc[-1]
            if "rsi_raw" in base_df.columns
            else base_df["rsi"].iloc[-1]
        )
        adx = (
            base_df["adx_raw"].iloc[-1]
            if "adx_raw" in base_df.columns
            else base_df["adx"].iloc[-1]
        )
        atr_pct = base_df["atr"].iloc[-1] / precio if precio > 0 else 0
        vol_avg = (
            base_df["volume_ma"].iloc[-1] if "volume_ma" in base_df.columns else 0.0
        )
        vol_rel = base_df["volume"].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
        ob_status = StrategyUtils.detect_order_block(base_df, symbol)

        # Tendencia direccional simplificada en timeframe base
        base_trend = (
            "UP"
            if precio
            > (base_df["ema"].iloc[-1] if "ema" in base_df.columns else precio)
            else "DOWN"
        )
        market_regime = str(kwargs.get("market_regime") or "").upper()
        if market_regime in ["BULL_TREND", "BEAR_TREND", "RANGE"]:
            regime = market_regime
        elif adx >= float(getattr(Config, "ADX_TREND_THRESHOLD", 20)):
            regime = "BULL_TREND" if base_trend == "UP" else "BEAR_TREND"
        else:
            regime = "RANGE"

        # 4. Preparación del Contexto para Agentes
        context = {
            "symbol": symbol,
            "side": "BUY" if base_trend == "UP" else "SELL",  # Señal base tentativa
            "price": precio,
            "rsi": rsi,
            "adx": adx,
            "vol_rel": vol_rel,
            "atr_pct": atr_pct,
            "btc_delta_tf": btc_delta_tf,
            "funding_rate": funding_rate,
            "df": base_df,
            "df_1h": df_1h,
            "df_4h": df_4h,
            "order_book": order_book,
            "ob_status": ob_status,
            "brain_instance": brain_instance,
            "model": ghost_model,  # Para GhostAgent
            "bootstrap_heuristic_mode": bool(ghost_model is None),
            "scaler": scaler,
            "regime": regime,
            "z_score": StrategyUtils.calculate_z_score(base_df),
        }

        # 5. Ejecución del Consenso (Orquestador)
        agent_performances = brain_instance.get_agent_performance(
            context_type=f"{base_trend}_{'VOLATIL' if adx > 25 else 'CALMO'}",
            primary_ids=["MT", "SR", "G"],
        )

        score_final, votos = cls._orchestrator.calculate_consensus(
            context, agent_performances
        )

        # 6. Filtros finales (Sello Institucional 1H)
        score_final = max(0.0, min(100.0, score_final))

        signal = "BUY" if base_trend == "UP" else "SELL"

        # Veto direccional macro 4H (duro, no suma puntaje)
        macro_veto_reason = None
        if df_4h is not None and not df_4h.empty and len(df_4h) >= 55:
            try:
                close_4h = float(df_4h["close"].iloc[-1])
                ema50_4h = float(
                    df_4h["close"].ewm(span=50, adjust=False).mean().iloc[-1]
                )
                adx_4h = float(df_4h["adx"].iloc[-1]) if "adx" in df_4h.columns else 0.0
                adx_gate = float(getattr(Config, "ADX_TREND_THRESHOLD", 20))

                if adx_4h >= adx_gate:
                    if close_4h < ema50_4h and signal == "BUY":
                        macro_veto_reason = "VETO_4H_BEARISH_BUY"
                    elif close_4h > ema50_4h and signal == "SELL":
                        macro_veto_reason = "VETO_4H_BULLISH_SELL"
            except Exception:
                macro_veto_reason = None

        # Reporte de telemetría para UI
        telemetry = {
            "regime": regime,
            "ob_status": ob_status,
            "votos": votos,
            "atr_pct": atr_pct,
            "adx": adx,
            "rsi": {"val": rsi},
            "trend": base_trend,
        }

        if macro_veto_reason:
            telemetry["error"] = macro_veto_reason
            return "NEUTRAL", "NONE", precio, 0.0, telemetry, votos

        return signal, "NONE", precio, score_final, telemetry, votos

    @classmethod
    def analyze_crash_risk(
        cls, df, symbol, funding_rate, side, order_book, btc_delta_tf, **kwargs
    ):
        """Delegación al CrashPredictor."""
        if cls._crash_predictor is None and CRASH_PREDICTOR_AVAILABLE:
            cls._crash_predictor = CrashPredictor()

        if not cls._crash_predictor:
            return {"crash_probability": 0, "signals": [], "recommended_action": "SAFE"}

        return cls._crash_predictor.analyze_crash_risk(
            df, symbol, funding_rate, side, order_book, btc_delta_tf
        )

    @classmethod
    def get_ultimate_ml(cls):
        if cls._ultimate_ml is None and ULTIMATE_ML_AVAILABLE:
            cls._ultimate_ml = UltimateMLSystem()
            cls._ultimate_ml.load()
        return cls._ultimate_ml

    @classmethod
    def get_take_profit(
        cls, entry_price, side, atr, trend, spread=0.0, fees=None, **kwargs
    ):
        """
        Cálculo dinámico de Take Profit con '3x Rule' (Cobertura de spread/fees).
        Implementación requerida por el Commander (v118-PRO).
        """
        # Usar VIRTUAL_FEE si no se provee fees
        actual_fees = fees if fees is not None else Config.VIRTUAL_FEE

        # Modifier basado en régimen
        modifier = Config.ATR_TP2_MULTIPLIER
        if trend == "CHAOS":
            modifier = (
                Config.ATR_TP1_MULTIPLIER
            )  # Menor TP en caos para asegurar salida

        # Ajustes de IA/Genes si existen
        genes = kwargs.get("genes", {})
        if "tp_multiplier" in genes:
            modifier *= genes["tp_multiplier"]

        base_tp_dist = atr * modifier

        # 3x Rule: El TP neto debe cubrir 3 veces los costos de entrada/salida
        # Costos = Spread % + Fees de Entrada % + Fees de Salida %
        # cost_dist = entry_price * (spread_pct + fees_pct * 2)
        total_cost_pct = spread + (actual_fees * 2)
        min_tp_dist = entry_price * total_cost_pct * 3.0

        final_tp_dist = max(base_tp_dist, min_tp_dist)

        if side == "BUY":
            return entry_price + final_tp_dist
        else:
            return entry_price - final_tp_dist

    @staticmethod
    def get_stop_loss(
        entry_price, side, atr, trend, is_shadow=False, modifier=None, **kwargs
    ):
        """
        Cálculo robusto de Stop Loss ajustado por régimen y volatilidad.
        """
        # Usar modifier provisto por Commander o el master de Config
        sl_modifier = (
            modifier if modifier is not None else Config.STOP_LOSS_ATR_MODIFIER
        )

        if trend == "CHAOS":
            sl_modifier *= (
                1.5  # Dar más espacio en alta volatilidad y evitar stop-hunts
            )

        # Ajustes de IA/Genes si existen
        genes = kwargs.get("genes", {})
        if "sl_multiplier" in genes:
            sl_modifier *= genes["sl_multiplier"]

        dist = atr * sl_modifier

        if side == "BUY":
            return entry_price - dist
        else:
            return entry_price + dist

    @classmethod
    def detect_order_block(cls, df, symbol):
        """Retro-compatibilidad para main.py v118."""
        return StrategyUtils.detect_order_block(df, symbol)

    @classmethod
    def compute_runtime_snapshot(cls, df, cache_symbol: str = "runtime"):
        return StrategyUtils.compute_runtime_snapshot(df, cache_symbol=cache_symbol)

    @classmethod
    def prepare_ghost_features(cls, rsi, adx, vol_rel):
        """Retro-compatibilidad para el módulo Trailing Dinámico en main.py."""

        # Asumimos 20 features como base genérica para RandomForest
        return np.zeros((1, 20))

    @classmethod
    def check_entry_filters(
        cls,
        rsi,
        adx,
        current_time,
        audit_signal,
        volatility,
        vol_rel,
        is_shadow=False,
        **kwargs,
    ):
        """
        [v118-STRESS_TEST] Filtros de seguridad críticos.
        ===============================================
        Implementa el 'Filtro KAVA' (Hard-Cap 1.2% SL).
        """
        # 1. Recuperar contexto de Stop Loss
        entry_price = kwargs.get("price", 0)
        atr = kwargs.get("atr", 0)
        side = kwargs.get("side", "BUY")
        trend = kwargs.get("regime", "RANGE")
        modifier = kwargs.get("modifier")
        genes = kwargs.get("genes") or {}

        if entry_price > 0 and atr > 0:
            sl_price = cls.get_stop_loss(
                entry_price,
                side,
                atr,
                trend,
                is_shadow,
                modifier=modifier,
                genes=genes,
            )
            sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
            max_entry_sl_pct = float(getattr(Config, "MAX_ENTRY_SL_PCT", 1.2) or 1.2)

            # --- VETO KAVA: Hard-Cap configurable ---
            if sl_dist_pct > max_entry_sl_pct:
                return (
                    False,
                    f"VETO_KAVA: RIESGO EXCESIVO ({sl_dist_pct:.2f}% > {max_entry_sl_pct:.2f}%)",
                    "HIGH_RISK",
                    {},
                )

        return True, "Filter Pass (v118-PRO)", "CALM", {}
