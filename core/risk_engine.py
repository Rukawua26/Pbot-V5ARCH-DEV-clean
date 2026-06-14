import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from config import Config
from core.config.hyperopt_loader import HyperoptConfigLoader
from core.types import SignalContext
from tools.crash_predictor import CrashPredictor
from tools.strategy import Strategy


def get_daily_pnl_pct(db_path, wallet_balance: float) -> tuple[float | None, float | None]:
    """Devuelve PnL real cerrado del día UTC como fracción y USD.

    Solo considera trades reales (`is_shadow=0`) y usa `timestamp` con prefijo
    YYYY-MM-DD UTC para evitar mezclar días locales en despliegues cloud.
    """
    try:
        balance = float(wallet_balance or 0.0)
        if balance <= 0:
            return 0.0, 0.0

        today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(SUM(pnl), 0.0)
                FROM trades
                WHERE is_shadow=0
                  AND pnl IS NOT NULL
                  AND timestamp LIKE ?
                """,
                (f"{today_utc}%",),
            )
            pnl_usd = float(cursor.fetchone()[0] or 0.0)
        finally:
            conn.close()

        return pnl_usd / balance, pnl_usd
    except Exception as error:
        logging.getLogger("RiskEngine").warning(f"⚠️ DAILY_DRAWDOWN_PNL_UNAVAILABLE: {error}")
        return None, None


class RiskEngine:
    """
    [V118-ULTIMATE] RISK ENGINE
    ===========================
    Centraliza el control de riesgo, dimensionamiento de posición (Sizing)
    y detección de anomalías de mercado.

    Kelly Fraccional: escala el notional entre MIN_NOTIONAL y max_margin
    en función de la confianza de la red neuronal de consenso (0-100%).
    """

    def __init__(self, brain):
        self.brain = brain
        self.crash_predictor = CrashPredictor()
        self.logger = logging.getLogger("RiskEngine")

        self.hyperopt_enabled = HyperoptConfigLoader.is_enabled()
        self.stop_loss_pct = float(HyperoptConfigLoader.get_param("stop_loss_pct", 2.45))
        self.take_profit_pct = float(HyperoptConfigLoader.get_param("take_profit_pct", 6.47))

        # [v118] ANTI-REVENGE SYSTEM
        self.symbol_streaks = {}  # {symbol: consecutive_losses}
        self.temp_blacklist = {}  # {symbol: expiry_datetime}

    def get_exit_levels(
        self,
        entry_price: float,
        side: str,
        atr: float,
        trend: str,
        is_shadow: bool = False,
        modifier: float | None = None,
        genes: dict | None = None,
        spread: float = 0.0,
        fees: float | None = None,
        regime_sl_mult: float = 1.0,
        regime_tp_mult: float = 1.0,
        symbol: str | None = None,
    ) -> tuple[float, float, str]:
        """Retorna niveles de SL/TP para runtime (CCXT) con soporte hyperopt.

        Args:
            regime_sl_mult: Multiplicador de SL por régimen (auto-tuning).
            regime_tp_mult: Multiplicador de TP por régimen (auto-tuning).
        """
        if entry_price <= 0:
            return 0.0, 0.0, "INVALID_ENTRY"

        if self.hyperopt_enabled and symbol:
            params = HyperoptConfigLoader.get_params_for_symbol(symbol)
            stop_loss_pct = float(params.get("stop_loss_pct", self.stop_loss_pct))
            take_profit_pct = float(params.get("take_profit_pct", self.take_profit_pct))
        else:
            stop_loss_pct = self.stop_loss_pct
            take_profit_pct = self.take_profit_pct

        if self.hyperopt_enabled and stop_loss_pct > 0 and take_profit_pct > 0:
            sl_dist = stop_loss_pct / 100.0 * regime_sl_mult
            tp_dist = take_profit_pct / 100.0 * regime_tp_mult
            if side == "BUY":
                sl = entry_price * (1.0 - sl_dist)
                tp = entry_price * (1.0 + tp_dist)
            else:
                sl = entry_price * (1.0 + sl_dist)
                tp = entry_price * (1.0 - tp_dist)
            return sl, tp, "HYPEROPT_SYMBOL" if symbol else "HYPEROPT_FIXED"

        g = genes or {}
        modifier = (modifier or 1.0) * regime_sl_mult
        sl = Strategy.get_stop_loss(
            entry_price,
            side,
            atr,
            trend,
            is_shadow,
            modifier=modifier,
            genes=g,
        )
        tp = Strategy.get_take_profit(
            entry_price,
            side,
            atr,
            trend,
            genes=g,
            spread=spread,
            fees=fees,
        )
        # Apply TP multiplier to the TP distance
        if regime_tp_mult != 1.0 and entry_price > 0:
            if side == "BUY":
                tp = entry_price + (tp - entry_price) * regime_tp_mult
            else:
                tp = entry_price - (entry_price - tp) * regime_tp_mult
        return sl, tp, "DYNAMIC_ATR"

    def calculate_position_size(
        self,
        balance: float,
        symbol: str,
        price: float,
        leverage: int,
        context: SignalContext,
        is_shadow: bool = False,
        exchange=None,
    ) -> tuple[float, float]:
        """
        Cálculo profesional de tamaño de posición (Position Sizing).
        Implementa Kelly Fraccional para sizing dinámico basado en confianza neuronal.

        Fórmula de escala:
          - conf < 60%  → notional = MIN_NOTIONAL_VALUE (base conservador)
          - conf ≥ 60%  → interpolación lineal hasta max_margin * leverage
          - El resultado siempre se valida contra MAX_RISK_USD y margen disponible.

        Retorna:
            (amount: float, final_notional: float)
            En caso de error retorna (0, código_error_negativo).
        """
        # [ABLATION] Si el sizing de Kelly está desactivado, redirigir al baseline de riesgo fijo
        if not getattr(Config, "USE_KELLY_SIZING", True):
            return self.calculate_position_size_by_stop(
                balance, symbol, price, price * 0.98, leverage, is_shadow, exchange
            )

        try:
            # --- 1. Límites absolutos ---
            base_notional = Config.MIN_NOTIONAL_VALUE  # e.g. $12
            margin_fraction = float(getattr(Config, "MAX_MARGIN_PERCENT", 5.0))
            if margin_fraction > 1.0:
                margin_fraction = margin_fraction / 100.0
            max_margin_usd = balance * margin_fraction
            max_notional_allowed = max_margin_usd * leverage

            # Veto de capital insuficiente: no se puede ni abrir el mínimo
            if not is_shadow and max_notional_allowed < base_notional:
                self.logger.warning(
                    f"🚫 CAPITAL_INSUF {symbol}: regla riesgo "
                    f"({margin_fraction * 100:.1f}%) → "
                    f"${max_notional_allowed:.2f} < min ${base_notional:.2f}"
                )
                return 0, -1

            # --- 2. Kelly Fraccional: escalar por confianza IA ---
            # Normalizar confianza al rango 0-100
            confidence = float(context.get("prob_final", 0.0))
            conf = confidence * 100.0 if confidence <= 1.0 else confidence

            if conf < 60.0:
                target_notional = base_notional
            else:
                scale_factor = (conf - 60.0) / (100.0 - 60.0)
                target_notional = base_notional + (
                    (max_notional_allowed - base_notional) * scale_factor
                )

            # --- 3. Cap de seguridad ---
            final_notional = min(target_notional, max_notional_allowed)

            # --- 4. Veto de riesgo absoluto (MAX_RISK_USD) ---
            # Usamos el SL mínimo del config como distancia conservadora
            atr_pct = float(context.get("atr_pct", 0.02))
            if self.hyperopt_enabled and self.stop_loss_pct > 0:
                sl_dist = max(self.stop_loss_pct / 100.0, 0.005)
            else:
                sl_dist = max(atr_pct * Config.STOP_LOSS_ATR_MODIFIER, 0.005)
            real_risk_usd = final_notional * sl_dist

            max_risk_usd = float(getattr(Config, "MAX_RISK_USD", 0.0) or 0.0)
            if not is_shadow and max_risk_usd > 0 and real_risk_usd > max_risk_usd:
                self.logger.warning(
                    f"🚫 VETO_RIESGO {symbol}: arriesga ${real_risk_usd:.2f} "
                    f"(Límite: ${max_risk_usd:.2f})"
                )
                return 0, -4  # -4: Riesgo excesivo

            # --- 5. Logging de sizing ---
            self.logger.info(
                f"📊 KELLY SIZING: {symbol} | Conf: {conf:.1f}% | "
                f"Notional: ${final_notional:.2f} "
                f"(Base: ${base_notional:.2f}, Max: ${max_notional_allowed:.2f})"
            )

            # --- 6. Resolución de cantidad via CCXT (solo modo real) ---
            # En SHADOW no necesitamos precisión del exchange para simular.
            if is_shadow or price <= 0 or exchange is None:
                # Modo shadow / fallback: retornar solo el notional
                return final_notional / max(price, 1e-9), final_notional

            if symbol not in exchange.markets:
                exchange.load_markets()
                if symbol not in exchange.markets:
                    return 0, -3  # -3: Símbolo no disponible

            raw_amount = final_notional / price
            amount_str = exchange.amount_to_precision(symbol, raw_amount)
            if not amount_str:
                raise ValueError("amount_to_precision devolvió string vacío")

            amount = float(amount_str)

            # Ajuste mínimo post-redondeo (evitar error -4164 de Binance)
            if amount * price < 5.05:
                target_usd = Config.MIN_NOTIONAL_VALUE
                raw_min_amount = target_usd / price
                amount_str = exchange.amount_to_precision(symbol, raw_min_amount)
                amount = float(amount_str)
                final_notional = amount * price
                self.logger.info(
                    f"⚠️ AJUSTE_PRECISIÓN {symbol}: forzado a {amount} "
                    f"para cumplir min ${target_usd} (Notional: ${final_notional:.2f})"
                )

            if amount <= 0:
                return 0, -2  # -2: Cálculo inválido

            final_notional = amount * price
            real_risk_usd = final_notional * sl_dist
            if not is_shadow and max_risk_usd > 0 and real_risk_usd > max_risk_usd:
                self.logger.warning(
                    f"🚫 VETO_RIESGO {symbol}: ajuste mínimo arriesga "
                    f"${real_risk_usd:.2f} > límite ${max_risk_usd:.2f}"
                )
                return 0, -4

            return amount, final_notional

        except Exception as e:
            self.logger.error(f"❌ Error calculate_position_size {symbol}: {e}")
            return 0, -2

    def calculate_position_size_by_stop(
        self,
        balance: float,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        leverage: int,
        is_shadow: bool = False,
        exchange=None,
        risk_pct: float | None = None,
    ) -> tuple[float, float]:
        """Calcula cantidad por riesgo real: risk_usd / distancia al SL.

        Retorna (amount, final_notional). Si no puede operar, retorna amount=0 y
        código negativo en final_notional para mantener compatibilidad con el flujo
        actual de `trade_manager`.
        """
        try:
            balance = float(balance or 0.0)
            entry = float(entry_price or 0.0)
            sl = float(stop_loss_price or 0.0)
            if balance <= 0 or entry <= 0 or sl <= 0:
                return 0.0, -2

            stop_distance = abs(entry - sl)
            if stop_distance <= 0:
                return 0.0, -5

            risk_fraction = float(risk_pct if risk_pct is not None else Config.RISK_PER_TRADE_PCT)
            if risk_fraction <= 0:
                return 0.0, -2

            risk_budget_usd = balance * risk_fraction
            max_risk_usd = float(getattr(Config, "MAX_RISK_USD", 0.0) or 0.0)
            if max_risk_usd > 0:
                risk_budget_usd = min(risk_budget_usd, max_risk_usd)

            raw_amount = risk_budget_usd / stop_distance
            if raw_amount <= 0:
                return 0.0, -2

            margin_fraction = float(getattr(Config, "MAX_MARGIN_PERCENT", 5.0))
            if margin_fraction > 1.0:
                margin_fraction = margin_fraction / 100.0
            lev = max(1, int(float(leverage or 1)))
            max_notional_allowed = balance * margin_fraction * lev

            raw_notional = raw_amount * entry
            if max_notional_allowed > 0 and raw_notional > max_notional_allowed:
                raw_amount = max_notional_allowed / entry
                raw_notional = raw_amount * entry

            min_notional = float(getattr(Config, "MIN_NOTIONAL_VALUE", 0.0) or 0.0)
            if raw_notional < min_notional:
                affordable_notional = balance * lev
                if (
                    not is_shadow
                    and max_notional_allowed > 0
                    and min_notional > max_notional_allowed
                ):
                    self.logger.warning(
                        f"🚫 RISK_SIZE_MIN_MARGIN {symbol}: min notional ${min_notional:.2f} "
                        f"> margen permitido ${max_notional_allowed:.2f}"
                    )
                    return 0.0, -6
                min_amount = min_notional / entry if entry > 0 else 0.0
                min_risk_usd = min_amount * stop_distance
                if affordable_notional < min_notional:
                    self.logger.warning(
                        f"🚫 RISK_SIZE_MIN_NOTIONAL {symbol}: ${raw_notional:.2f} < "
                        f"${min_notional:.2f} y balance×leverage=${affordable_notional:.2f}"
                    )
                    return 0.0, -1
                if not is_shadow and max_risk_usd > 0 and min_risk_usd > max_risk_usd:
                    self.logger.warning(
                        f"🚫 RISK_SIZE_MIN_RISK {symbol}: min notional arriesga "
                        f"${min_risk_usd:.2f} > límite ${max_risk_usd:.2f}"
                    )
                    return 0.0, -4
                raw_amount = min_amount
                raw_notional = min_notional
                self.logger.info(
                    f"⚠️ RISK_SIZE_MIN_NOTIONAL_ADJUST {symbol}: "
                    f"${raw_notional:.2f} forzado al mínimo "
                    f"(risk=${min_risk_usd:.2f})"
                )

            if is_shadow or exchange is None:
                return raw_amount, raw_notional

            if symbol not in exchange.markets:
                exchange.load_markets()
                if symbol not in exchange.markets:
                    return 0.0, -3

            amount_str = exchange.amount_to_precision(symbol, raw_amount)
            if not amount_str:
                return 0.0, -2
            amount = float(amount_str)
            final_notional = amount * entry

            if amount <= 0:
                return 0.0, -2
            if final_notional < min_notional:
                self.logger.warning(
                    f"🚫 RISK_SIZE_MIN_NOTIONAL {symbol}: post-round ${final_notional:.2f} < ${min_notional:.2f}"
                )
                return 0.0, -1

            actual_risk = amount * stop_distance
            if not is_shadow and max_risk_usd > 0 and actual_risk > max_risk_usd:
                self.logger.warning(
                    f"🚫 RISK_SIZE_POST_PRECISION_RISK {symbol}: post-round risk "
                    f"${actual_risk:.2f} > límite ${max_risk_usd:.2f}"
                )
                return 0.0, -4
            self.logger.info(
                f"📊 RISK SIZING: {symbol} | risk=${actual_risk:.2f} "
                f"({risk_fraction * 100:.2f}%) | stop_dist=${stop_distance:.6f} | "
                f"notional=${final_notional:.2f} | amount={amount}"
            )
            return amount, final_notional
        except Exception as e:
            self.logger.error(f"❌ Error calculate_position_size_by_stop {symbol}: {e}")
            return 0.0, -2

    def check_market_safety(self, df, symbol, funding, side, order_book, btc_delta):
        """
        Consulta al CrashPredictor y aplica filtros de seguridad globales.
        Returns: (is_safe, reason, crash_probability)
        """
        if df is None or df.empty:
            return True, "SAFE_NO_DATA", 0.0

        analysis = self.crash_predictor.analyze_crash_risk(
            df, symbol, funding, side, order_book, btc_delta
        )

        prob = analysis["crash_probability"]
        action = analysis["recommended_action"]

        if action == "CLOSE_ALL" or prob > 75:
            return False, f"CRASH_PROB_EXTREME ({prob:.0f}%)", prob

        if action == "REDUCE_EXPOSURE" and prob > 50:
            return False, f"CRASH_PROB_HIGH ({prob:.0f}%)", prob

        return True, "SAFE", prob

    def check_daily_drawdown(self, current_balance):
        """Verifica si hemos alcanzado el límite de pérdida diaria."""
        try:
            percent_real, usd_hoy = self.brain.get_daily_real_pnl(current_balance)
            if percent_real is None:
                return False, "DAILY_DRAWDOWN_UNVERIFIED"
            if percent_real <= -Config.DAILY_LOSS_LIMIT:
                return False, f"DAILY_LIMIT_REACHED ({percent_real:.2f}%)"
            return True, "OK"
        except Exception:
            return False, "DAILY_DRAWDOWN_UNVERIFIED"

    def check_anti_revenge_blacklist(self, symbol: str) -> tuple[bool, str]:
        """
        [v118] Verifica si un símbolo está en enfriamiento por racha de pérdidas.
        """
        now = datetime.now()
        if symbol in self.temp_blacklist:
            expiry = self.temp_blacklist[symbol]
            if now < expiry:
                remaining = (expiry - now).total_seconds() / 3600
                return False, f"ANTI_REVENGE_BLACKLIST ({remaining:.1f}h restantes)"
            else:
                # Limpieza
                self.temp_blacklist.pop(symbol, None)
                self.symbol_streaks[symbol] = 0

        return True, "SAFE"

    def record_trade_result(self, symbol: str, pnl_pct: float):
        """
        [v118] Registra el resultado para la blacklist dinámica.
        """
        if pnl_pct < 0:
            self.symbol_streaks[symbol] = self.symbol_streaks.get(symbol, 0) + 1
            if self.symbol_streaks[symbol] >= 2:
                # Ban de 6 horas
                self.temp_blacklist[symbol] = datetime.now() + timedelta(hours=6)
                self.logger.warning(
                    f"🚫 [v118] ANTI-REVENGE: {symbol} baneado por 6h (Racha: {self.symbol_streaks[symbol]})"
                )
        else:
            # Una victoria rompe la racha
            self.symbol_streaks[symbol] = 0

    def check_signal_integrity(
        self, trade: dict, current_ai_score: float, elapsed_mins: float
    ) -> tuple[bool, str]:
        """
        [V118-SMART-EXIT]
        Evalúa si la razón probabilística de la entrada sigue vigente.
        Retorna: (is_degraded, reason)
        """
        entry_score = trade.get("entry_confidence", 75.0)
        side = trade.get("side")

        # Cálculo de caída relativa de confianza
        score_drop_pct = (entry_score - current_ai_score) / entry_score if entry_score > 0 else 0

        # Lógica para LONG
        if side == "BUY":
            if current_ai_score < 52.0:
                return True, f"CONFIDENCE_FLOOR_VIOLATED_{current_ai_score:.1f}"
            if score_drop_pct > 0.30 and elapsed_mins <= 3.0:
                return True, f"SUDDEN_CONFIDENCE_CRASH_{score_drop_pct * 100:.1f}%"

        # Lógica para SHORT
        elif side == "SELL":
            # En shorts, un aumento del score (presión alcista según consenso) invalida la tesis
            if current_ai_score > 55.0:
                return True, f"SHORT_THESIS_INVALIDATED_{current_ai_score:.1f}"

        return False, "INTEGRITY_OK"

    def should_abort_trade(
        self,
        entry_confidence: float,
        current_confidence: float,
        threshold_factor: float = 0.70,
    ) -> tuple[bool, str]:
        """
        [SMART EXIT v118] Regla universal de degradación de tesis.
        ============================================================
        Dispara una orden de cierre (Market Exit) si la confianza actual
        cae por debajo del `threshold_factor` (70% por defecto) de la
        confianza registrada en el momento de la entrada.

        Diseño:
        - Agnóstica de lado BUY/SELL (complementa check_signal_integrity).
        - Un único umbral relativo evita inconsistencias por activo.
        - Ejemplo: entrada con 80% → abortar si current_conf < 56%
                   entrada con 65% → abortar si current_conf < 45.5%

        Args:
            entry_confidence:   Confianza de la IA en el momento de abrir el trade (0-100).
            current_confidence: Confianza de la IA en el tick de monitoreo actual (0-100).
            threshold_factor:   Fracción mínima aceptable (default 0.70 = 70%).

        Returns:
            (should_abort: bool, reason: str)
        """
        if entry_confidence <= 0:
            return False, "NO_ENTRY_CONF"

        threshold = entry_confidence * threshold_factor
        if current_confidence < threshold:
            drop_pct = (entry_confidence - current_confidence) / entry_confidence * 100
            reason = (
                f"CONF_DEGRADED_{drop_pct:.1f}%"
                f"_ENTRY={entry_confidence:.1f}"
                f"_NOW={current_confidence:.1f}"
                f"_FLOOR={threshold:.1f}"
            )
            self.logger.warning(
                f"🚨 SMART EXIT ACTIVADO: Confianza {current_confidence:.1f}% "
                f"< umbral {threshold:.1f}% (entrada: {entry_confidence:.1f}%) — "
                f"Caída: {drop_pct:.1f}%. Tesis invalidada → Market Exit."
            )
            return True, reason

        return False, "CONF_OK"

    def should_defer_confidence_exit_for_fee_noise(
        self,
        trade: dict,
        current_price: float,
        elapsed_mins: float,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Evita cierres por degradación cuando el trade apenas está sobre entrada
        y el movimiento todavía no cubre el coste estimado de ida y vuelta.
        """
        if not bool(getattr(Config, "SMART_EXIT_FEE_GUARD_ENABLED", False)):
            return False, "FEE_GUARD_DISABLED"

        if current_price <= 0 or elapsed_mins <= 0:
            return False, "PRICE_OR_TIME_UNAVAILABLE"

        reason_text = str(reason or "")
        if "CONFIDENCE_FLOOR_VIOLATED" not in reason_text:
            return False, "NOT_FEE_NOISE_REASON"

        max_minutes = float(getattr(Config, "SMART_EXIT_FEE_NOISE_MAX_MINUTES", 45.0) or 45.0)
        if elapsed_mins > max_minutes:
            return False, "FEE_NOISE_WINDOW_EXPIRED"

        entry = float(trade.get("entry") or 0.0)
        if entry <= 0:
            return False, "NO_ENTRY_PRICE"

        side = str(trade.get("side") or "BUY").upper()
        gross_move_pct = (
            ((current_price - entry) / entry) * 100.0
            if side == "BUY"
            else ((entry - current_price) / entry) * 100.0
        )
        fee_floor_pct = float(getattr(Config, "VIRTUAL_FEE", 0.001) or 0.001) * 2.0 * 100.0

        if 0.0 <= gross_move_pct < fee_floor_pct:
            return (
                True,
                f"FEE_NOISE_GROSS={gross_move_pct:.3f}%_FLOOR={fee_floor_pct:.3f}%",
            )

        return False, "FEE_NOISE_NOT_APPLICABLE"
