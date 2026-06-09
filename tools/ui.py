"""
SNIPER AI v118-PRO - CONSOLE UI
=================================
Sin Rich, prints simples con progreso de scan
"""

import shutil
from datetime import datetime
from config import Config


class UI:
    def __init__(self):
        self.state = {}
        self._render_count = 0
        self._scan_progress = 0

    def start(self):
        print("=" * 60)
        print("  SNIPER AI v118-PRO - CONSOLE MODE")
        print("  Paper: " + ("YES" if Config.PAPER_MODE else "NO"))
        print("=" * 60)

    def stop(self):
        print("[UI] Detenido")

    def update(self, **kwargs):
        self.state.update(kwargs)

    @staticmethod
    def _fit_text(text, max_len):
        value = str(text or "")
        if max_len <= 0 or len(value) <= max_len:
            return value
        if max_len <= 3:
            return value[:max_len]
        return value[: max_len - 3] + "..."

    def _print_scanning(self):
        """Muestra el progreso del scan en curso"""
        scanner = self.state.get("scanner", [])
        self._scan_progress += 1

        # Solo mostrar cada 3 ciclos mientras escanea
        if self._scan_progress % 3 != 0:
            return

        print(
            f"\r[{datetime.now().strftime('%H:%M:%S')}] 📡 Escaneando... [{len(scanner)} pares]   ",
            end="",
            flush=True,
        )

    def render(self):
        """Imprime resumen completo"""
        self._render_count += 1

        # Mostrar progreso de scan en cada ciclo
        self._print_scanning()

        # Solo imprimir resumen completo cada 10 ciclos (~30 seg)
        if self._render_count % 10 != 0:
            return

        print()  # Nueva línea después del scan progress
        print()

        st = self.state
        scanner = st.get("scanner", [])
        trades = st.get("trades", [])
        recent_closed_trades = st.get("recent_closed_trades", [])
        balance = st.get("balance", 0)
        db_stats = st.get("db_stats", {})
        sentiment = st.get("sentiment", ("NEUTRAL", "white"))

        # Contar trades reales vs shadow
        real_active = sum(1 for t in trades if not t.get("is_shadow"))
        shadow_active = sum(1 for t in trades if t.get("is_shadow"))
        real_closing = sum(
            1
            for t in trades
            if not t.get("is_shadow") and t.get("closing_in_progress", False)
        )
        shadow_closing = sum(
            1
            for t in trades
            if t.get("is_shadow") and t.get("closing_in_progress", False)
        )

        # Stats de la DB
        total_real = db_stats.get("total_real_trades", 0) if db_stats else 0
        total_shadow = db_stats.get("total_shadow_trades", 0) if db_stats else 0
        shadow_wr = (
            db_stats.get("shadow_win_rate", db_stats.get("win_rate", 0))
            if db_stats
            else 0
        )
        real_wr = db_stats.get("real_win_rate", None) if db_stats else None
        real_wr_str = f"{real_wr:.1f}%" if isinstance(real_wr, (int, float)) else "N/A"

        term_width = max(shutil.get_terminal_size((120, 30)).columns, 70)
        table_width = max(70, min(term_width, 180))

        # Header
        print("=" * 70)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SNIPER v118-PRO")
        print("=" * 70)

        # Balance y stats
        print("\n📊 CUENTA")
        print(f"   Balance: ${balance:.2f}")
        print(f"   SHADOW WR: {shadow_wr:.1f}%")
        print(f"   REAL WR: {real_wr_str}")
        print(f"   Real Trades Totales: {total_real} | Shadow Totales: {total_shadow}")

        # Trades activos
        real_header = f"\n🔴 TRADES REALES ABIERTOS: {real_active}"
        if real_closing:
            real_header += f" | cerrando: {real_closing}"
        print(real_header)
        real_trades = [t for t in trades if not t.get("is_shadow")]
        if real_trades:
            for t in real_trades:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                entry = t.get("entry", 0) or t.get("entry_price", 0)
                pnl = t.get("pnl", 0)
                suffix = " [CERRANDO]" if t.get("closing_in_progress", False) else ""
                print(f"   - {sym} {side}{suffix}")
                print(f"     Entry: ${entry:.6f} | PnL: {pnl:+.2f}%")
        else:
            print("   (ninguno)")

        shadow_header = f"\n🟡 TRADES SHADOW ABIERTOS: {shadow_active}"
        if shadow_closing:
            shadow_header += f" | cerrando: {shadow_closing}"
        print(shadow_header)
        shadow_trades = [t for t in trades if t.get("is_shadow")]
        if shadow_trades:
            for t in shadow_trades:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                entry = t.get("entry", 0) or t.get("entry_price", 0)
                pnl = t.get("pnl", 0)
                suffix = " [CERRANDO]" if t.get("closing_in_progress", False) else ""
                print(f"   - {sym} {side}{suffix}")
                print(f"     Entry: ${entry:.6f} | PnL: {pnl:+.2f}%")
        else:
            print("   (ninguno)")

        print("\n🧾 ÚLTIMOS TRADES CERRADOS")
        if recent_closed_trades:
            for t in recent_closed_trades[:6]:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                pnl = t.get("pnl", 0)
                reason = self._fit_text(t.get("reason", ""), 44)
                tag = "SHADOW" if t.get("is_shadow") else "REAL"
                print(f"   - {sym} {side} [{tag}] | PnL: {pnl:+.2f}%")
                print(f"     Reason: {reason}")
        else:
            print("   (ninguno)")

        # Radar - símbolos escaneados
        print(f"\n📡 RADAR ({len(scanner)} pares)")
        print("-" * table_width)
        if scanner:
            # Header de la tabla
            print(
                f"{'#':<3} {'SYMBOL':<12} {'SIG':<5} {'PROB':<7} {'RSI':<5} {'TREND':<6} {'TIER':<6} {'RESULT'}"
            )
            print("-" * table_width)
            for i, item in enumerate(scanner, 1):
                sym = item.get("symbol", "?")
                signal = item.get("signal", "?") or item.get("side", "?")
                prob_str = item.get("ia_prob", "---")  # Ya viene como string "XX%"
                rsi_val = item.get("rsi_val", 0) or 0
                trend = item.get("trend_val", "N/A") or "N/A"
                tier = item.get("tier", "") or "IRON"
                result = item.get("result", "") or ""
                ia_shadow = item.get("ia_shadow", "")
                ia_real = item.get("ia_real", "")

                # Abreviar signal
                sig_map = {
                    "BUY": "BUY",
                    "SELL": "SELL",
                    "NEUTRAL": "NEUT",
                    "WAIT": "WAIT",
                    "HOLD": "HOLD",
                }
                sig = sig_map.get(signal, signal[:4]) if signal else "?"

                # Modo (REAL/SHADOW)
                if ia_real == "✅":
                    mode = "🔥REAL"
                elif ia_shadow == "✅":
                    mode = "🧪SH"
                else:
                    mode = tier[:4] if tier else "IRON"

                rsi_str = f"{rsi_val:.0f}" if isinstance(rsi_val, (int, float)) else "?"
                trend_str = trend[:5] if trend else "N/A"

                sym_str = self._fit_text(sym, 12)
                prefix = f"{i:<3} {sym_str:<12} {sig:<5} {prob_str:<7} {rsi_str:>4}  {trend_str:<6} {mode:<6} "
                result_width = max(24, table_width - len(prefix))
                result_text = self._fit_text(result, result_width)

                print(prefix + result_text)
        else:
            print("   🔄 Esperando datos del radar...")

        # Sentiment
        sentiment_text = sentiment[0] if isinstance(sentiment, tuple) else sentiment
        print(f"\n🌐 BTC SENTIMENT: {sentiment_text}")

        # ML Metrics (v118-PRO)
        ml = st.get("ml_metrics", {})
        if ml:
            print("\n🧠 MACHINE LEARNING")
            perf = ml.get("performance", {})
            if perf:
                print(
                    f"   Score: {perf.get('score', 0):.2f} | Precision: {perf.get('precision', 0):.2f}"
                )

            top = ml.get("top_symbols", [])
            if top:
                top_str = ", ".join(
                    [f"{s['symbol']}({s['accuracy']:.0f}%)" for s in top[:3]]
                )
                print(f"   Top: {top_str}")

        print("=" * 70)
