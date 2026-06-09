"""
SNIPER AI v118.0 - CRASH PREDICTOR MODULE
========================================
- Módulo de detección proactiva de crashes
- Sin necesidad de API de noticias
- Usa: Funding Rate, Order Book, RSI Divergence, Volumen, BTC Delta
"""

from datetime import datetime


class CrashPredictor:
    def __init__(self):
        self.crash_signals_history = []
        self.last_crash_warning = None
        self.consecutive_warnings = 0

    @staticmethod
    def calculate_rsi_divergence(df, lookback=14):
        """
        Detecta divergencia RSI (precio sube, RSI baja = debilidad)
        Returns: ('BEARISH_DIVERGENCE', confidence) o (None, 0)
        """
        if df is None or len(df) < lookback + 5:
            return None, 0

        try:
            price_now = df["close"].iloc[-1]
            price_prev = df["close"].iloc[-lookback]
            price_change = (price_now - price_prev) / price_prev * 100

            rsi_now = df["rsi"].iloc[-1] if "rsi" in df.columns else 50
            rsi_prev = df["rsi"].iloc[-lookback] if "rsi" in df.columns else 50
            rsi_change = rsi_now - rsi_prev

            if price_change > 2.0 and rsi_change < -5:
                confidence = min(abs(rsi_change) * 2, 100)
                return "BEARISH_DIVERGENCE", confidence

            if price_change < -2.0 and rsi_change > 5:
                confidence = min(rsi_change * 2, 100)
                return "BULLISH_DIVERGENCE", confidence

        except (KeyError, IndexError):
            return None, 0

        return None, 0

    @staticmethod
    def analyze_funding_risk(funding_rate, side):
        """
        Analiza el funding rate para detectar riesgo de squeeze
        Returns: ('signal', risk_level 0-100)
        """
        if funding_rate == 0:
            return None, 0

        risk_level = 0
        signal = None

        abs_funding = abs(funding_rate)

        if abs_funding > 0.05:
            risk_level = 90
            signal = "EXTREME_FUNDING"
        elif abs_funding > 0.03:
            risk_level = 60
            signal = "HIGH_FUNDING"
        elif abs_funding > 0.01:
            risk_level = 30
            signal = "ELEVATED_FUNDING"

        if signal:
            if side == "BUY" and funding_rate < 0:
                risk_level += 20
            elif side == "SELL" and funding_rate > 0:
                risk_level += 20

        return signal, min(risk_level, 100)

    @staticmethod
    def detect_volume_anomaly(df, threshold=2.0):
        """
        Detecta spike de volumen sin movimiento proporcional de precio
        Returns: ('VOLUME_SPIKE', confidence)
        """
        if df is None or "volume" not in df.columns or len(df) < 20:
            return None, 0

        try:
            vol_curr = df["volume"].iloc[-1]
            vol_avg = df["volume"].rolling(20).mean().iloc[-1]

            if vol_avg == 0:
                return None, 0

            vol_ratio = vol_curr / vol_avg

            price_now = df["close"].iloc[-1]
            price_prev = df["close"].iloc[-5]
            price_change = abs((price_now - price_prev) / price_prev * 100)

            if vol_ratio > threshold and price_change < 1.0:
                confidence = min((vol_ratio - threshold) * 30, 100)
                return "VOLUME_ANOMALY", confidence

        except (KeyError, IndexError, ZeroDivisionError):
            return None, 0

        return None, 0

    @staticmethod
    def analyze_order_book_walls(order_book, side, price):
        """
        Detecta muros institucionales en el order book
        Returns: ('WHALE_WALL', risk_level)
        """
        if not order_book or "bids" not in order_book:
            return None, 0

        try:
            bids_vol = sum([b[1] for b in order_book.get("bids", [])[:10]])
            asks_vol = sum([a[1] for a in order_book.get("asks", [])[:10]])

            if bids_vol == 0 or asks_vol == 0:
                return None, 0

            if side == "BUY":
                if asks_vol > bids_vol * 5:
                    return "SELL_WALL", 80
                elif asks_vol > bids_vol * 3:
                    return "SELL_WALL", 50
            else:
                if bids_vol > asks_vol * 5:
                    return "BUY_WALL", 80
                elif bids_vol > asks_vol * 3:
                    return "BUY_WALL", 50

        except (KeyError, IndexError, ZeroDivisionError, TypeError):
            return None, 0

        return None, 0

    @staticmethod
    def detect_liquidation_squeeze(df, funding_rate, side):
        """
        Detecta condiciones de liquidation squeeze
        Returns: ('LIQUIDATION_SQUEEZE', risk_level)
        """
        risk_level = 0
        signal = None

        if funding_rate > 0.03 and side == "BUY":
            risk_level += 40
            signal = "LONG_SQUEEZE_RISK"
        elif funding_rate < -0.03 and side == "SELL":
            risk_level += 40
            signal = "SHORT_SQUEEZE_RISK"

        if df is not None and len(df) > 10:
            try:
                atr = df["atr"].iloc[-1] if "atr" in df.columns else 0
                price = df["close"].iloc[-1]

                if price > 0:
                    atr_pct = (atr / price) * 100
                    if atr_pct > 5:
                        risk_level += 30
                        if signal:
                            signal += "_HIGH_VOL"
                        else:
                            signal = "HIGH_VOLATILITY"
            except Exception:
                return signal, min(risk_level, 100) if signal else (None, 0)

        return signal, min(risk_level, 100) if signal else (None, 0)

    @staticmethod
    def analyze_btc_stress(btc_delta_tf, btc_price, btc_ema_200):
        """
        Analiza el estrés de BTC para detectar crashes inminentes
        Returns: ('BTC_CRASH_SIGNAL', risk_level)
        """
        if btc_delta_tf is None:
            return None, 0

        risk_level = 0
        signal = None

        if btc_delta_tf < -3.0:
            risk_level = 100
            signal = "BTC_CRASH"
        elif btc_delta_tf < -2.0:
            risk_level = 75
            signal = "BTC_DROP"
        elif btc_delta_tf < -1.5:
            risk_level = 50
            signal = "BTC_STRESS"

        if signal and btc_price and btc_ema_200:
            if btc_price < btc_ema_200:
                risk_level += 20

        return signal, min(risk_level, 100)

    @staticmethod
    def detect_market_exhaustion(rsi, adx, vol_rel, atr_pct):
        """
        Detecta agotamiento del mercado (sobrecompra/sobreventa extrema)
        Returns: ('EXHAUSTION', risk_level)
        """
        risk_level = 0
        signal = None

        if rsi > 80:
            risk_level = 70
            signal = "EXTREME_OVERBOUGHT"
        elif rsi > 75:
            risk_level = 50
            signal = "OVERBOUGHT"
        elif rsi < 20:
            risk_level = 70
            signal = "EXTREME_OVERSOLD"
        elif rsi < 25:
            risk_level = 50
            signal = "OVERSOLD"

        if adx < 15 and risk_level > 0:
            risk_level += 10

        if vol_rel > 2.0 and risk_level > 0:
            risk_level += 15

        if atr_pct > 0.04 and risk_level > 0:
            risk_level += 20

        return signal, min(risk_level, 100) if signal else (None, 0)

    def analyze_crash_risk(
        self,
        df,
        symbol,
        funding_rate,
        side,
        order_book,
        btc_delta_tf,
        btc_price=None,
        btc_ema_200=None,
    ):
        """
        Análisis completo de riesgo de crash
        Returns: {
            'crash_probability': 0-100,
            'signals': list of detected signals,
            'recommended_action': 'CLOSE_ALL' | 'REDUCE_EXPOSURE' | 'STAND_BY' | 'SAFE'
        }
        """
        signals = []
        total_risk = 0
        signal_count = 0

        rsi = df["rsi"].iloc[-1] if df is not None and "rsi" in df.columns else 50
        adx = df["adx"].iloc[-1] if df is not None and "adx" in df.columns else 20
        vol_rel = (
            df["volume"].iloc[-1] / df["volume"].rolling(20).mean().iloc[-1]
            if df is not None and "volume" in df.columns
            else 1.0
        )
        atr_pct = (
            (df["atr"].iloc[-1] / df["close"].iloc[-1])
            if df is not None and "atr" in df.columns and df["close"].iloc[-1] > 0
            else 0.02
        )

        div_signal, div_confidence = self.calculate_rsi_divergence(df)
        if div_signal:
            signals.append(
                {
                    "type": div_signal,
                    "confidence": div_confidence,
                    "source": "RSI_DIVERGENCE",
                }
            )
            total_risk += div_confidence
            signal_count += 1

        fund_signal, fund_risk = self.analyze_funding_risk(funding_rate, side)
        if fund_signal:
            signals.append(
                {"type": fund_signal, "confidence": fund_risk, "source": "FUNDING"}
            )
            total_risk += fund_risk
            signal_count += 1

        vol_signal, vol_confidence = self.detect_volume_anomaly(df)
        if vol_signal:
            signals.append(
                {"type": vol_signal, "confidence": vol_confidence, "source": "VOLUME"}
            )
            total_risk += vol_confidence
            signal_count += 1

        ob_signal, ob_risk = self.analyze_order_book_walls(
            order_book, side, df["close"].iloc[-1] if df is not None else 0
        )
        if ob_signal:
            signals.append(
                {"type": ob_signal, "confidence": ob_risk, "source": "ORDERBOOK"}
            )
            total_risk += ob_risk
            signal_count += 1

        liq_signal, liq_risk = self.detect_liquidation_squeeze(df, funding_rate, side)
        if liq_signal:
            signals.append(
                {"type": liq_signal, "confidence": liq_risk, "source": "LIQUIDATION"}
            )
            total_risk += liq_risk
            signal_count += 1

        btc_signal, btc_risk = self.analyze_btc_stress(
            btc_delta_tf, btc_price, btc_ema_200
        )
        if btc_signal:
            signals.append(
                {"type": btc_signal, "confidence": btc_risk, "source": "BTC"}
            )
            total_risk += btc_risk
            signal_count += 1

        exhaus_signal, exhaus_risk = self.detect_market_exhaustion(
            rsi, adx, vol_rel, atr_pct
        )
        if exhaus_signal:
            signals.append(
                {
                    "type": exhaus_signal,
                    "confidence": exhaus_risk,
                    "source": "EXHAUSTION",
                }
            )
            total_risk += exhaus_risk
            signal_count += 1

        crash_probability = total_risk / signal_count if signal_count > 0 else 0

        if crash_probability >= 70:
            recommended_action = "CLOSE_ALL"
        elif crash_probability >= 50:
            recommended_action = "REDUCE_EXPOSURE"
        elif crash_probability >= 30:
            recommended_action = "STAND_BY"
        else:
            recommended_action = "SAFE"

        result = {
            "crash_probability": crash_probability,
            "signals": signals,
            "recommended_action": recommended_action,
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
        }

        self.crash_signals_history.append(result)
        if len(self.crash_signals_history) > 100:
            self.crash_signals_history = self.crash_signals_history[-100:]

        return result

    def get_market_sentiment(self):
        """
        Retorna el sentimiento general del mercado basado en crashes detectados
        """
        if not self.crash_signals_history:
            return "NEUTRAL", 50

        recent = self.crash_signals_history[-10:]
        high_risk_count = sum(1 for r in recent if r["crash_probability"] >= 50)

        if high_risk_count >= 7:
            return "PANIC", 90
        elif high_risk_count >= 5:
            return "FEAR", 70
        elif high_risk_count >= 3:
            return "CAUTION", 50
        else:
            return "NEUTRAL", 30


def get_crash_warning_message(analysis):
    """Genera mensaje de alerta de crash"""
    if analysis["recommended_action"] == "SAFE":
        return None

    signals = [s["type"] for s in analysis["signals"]]
    prob = analysis["crash_probability"]

    msg = f"🚨 ALERTA CRASH: {prob:.0f}%\n"
    msg += f"📊 Señales: {', '.join(signals[:3])}\n"
    msg += f"⚡ Acción: {analysis['recommended_action']}"

    return msg
