from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

import tools.pandas_ta as pandas_ta  # noqa: F401 - registers the pandas df.ta accessor

if TYPE_CHECKING:
    from core.candle_close_cache import CandleCloseCache

logger = logging.getLogger("SniperAI")


class StrategyUtils:
    """
    Utilidades estáticas para el motor de estrategia.
    Maneja indicadores, preprocesamiento y detección de estructuras.
    """

    _ob_cache: dict[str, str] = {}
    _candle_cache: CandleCloseCache | None = None

    @staticmethod
    def compute_runtime_snapshot(
        df: pd.DataFrame, cache_symbol: str = "runtime"
    ) -> dict[str, float] | None:
        if df is None or df.empty:
            return None

        cc = StrategyUtils._candle_cache
        if cc is not None and "time" in df.columns:
            try:
                candle_ms = int(df["time"].iloc[-1])
                cached = cc.get("snapshot", cache_symbol, "1h", candle_ms)
                if cached is not None:
                    return cached
            except Exception:
                logger.debug("CandleCloseCache read failed", exc_info=True)

        required_cols = ["open", "high", "low", "close", "volume"]
        if any(col not in df.columns for col in required_cols):
            return None

        try:
            work = df[required_cols].copy()
            work = work.dropna(subset=required_cols)
            if len(work) < 100:
                return None

            work.ta.ema(length=9, append=True)
            work.ta.ema(length=21, append=True)
            work.ta.ema(length=50, append=True)
            work.ta.rsi(length=14, append=True)
            work.ta.atr(length=14, append=True)
            work.ta.adx(length=14, append=True)
            work.ta.bbands(length=20, append=True)
            work["volume_ma"] = work["volume"].rolling(window=20, min_periods=20).mean()

            needed = [
                "EMA_9",
                "EMA_21",
                "EMA_50",
                "RSI_14",
                "ATRr_14",
                "ADX_14",
                "BBL_20_2.0",
                "BBU_20_2.0",
                "volume_ma",
            ]
            work = work.dropna(subset=needed)
            if work.empty:
                return None

            last = work.iloc[-1]
            ema_9 = float(last["EMA_9"])
            ema_21 = float(last["EMA_21"])
            ema = float(last["EMA_50"])
            rsi = float(last["RSI_14"])
            atr = float(last["ATRr_14"])
            adx = float(last["ADX_14"])
            bb_lower = float(last["BBL_20_2.0"])
            bb_upper = float(last["BBU_20_2.0"])
            volume_ma = float(last["volume_ma"])
            close = float(last["close"])

            if any(
                pd.isna(v)
                for v in [ema_9, ema_21, ema, rsi, atr, adx, bb_lower, bb_upper, volume_ma, close]
            ):
                return None
            if (
                ema_9 <= 0
                or ema_21 <= 0
                or ema <= 0
                or close <= 0
                or volume_ma <= 0
                or atr < 0
                or adx < 0
            ):
                return None
            if bb_upper <= bb_lower:
                return None

            try:
                from config import Config

                slope_lookback = max(1, int(getattr(Config, "EMA_SLOPE_LOOKBACK", 2) or 2))
            except Exception:
                slope_lookback = 2
            ema_prev = (
                float(work["EMA_50"].iloc[-(slope_lookback + 1)])
                if len(work) > slope_lookback
                else ema
            )
            ema50_slope = ((ema - ema_prev) / ema_prev) if ema_prev > 0 else 0.0
            result = {
                "rows": float(len(work)),
                "ema_9": ema_9,
                "ema_21": ema_21,
                "ema": ema,
                "rsi": rsi,
                "atr": atr,
                "adx": adx,
                "volume_ma": volume_ma,
                "dist_ema": (close - ema) / ema,
                "ema_fast_spread": (ema_9 - ema_21) / close,
                "ema_compression": (ema_9 - ema) / close,
                "ema50_slope": ema50_slope,
                "bb_lower": bb_lower,
                "bb_upper": bb_upper,
                "bb_pos": (close - bb_lower) / (bb_upper - bb_lower),
                "bb_width": (bb_upper - bb_lower) / close,
            }

            if cc is not None and "time" in df.columns:
                try:
                    cc.set("snapshot", cache_symbol, "1h", int(df["time"].iloc[-1]), result)
                except Exception:
                    logger.debug("CandleCloseCache write failed", exc_info=True)

            return result
        except Exception:
            logger.warning("StrategyUtils snapshot falló", exc_info=True)
            return None

    @staticmethod
    def calculate_z_score(df: pd.DataFrame, window: int = 20) -> float:
        """Calcula el Z-Score de la volatilidad para detectar irracionalidad."""
        if df is None or len(df) < window:
            return 0.0
        try:
            returns = df["close"].pct_change().dropna()
            if len(returns) < window:
                return 0.0

            rolling_mean = returns.rolling(window=window).mean()
            rolling_std = returns.rolling(window=window).std()

            if rolling_std.iloc[-1] == 0:
                return 0.0

            z = (returns.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1]
            return float(z)
        except Exception:
            logger.debug("Z-score calculation falló", exc_info=True)
            return 0.0

    @staticmethod
    def get_market_context(adx: float, rsi: float) -> str:
        """Define el estado del mercado para ajustar pesos de agentes."""
        if adx > 25:
            return "TREND"
        elif rsi < 30 or rsi > 70:
            return "VOLATILE"
        return "CALM"

    @staticmethod
    def detect_order_block(df: pd.DataFrame, symbol: str) -> str:
        """Detecta bloques de órdenes con confirmación de volumen y mitigación."""
        if df is None or len(df) < 30:
            return "⚪"

        last_ts = str(df["time"].iloc[-1])
        cache_key = f"{symbol}_{last_ts}"
        if cache_key in StrategyUtils._ob_cache:
            return StrategyUtils._ob_cache[cache_key]

        last_20 = df.tail(20).copy()
        avg_body = abs(last_20["close"] - last_20["open"]).mean()
        avg_volume = last_20["volume"].mean() if "volume" in last_20.columns else 1.0

        result = "⚪"
        for i in range(len(df) - 2, len(df) - 22, -1):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            vol = candle["volume"] if "volume" in df.columns else 1.0

            if float(body) > (float(avg_body) * 1.6) and float(vol) > (float(avg_volume) * 1.2):
                is_bullish_ob = candle["close"] < candle["open"]
                is_bearish_ob = candle["close"] > candle["open"]
                current_price = df["close"].iloc[-1]

                if is_bullish_ob:
                    ob_low, ob_high = candle["low"], candle["high"]
                    if ob_low * 0.999 <= current_price <= ob_high * 1.002:
                        since_ob = df.iloc[i + 1 : -1]
                        if not since_ob.empty and (since_ob["close"] < ob_low).any():
                            continue
                        result = "🟢"
                        break
                elif is_bearish_ob:
                    ob_low, ob_high = candle["low"], candle["high"]
                    if ob_low * 0.998 <= current_price <= ob_high * 1.001:
                        since_ob = df.iloc[i + 1 : -1]
                        if not since_ob.empty and (since_ob["close"] > ob_high).any():
                            continue
                        result = "🔴"
                        break

        if len(StrategyUtils._ob_cache) > 100:
            StrategyUtils._ob_cache.clear()
        StrategyUtils._ob_cache[cache_key] = result
        return result

    @staticmethod
    def preprocess_data(df: pd.DataFrame, mode: str = "full") -> pd.DataFrame | None:
        """Punto único de cálculo de indicadores y normalización dinámica Z-Score."""
        if df is None or len(df) < 100:  # Aumentado a 100 para soportar la ventana de normalización
            return None

        try:
            if mode == "full":
                # Recalcular SIEMPRE indicadores base desde OHLCV para evitar
                # reutilizar columnas normalizadas/contaminadas del caché.
                cols_to_drop = [
                    "ema",
                    "rsi",
                    "atr",
                    "adx",
                    "bb_lower",
                    "bb_upper",
                    "stoch_k",
                    "stoch_d",
                    "ema_9",
                    "ema_21",
                    "ema_200",
                    "EMA_9",
                    "EMA_21",
                    "EMA_50",
                    "RSI_14",
                    "ATRr_14",
                    "ADX_14",
                    "BBL_20_2.0",
                    "BBU_20_2.0",
                    "STOCHk_14_3_3",
                    "STOCHd_14_3_3",
                    "EMA_200",
                ]
                df.drop(
                    columns=[c for c in cols_to_drop if c in df.columns],
                    inplace=True,
                    errors="ignore",
                )

                df.ta.ema(length=9, append=True)
                df.ta.ema(length=21, append=True)
                df.ta.ema(length=50, append=True)
                df.ta.rsi(length=14, append=True)
                df.ta.atr(length=14, append=True)
                df.ta.adx(length=14, append=True)
                df.ta.bbands(length=20, append=True)
                df.ta.stoch(k=14, d=3, append=True)
                if "volume_ma" not in df.columns and "volume" in df.columns:
                    df["volume_ma"] = df["volume"].rolling(window=20).mean()
                df.ta.ema(length=200, append=True)

                rename_map = {
                    "EMA_9": "ema_9",
                    "EMA_21": "ema_21",
                    "EMA_50": "ema",
                    "RSI_14": "rsi",
                    "ATRr_14": "atr",
                    "ADX_14": "adx",
                    "BBL_20_2.0": "bb_lower",
                    "BBU_20_2.0": "bb_upper",
                    "STOCHk_14_3_3": "stoch_k",
                    "STOCHd_14_3_3": "stoch_d",
                    "EMA_200": "ema_200",
                }
                df.rename(
                    columns={k: v for k, v in rename_map.items() if k in df.columns},
                    inplace=True,
                )
            else:
                df.drop(
                    columns=[
                        c
                        for c in [
                            "ema",
                            "ema_9",
                            "ema_21",
                            "ema_200",
                            "EMA_9",
                            "EMA_21",
                            "EMA_50",
                            "EMA_200",
                        ]
                        if c in df.columns
                    ],
                    inplace=True,
                    errors="ignore",
                )
                df.ta.ema(length=9, append=True)
                df.ta.ema(length=21, append=True)
                df.ta.ema(length=50, append=True)
                df.rename(columns={"EMA_9": "ema_9", "EMA_21": "ema_21"}, inplace=True)
                df.rename(columns={"EMA_50": "ema"}, inplace=True)
                if len(df) >= 200:
                    df.ta.ema(length=200, append=True)
                    df.rename(columns={"EMA_200": "ema_200"}, inplace=True)

            if mode == "full":
                if "rsi" not in df.columns or len(df) == 0:
                    return None
                last_rsi = df["rsi"].iloc[-1]
                if last_rsi == 0 or pd.isna(last_rsi):
                    return None

            df.fillna(0, inplace=True)

            # Preservar indicadores crudos para decisiones/filtros/UI.
            # Las columnas base pueden normalizarse para ML, pero el runtime
            # institucional debe evaluar RSI/ADX en su escala natural.
            if "rsi" in df.columns:
                df["rsi_raw"] = df["rsi"]
            if "adx" in df.columns:
                df["adx_raw"] = df["adx"]
            if "atr" in df.columns:
                df["atr_raw"] = df["atr"]
            if "volume" in df.columns:
                df["volume_raw"] = df["volume"]
            if "dist_ema" in df.columns:
                df["dist_ema_raw"] = df["dist_ema"]
            if "bb_pos" in df.columns:
                df["bb_pos_raw"] = df["bb_pos"]
            if "bb_width" in df.columns:
                df["bb_width_raw"] = df["bb_width"]

            # --- [SRE] NORMALIZACIÓN DINÁMICA Z-SCORE (Anti-Feature Drift) ---
            # Definimos las features que la IA consume (basado en GhostAgent)
            features_to_scale = [
                "rsi",
                "adx",
                "volume",
                "atr",
                "funding_rate",
                "z_score",
                "dist_ema",
                "bb_pos",
                "bb_width",
            ]
            available_features = [col for col in features_to_scale if col in df.columns]

            # 1. Generación de Features Sintéticas ANTES de normalizar
            # Esto evita que el Z-Score destruya la lógica matemática (ej: rsi**2)
            if "rsi" in df.columns:
                df["rsi_sq"] = df["rsi"] ** 2
                df["rsi_log"] = np.log1p(df["rsi"].abs())
                df["rsi_inv"] = 100 - df["rsi"]
            if "adx" in df.columns:
                df["adx_sq"] = df["adx"] ** 2
                df["adx_log"] = np.log1p(df["adx"].abs())
            if "rsi" in df.columns and "adx" in df.columns:
                df["rsi_adx"] = (df["rsi"] - 50) * df["adx"]
            if "volume" in df.columns and "adx" in df.columns:
                df["vol_adx"] = df["volume"] * df["adx"]

            # Actualizamos la lista para incluir las sintéticas en el escalado
            all_scaled_cols = available_features + [
                "rsi_sq",
                "rsi_log",
                "rsi_inv",
                "adx_sq",
                "adx_log",
                "rsi_adx",
                "vol_adx",
            ]

            window_size = 100
            for col in all_scaled_cols:
                if col in df.columns:
                    rolling_mean = df[col].rolling(window=window_size, min_periods=10).mean()
                    rolling_std = (
                        df[col].rolling(window=window_size, min_periods=10).std().replace(0, 1e-10)
                    )
                    df[col] = (df[col] - rolling_mean) / rolling_std

            # Limpieza de NaNs resultantes de la ventana móvil
            if available_features:
                df.dropna(subset=available_features, inplace=True)

            return df
        except Exception as e:
            logger.error(f"❌ Error en preprocess_data (mode={mode}): {e}", exc_info=True)
            return None
