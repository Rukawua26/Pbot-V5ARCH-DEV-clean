"""
SNIPER AI v118 - LEARNING MODULE (KNN VECTORIAL)
===============================================
- Versión unificada v118.
- Soporte para KNN Vectorial, Meta-Learning y Neural Consensus.
- RAG configurable, ML health checks y telemetría asíncrona.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
import json
import os
import random
import numpy as np
import pandas as pd
from core.active_trade_store import (
    delete_active_trade_state as run_delete_active_trade_state,
    load_active_trade_states as run_load_active_trade_states,
    save_active_trade_state as run_save_active_trade_state,
)
from core.learning_paths import DEFAULT_DB_PATH
from core.model_loader import safe_pickle_load
from core.rag_cache import (
    get_rag_inference as run_rag_inference,
    init_rag_cache as run_init_rag_cache,
    update_rag_cache as run_update_rag_cache,
)
from core.shadow_logger import (
    AsyncShadowLogger,
    LazyShadowLogger,
    shadow_logger as shadow_logger,
)

__all__ = ["AsyncShadowLogger", "LazyShadowLogger", "shadow_logger"]

try:
    from config import Config
except ImportError:
    Config = None  # type: ignore[assignment, misc]

import time

RAG_CACHE_MAX_TRADES = 5000

# --- UMBRALES DE LATENCIA RAG (v119) ---
RAG_LATENCY_OK_MS = 10.0
RAG_LATENCY_WARN_MS = 25.0
RAG_OPTIMIZE_THRESHOLD_MS = 100.0  # Gatillo para vectorización NumPy/FAISS


def _utc_now_naive():
    return datetime.now(UTC).replace(tzinfo=None)

# [v118] Ruta de la DB con soporte para inyección por ENV (Docker/OCI).
_DB_PATH = DEFAULT_DB_PATH


class Brain:
    def __init__(self, db_name: str = _DB_PATH):
        self.db_name = db_name
        self.pending_model_update = False  # [SRE] Flag para recarga oportunista
        self._init_db()
        self._init_rag_cache()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_name, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # Mejor concurrencia
        conn.execute("PRAGMA synchronous=NORMAL")  # Durabilidad razonable para recovery.
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        return conn

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()

        def _safe_add_column(sql: str, column_name: str):
            try:
                c.execute(sql)
            except sqlite3.OperationalError as e:
                message = str(e).lower()
                if "duplicate column name" in message or "already exists" in message:
                    return
                print(f"⚠️ Error migrando columna '{column_name}': {e}")
            except Exception as e:
                print(f"⚠️ Error migrando columna '{column_name}': {e}")

        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, symbol TEXT,
                side TEXT, entry_price REAL, exit_price REAL, pnl REAL,
                pnl_percent REAL, reason TEXT
            )
        """)

        migration_columns = [
            ("ALTER TABLE trades ADD COLUMN is_shadow BOOLEAN DEFAULT 0", "is_shadow"),
            ("ALTER TABLE trades ADD COLUMN fees REAL DEFAULT 0", "fees"),
            ("ALTER TABLE trades ADD COLUMN market_snapshot TEXT", "market_snapshot"),
            ("ALTER TABLE trades ADD COLUMN market_context TEXT", "market_context"),
            ("ALTER TABLE trades ADD COLUMN open_time TEXT", "open_time"),
            ("ALTER TABLE trades ADD COLUMN entry_ob TEXT DEFAULT '⚪'", "entry_ob"),
            ("ALTER TABLE trades ADD COLUMN funding_rate REAL", "funding_rate"),
            ("ALTER TABLE trades ADD COLUMN rsi REAL", "rsi"),
            ("ALTER TABLE trades ADD COLUMN adx REAL", "adx"),
            ("ALTER TABLE trades ADD COLUMN post_mortem_data TEXT", "post_mortem_data"),
            ("ALTER TABLE trades ADD COLUMN vol_rel REAL", "vol_rel"),
            ("ALTER TABLE trades ADD COLUMN dist_ema REAL", "dist_ema"),
            ("ALTER TABLE trades ADD COLUMN z_score REAL", "z_score"),
            ("ALTER TABLE trades ADD COLUMN bb_pos REAL", "bb_pos"),
            ("ALTER TABLE trades ADD COLUMN ob_status TEXT", "ob_status"),
            ("ALTER TABLE trades ADD COLUMN mae_percent REAL", "mae_percent"),
            ("ALTER TABLE trades ADD COLUMN mfe_percent REAL", "mfe_percent"),
            ("ALTER TABLE trades ADD COLUMN btc_correlation REAL", "btc_correlation"),
            ("ALTER TABLE trades ADD COLUMN market_regime TEXT", "market_regime"),
            ("ALTER TABLE trades ADD COLUMN entry_confidence REAL", "entry_confidence"),
            ("ALTER TABLE trades ADD COLUMN exit_confidence REAL", "exit_confidence"),
            (
                "ALTER TABLE trades ADD COLUMN entry_shock_level REAL",
                "entry_shock_level",
            ),
            ("ALTER TABLE trades ADD COLUMN entry_atr REAL", "entry_atr"),
            (
                "ALTER TABLE trades ADD COLUMN breakout_origin INTEGER DEFAULT 0",
                "breakout_origin",
            ),
            (
                "ALTER TABLE trades ADD COLUMN entry_client_order_id TEXT",
                "entry_client_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN sl_client_order_id TEXT",
                "sl_client_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN tp_client_order_id TEXT",
                "tp_client_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN entry_exchange_order_id TEXT",
                "entry_exchange_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN sl_exchange_order_id TEXT",
                "sl_exchange_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN tp_exchange_order_id TEXT",
                "tp_exchange_order_id",
            ),
            (
                "ALTER TABLE trades ADD COLUMN exit_reason TEXT",
                "exit_reason",
            ),
            (
                "ALTER TABLE trades ADD COLUMN is_adopted INTEGER DEFAULT 0",
                "is_adopted",
            ),
            (
                "ALTER TABLE trades ADD COLUMN is_dirty INTEGER DEFAULT 0",
                "is_dirty",
            ),
            (
                "ALTER TABLE trades ADD COLUMN mae_at_sl REAL",
                "mae_at_sl",
            ),
            (
                "ALTER TABLE trades ADD COLUMN mfe_at_sl REAL",
                "mfe_at_sl",
            ),
        ]

        for sql, column_name in migration_columns:
            _safe_add_column(sql, column_name)
        # ----------------------------------------------------

        c.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_config (
                symbol TEXT PRIMARY KEY,
                min_score REAL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_trades_state (
                symbol TEXT PRIMARY KEY,
                state_data TEXT
            )
        """)
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS confidence_exit_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_client_order_id TEXT UNIQUE,
                symbol TEXT,
                side TEXT,
                is_shadow BOOLEAN DEFAULT 0,
                entry_price REAL,
                amount REAL,
                entry_time TEXT,
                entry_confidence REAL,
                floor_confidence REAL,
                confidence_drop_pct REAL,
                floor_price REAL,
                gross_pnl_at_conf_drop_usd REAL,
                gross_pnl_at_conf_drop_pct REAL,
                fee_floor_usd REAL,
                fee_floor_pct REAL,
                fee_noise_zone INTEGER DEFAULT 0,
                guard_reason TEXT,
                trigger_reason TEXT,
                votes_json TEXT,
                dominant_killer TEXT,
                first_floor_ts TEXT,
                defer_count INTEGER DEFAULT 0,
                last_defer_ts TEXT,
                final_trade_id INTEGER,
                final_ts TEXT,
                final_reason TEXT,
                final_pnl_usd REAL,
                final_pnl_percent REAL
            )
        """
        )
        c.execute("""
            CREATE TABLE IF NOT EXISTS hourly_blacklist (
                hour INTEGER PRIMARY KEY,
                reason TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS sector_blacklist (
                sector TEXT PRIMARY KEY,
                reason TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS symbol_blacklist (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                added_date TEXT,
                min_trades INTEGER DEFAULT 5
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS system_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS genetic_config (
                symbol TEXT PRIMARY KEY,
                sl_mult REAL,
                tp_mult REAL,
                generation INTEGER DEFAULT 0,
                last_fitness REAL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS genetic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                sl_mult REAL,
                tp_mult REAL,
                fitness REAL,
                mutation_type TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_reputation (
                agent_id TEXT PRIMARY KEY,
                reputation REAL DEFAULT 100.0,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0
            )
        """)
        # Inicializar agentes si no existen (v118: 13 Agentes)
        for agent in ["T", "V", "J", "G", "C", "L", "F", "S", "O", "R", "M", "D", "E"]:
            c.execute(
                "INSERT OR IGNORE INTO agent_reputation (agent_id) VALUES (?)", (agent,)
            )

        # FASE 7: Autopsia Contextual (Reputación por Contexto)
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_reputation_context (
                agent_id TEXT,
                context_type TEXT,
                reputation REAL DEFAULT 100.0,
                total_trades INTEGER DEFAULT 0,
                PRIMARY KEY (agent_id, context_type)
            )
        """)

        # FASE 9: Meta-Aprendizaje (Patrones de error por agente)
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_meta_learning (
                agent_id TEXT,
                context_type TEXT,
                avg_vote_when_wrong REAL DEFAULT 50.0,
                avg_vote_when_right REAL DEFAULT 50.0,
                sample_count INTEGER DEFAULT 0,
                optimal_threshold REAL DEFAULT 60.0,
                PRIMARY KEY (agent_id, context_type)
            )
        """)

        # FASE 8: Curva de Equidad (Equity Curve)
        c.execute("""
            CREATE TABLE IF NOT EXISTS equity_history (
                timestamp TEXT PRIMARY KEY,
                balance REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                timestamp TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                entry_client_order_id TEXT,
                features_json TEXT
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_alerts_ts_status ON signal_alerts(ts, status)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_alerts_entry_client_oid ON signal_alerts(entry_client_order_id)"
        )

        # Migración: eliminar columna trade_id si existe (era NULL siempre, nunca usada)
        try:
            c.execute("ALTER TABLE signal_alerts DROP COLUMN trade_id")
        except Exception:
            pass

        c.execute("""
            CREATE TABLE IF NOT EXISTS trade_context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                is_shadow INTEGER DEFAULT 1,
                is_winner INTEGER DEFAULT 0,
                entry_timestamp TEXT NOT NULL,
                exit_timestamp TEXT,
                pnl_percent REAL,
                context_json TEXT NOT NULL,
                context_hash TEXT
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_tcs_context_hash "
            "ON trade_context_snapshots(context_hash)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_tcs_symbol "
            "ON trade_context_snapshots(symbol)"
        )

        c.execute("""
            CREATE TABLE IF NOT EXISTS error_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                error_msg TEXT,
                snapshot_data TEXT,
                timestamp TEXT
            )
        """)

        # ============================================================
        # SISTEMA DE PATRONES ELITE v2.0 (CORREGIDO)
        # ============================================================
        # Patrones Elite: WR >= 60%, PnL > 0, 20+ trades
        # Se pueden DESCLASIFICAR si WR < 50% o PnL < 0
        # Agregada columna 'confidence' basada en número de trades
        c.execute("""
            CREATE TABLE IF NOT EXISTS elite_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                rsi_avg REAL,
                adx_avg REAL,
                vol_rel_avg REAL,
                z_score_avg REAL,
                dist_ema_avg REAL,
                ob_status TEXT,
                win_rate REAL,
                avg_pnl REAL,
                total_trades INTEGER,
                confidence TEXT DEFAULT 'LOW',
                first_seen TEXT,
                last_updated TEXT,
                UNIQUE(symbol)
            )
        """)

        # Patrones Experimentales: Todos los demás (se borran cada 30 días)
        c.execute("""
            CREATE TABLE IF NOT EXISTS experimental_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                rsi_avg REAL,
                adx_avg REAL,
                vol_rel_avg REAL,
                z_score_avg REAL,
                dist_ema_avg REAL,
                ob_status TEXT,
                win_rate REAL,
                avg_pnl REAL,
                total_trades INTEGER,
                created_at TEXT,
                last_updated TEXT
            )
        """)

        # FASE 10: Auditoría de Patrones Elite
        c.execute("""
            CREATE TABLE IF NOT EXISTS elite_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                action TEXT,
                reason TEXT,
                metrics TEXT
            )
        """)

        conn.commit()
        conn.close()

    def _init_rag_cache(self):
        return run_init_rag_cache(self, RAG_CACHE_MAX_TRADES)

    def update_rag_cache(self, trade_data):
        return run_update_rag_cache(self, trade_data, RAG_CACHE_MAX_TRADES)

    def log_trade(self, trade_data):
        try:
            conn = self._get_conn()
            # Extraer datos de contexto si existen
            snap = trade_data.get("market_snapshot", {})
            funding = snap.get("funding_rate", 0.0)
            vol_rel = snap.get("vol_rel", 0.0)
            dist_ema = snap.get("dist_ema", 0.0)
            z_score = snap.get("z_score", 0.0)
            bb_pos = snap.get("bb_pos", 0.5)
            ob_status = snap.get("ob_status", "NEUTRAL")

            c = conn.cursor()
            c.execute(
                """
                INSERT INTO trades (
                    timestamp, symbol, side, entry_price, exit_price, pnl, pnl_percent, reason,
                    is_shadow, fees, funding_rate, vol_rel, rsi, adx, market_snapshot, open_time,
                    entry_ob, dist_ema, z_score, bb_pos, ob_status, mae_percent, mfe_percent,
                    market_regime, entry_confidence, exit_confidence, entry_shock_level, entry_atr,
                    breakout_origin, entry_client_order_id, sl_client_order_id, tp_client_order_id,
                    entry_exchange_order_id, sl_exchange_order_id, tp_exchange_order_id,
                    exit_reason, is_adopted, is_dirty, mae_at_sl, mfe_at_sl
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    trade_data["symbol"],
                    trade_data["side"],
                    trade_data["entry"],
                    trade_data["exit"],
                    trade_data.get("pnl_usd", 0.0),
                    trade_data["pnl_percent"],
                    trade_data["reason"],
                    trade_data.get("is_shadow", False),
                    trade_data.get("fees", 0.0),
                    funding,
                    vol_rel,
                    snap.get("rsi", 0.0),
                    snap.get("adx", 0.0),
                    json.dumps(trade_data.get("market_snapshot", {})),
                    trade_data.get("open_time"),
                    trade_data.get("entry_ob", "⚪"),
                    dist_ema,
                    z_score,
                    bb_pos,
                    ob_status,
                    trade_data.get("mae_percent", 0.0),
                    trade_data.get("mfe_percent", 0.0),
                    trade_data.get("market_regime", "RANGE"),
                    trade_data.get("entry_confidence", 0.0),
                    trade_data.get("exit_confidence", 0.0),
                    trade_data.get("entry_shock_level"),
                    trade_data.get("entry_atr"),
                    1 if trade_data.get("breakout_origin", False) else 0,
                    trade_data.get("entry_client_order_id"),
                    trade_data.get("sl_client_order_id"),
                    trade_data.get("tp_client_order_id"),
                    trade_data.get("entry_exchange_order_id"),
                    trade_data.get("sl_exchange_order_id"),
                    trade_data.get("tp_exchange_order_id"),
                    trade_data.get("exit_reason", "UNKNOWN"),
                    trade_data.get("is_adopted", 0),
                    trade_data.get("is_dirty", 0),
                    trade_data.get("mae_at_sl", 0.0),
                    trade_data.get("mfe_at_sl", 0.0),
                ),
            )
            trade_id = c.lastrowid
            conn.commit()
            conn.close()
            # Update RAM RAG cache with this new trade
            self.update_rag_cache(trade_data)

            # Clasificar y actualizar patrones
            self._classify_pattern(trade_data, snap)
            return trade_id
        except Exception as e:
            print(f"❌ Error guardando trade: {e}")
            return None

    def save_error_snapshot(self, symbol, error_msg, snapshot):
        """Registra una lección negativa cuando una oportunidad real falla o es vetada."""
        # [FIX] Sanitizar snapshot para evitar error de serialización JSON (DataFrames)
        clean_snap = {}
        if isinstance(snapshot, dict):
            clean_snap = {
                k: v for k, v in snapshot.items() if not isinstance(v, pd.DataFrame)
            }
        else:
            clean_snap = snapshot

        for attempt in range(3):
            try:
                conn = self._get_conn()
                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO trades (timestamp, symbol, side, pnl_percent, is_shadow, market_snapshot, entry_ob)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        datetime.now().isoformat(),
                        symbol,
                        "VETO_ERROR",
                        -99.0,
                        1,
                        json.dumps(clean_snap),
                        error_msg[:20],
                    ),
                )
                conn.commit()
                conn.close()
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 2:
                    time.sleep(0.05 * (2**attempt))
                    continue
                print(f"⚠️ Error de BD al guardar snapshot de error: {e}")
                return
            except sqlite3.Error as e:
                print(f"⚠️ Error de BD al guardar snapshot de error: {e}")
                return
            except Exception as e:
                print(f"❌ Error inesperado en save_error_snapshot: {e}")
                return

    def log_signal_alert(
        self,
        symbol,
        alert_type,
        execution_mode,
        status="PENDING",
        entry_client_order_id=None,
        features=None,
        ts=None,
    ):
        try:
            clean_features = {}
            if isinstance(features, dict):
                clean_features = {
                    k: v for k, v in features.items() if not isinstance(v, pd.DataFrame)
                }
            elif features is not None:
                clean_features = features

            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO signal_alerts (
                    ts, symbol, alert_type, execution_mode, status,
                    entry_client_order_id, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts or datetime.now().isoformat(),
                    symbol,
                    str(alert_type or "UNKNOWN"),
                    str(execution_mode or "NONE"),
                    str(status or "PENDING"),
                    entry_client_order_id,
                    json.dumps(clean_features or {}),
                ),
            )
            alert_id = c.lastrowid
            conn.commit()
            conn.close()
            return alert_id
        except Exception as e:
            print(f"⚠️ Error guardando signal_alert: {e}")
            return None

    def update_signal_alert_status(
        self, entry_client_order_id, status, trade_id=None, symbol=None
    ):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            if entry_client_order_id:
                c.execute(
                    """
                    UPDATE signal_alerts
                    SET status = ?, trade_id = COALESCE(?, trade_id)
                    WHERE entry_client_order_id = ?
                    """,
                    (str(status or "UNKNOWN"), trade_id, entry_client_order_id),
                )
            elif symbol:
                c.execute(
                    """
                    UPDATE signal_alerts
                    SET status = ?, trade_id = COALESCE(?, trade_id)
                    WHERE id = (
                        SELECT id FROM signal_alerts
                        WHERE symbol = ?
                        ORDER BY id DESC LIMIT 1
                    )
                    """,
                    (str(status or "UNKNOWN"), trade_id, symbol),
                )
            updated = c.rowcount
            conn.commit()
            conn.close()
            return updated
        except Exception as e:
            print(f"⚠️ Error actualizando signal_alert: {e}")
            return 0

    def get_recent_vetos(self, limit=3):
        """Recupera los últimos rechazos de la IA para el comando /thinking."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT symbol, entry_ob, market_snapshot FROM trades WHERE side='VETO_ERROR' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
            conn.close()

            results = []
            for r in rows:
                ctx_summary = "N/A"
                try:
                    snap = json.loads(r["market_snapshot"])
                    ctx_summary = (
                        f"RSI:{snap.get('rsi', 0):.1f} | Trend:{snap.get('trend', '?')}"
                    )
                except (json.JSONDecodeError, KeyError, TypeError):
                    ctx_summary = "N/A"  # Datos corruptos, usar N/A
                results.append(
                    {
                        "symbol": r["symbol"],
                        "reason": r["entry_ob"],
                        "context_summary": ctx_summary,
                    }
                )
            return results
        except Exception as e:
            print(f"⚠️ Error recuperando vetos recientes: {e}")
            return []

    def get_ai_maturity(self):
        """Calcula el progreso de aprendizaje de la IA."""
        # Encapsulamos el cálculo de XP y Rango aquí para que main.py sea más limpio
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM trades WHERE market_snapshot IS NOT NULL")
            total = c.fetchone()[0]
            conn.close()

            xp = min(
                (total / 10000) * 100, 100
            )  # Meta de 10,000 experiencias (Nivel Legendario)
            rank = (
                "🐣 Novato"
                if xp < 10
                else (
                    "⚔️ Guerrero"
                    if xp < 40
                    else ("🧠 Maestro" if xp < 80 else "🏆 LEGENDARIO")
                )
            )

            return {"xp_percent": round(xp, 1), "rank": rank, "total": total}
        except Exception as e:
            print(f"⚠️ Error calculando madurez IA: {e}")
            return {"xp_percent": 0, "rank": "Error", "total": 0}

    def reload_ghost_model(self, bot):
        """
        Realiza el Hot-Swap del modelo Ghost en la RAM del bot.
        Llamado únicamente por el Guardian en ventana segura (0 trades).
        """
        model_path = "ghost_brain.pkl"
        if os.path.exists(model_path):
            try:
                bot.ghost_model = safe_pickle_load(model_path)
                self.pending_model_update = False
                bot.log(
                    "✅ [HOT-SWAP] Modelo Ghost recargado exitosamente en ventana segura."
                )
                return True
            except Exception as e:
                bot.log(f"❌ Error en Hot-Swap de modelo: {e}")
        return False

    def get_dynamic_settings(self, symbol):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT * FROM dynamic_config WHERE symbol = ?", (symbol,))
            row = c.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    def update_dynamic_settings(self, symbol, min_score):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO dynamic_config (symbol, min_score)
                VALUES (?, ?)
            """,
                (symbol, min_score),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando config dinámica: {e}")

    def get_genetic_params(self, symbol):
        """Recupera parámetros genéticos (SL/TP) para un símbolo."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT sl_mult, tp_mult FROM genetic_config WHERE symbol = ?",
                (symbol,),
            )
            row = c.fetchone()
            conn.close()
            if row:
                return {"sl_mult": row["sl_mult"], "tp_mult": row["tp_mult"]}
            return None
        except Exception:
            return None

    def update_genetic_params(self, symbol, sl, tp, fitness=0, mutation_type="GENERIC"):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO genetic_config (symbol, sl_mult, tp_mult, last_fitness, generation)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(symbol) DO UPDATE SET
                    sl_mult=excluded.sl_mult,
                    tp_mult=excluded.tp_mult,
                    last_fitness=excluded.last_fitness,
                    generation=generation+1
            """,
                (symbol, sl, tp, fitness),
            )

            # Guardar en historial
            c.execute(
                """
                INSERT INTO genetic_history (timestamp, symbol, sl_mult, tp_mult, fitness, mutation_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (datetime.now().isoformat(), symbol, sl, tp, fitness, mutation_type),
            )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error updating genetics: {e}")

    # ============================================================
    # SISTEMA DE PATRONES ELITE v1.0
    # ============================================================

    def _classify_pattern(self, trade_data, snap):
        """Clasifica un trade como ELITE o EXPERIMENTAL basándose en su rendimiento."""
        try:
            symbol = trade_data.get("symbol")
            is_shadow = trade_data.get("is_shadow", False)

            # Solo clasificamos trades reales
            if is_shadow:
                return

            conn = self._get_conn()
            c = conn.cursor()

            # Obtener stats actuales del símbolo (incluyendo MAE)
            c.execute(
                """
                SELECT COUNT(*), 
                       ROUND(AVG(rsi), 1), 
                       ROUND(AVG(adx), 1), 
                       ROUND(AVG(vol_rel), 2),
                       ROUND(AVG(z_score), 2),
                       ROUND(AVG(dist_ema), 3),
                       ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
                       ROUND(AVG(pnl_percent), 2) as avg_pnl,
                       ROUND(AVG(mae_percent), 2) as avg_mae
                FROM trades 
                WHERE symbol = ? AND is_shadow = 0
            """,
                (symbol,),
            )

            row = c.fetchone()
            if not row or row[0] < 3:
                conn.close()
                return

            (
                trades_count,
                rsi_avg,
                adx_avg,
                vol_rel,
                z_score,
                dist_ema,
                wr,
                avg_pnl,
                avg_mae,
            ) = row
            now = datetime.now().isoformat()

            # === VETO POR MAE ===
            # Si MAE > 2x PnL promedio, desclasificar inmediatamente
            # Ejemplo: PnL avg = 1.5%, MAE avg = 4.0% -> Ratio = 2.67x -> DESCARTAR
            mae_ratio = abs(avg_mae) / abs(avg_pnl) if avg_pnl != 0 else 999
            veto_mae = mae_ratio > 2.0 and avg_mae > 0
            if veto_mae:
                is_elite_candidate = False
                veto_reason = (
                    f"MAE_RATIO={mae_ratio:.1f}x (MAE={avg_mae}%, PnL={avg_pnl}%)"
                )
            else:
                is_elite_candidate = wr >= 60 and avg_pnl > 0 and trades_count >= 20
                veto_reason = None

            # Validación de robustez (OOS) antes de confirmar elite
            is_robust = False
            robustness_msg = "N/A"
            if is_elite_candidate and trades_count >= 30:
                is_robust, robustness_msg = self.validate_pattern_robustness(symbol)

            # Solo es elite si pasa la validación de robustez
            is_elite = is_elite_candidate and is_robust

            # Verificar si ya era elite antes (para logging)
            c.execute(
                "SELECT win_rate, avg_pnl FROM elite_patterns WHERE symbol = ?",
                (symbol,),
            )
            was_elite_row = c.fetchone()
            was_elite = was_elite_row is not None
            old_wr = was_elite_row[0] if was_elite else None
            old_pnl = was_elite_row[1] if was_elite else None

            # Logging de auditoría
            if is_elite and not was_elite:
                # Entró a elite
                c.execute(
                    "INSERT INTO elite_audit_log (timestamp, symbol, action, reason, metrics) VALUES (?, ?, ?, ?, ?)",
                    (
                        now,
                        symbol,
                        "PROMOTED",
                        f"WR={wr}%, PnL={avg_pnl}%, Trades={trades_count}",
                        robustness_msg,
                    ),
                )
            elif veto_mae:
                # Veto por MAE - registrar siempre que se detects
                c.execute(
                    "INSERT INTO elite_audit_log (timestamp, symbol, action, reason, metrics) VALUES (?, ?, ?, ?, ?)",
                    (
                        now,
                        symbol,
                        "VETO_MAE",
                        veto_reason,
                        f"MAE={avg_mae}%, PnL={avg_pnl}%, Ratio={mae_ratio:.1f}x",
                    ),
                )
            elif not is_elite and was_elite:
                # Salió de elite
                reason = (
                    "BAJO_RENDIMIENTO" if wr < 50 or avg_pnl < 0 else "SIN_ROBUSTEZ"
                )
                c.execute(
                    "INSERT INTO elite_audit_log (timestamp, symbol, action, reason, metrics) VALUES (?, ?, ?, ?, ?)",
                    (
                        now,
                        symbol,
                        "DEMOTED",
                        reason,
                        f"Old WR={old_wr}%, New WR={wr}%, Old PnL={old_pnl}%, New PnL={avg_pnl}",
                    ),
                )

            # Calcular confidence basado en número de trades
            if trades_count >= 100:
                confidence = "HIGH"
            elif trades_count >= 50:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            if is_elite:
                # Upsert en elite_patterns (puede actualizar)
                c.execute(
                    """
                    INSERT INTO elite_patterns (symbol, rsi_avg, adx_avg, vol_rel_avg, z_score_avg, dist_ema_avg, ob_status, win_rate, avg_pnl, total_trades, confidence, first_seen, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT first_seen FROM elite_patterns WHERE symbol = ?), ?), ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        rsi_avg = excluded.rsi_avg,
                        adx_avg = excluded.adx_avg,
                        vol_rel_avg = excluded.vol_rel_avg,
                        z_score_avg = excluded.z_score_avg,
                        dist_ema_avg = excluded.dist_ema_avg,
                        win_rate = excluded.win_rate,
                        avg_pnl = excluded.avg_pnl,
                        total_trades = excluded.total_trades,
                        confidence = excluded.confidence,
                        last_updated = excluded.last_updated
                """,
                    (
                        symbol,
                        rsi_avg,
                        adx_avg,
                        vol_rel,
                        z_score,
                        dist_ema,
                        snap.get("ob_status", "⚪"),
                        wr,
                        avg_pnl,
                        trades_count,
                        confidence,
                        symbol,
                        now,
                        now,
                    ),
                )
            else:
                # Si NO es elite pero ESTÁ en elite_patterns, lo DESCLASIFICAMOS
                # (Se elimina si WR < 50% o PnL < 0 o trades < 20)
                c.execute("DELETE FROM elite_patterns WHERE symbol = ?", (symbol,))

                # Upsert en experimental_patterns
                c.execute(
                    """
                    INSERT INTO experimental_patterns (symbol, rsi_avg, adx_avg, vol_rel_avg, z_score_avg, dist_ema_avg, ob_status, win_rate, avg_pnl, total_trades, created_at, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM experimental_patterns WHERE symbol = ?), ?), ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        rsi_avg = excluded.rsi_avg,
                        adx_avg = excluded.adx_avg,
                        vol_rel_avg = excluded.vol_rel_avg,
                        z_score_avg = excluded.z_score_avg,
                        dist_ema_avg = excluded.dist_ema_avg,
                        win_rate = excluded.win_rate,
                        avg_pnl = excluded.avg_pnl,
                        total_trades = excluded.total_trades,
                        last_updated = excluded.last_updated
                """,
                    (
                        symbol,
                        rsi_avg,
                        adx_avg,
                        vol_rel,
                        z_score,
                        dist_ema,
                        snap.get("ob_status", "⚪"),
                        wr,
                        avg_pnl,
                        trades_count,
                        symbol,
                        now,
                        now,
                    ),
                )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ Error clasificando patrones: {e}")

    def validate_pattern_robustness(self, symbol):
        """
        Realiza validación cruzada (Out-of-Sample).
        Divide los trades en 2 bloques (50/50).
        Retorna True si el patrón es consistente en ambos bloques.
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Obtener todos los trades del símbolo ordenados por tiempo
            c.execute(
                "SELECT pnl_percent FROM trades WHERE symbol = ? AND pnl_percent IS NOT NULL ORDER BY timestamp ASC",
                (symbol,),
            )
            trades = [row[0] for row in c.fetchall()]
            conn.close()

            if len(trades) < 20:
                return False, "Insuficientes datos para validación cruzada (min 20)"

            mid = len(trades) // 2
            train_set = trades[:mid]
            test_set = trades[mid:]

            def get_stats(data):
                wins = len([t for t in data if t > 0])
                wr = (wins / len(data)) * 100
                avg = sum(data) / len(data)
                return wr, avg

            wr_train, avg_train = get_stats(train_set)
            wr_test, avg_test = get_stats(test_set)

            # --- CRITERIOS DE ROBUSTEZ ---
            # 1. El Win Rate no debe caer más del 20% respecto al entrenamiento
            # 2. El PnL promedio debe seguir siendo positivo
            is_robust = wr_test >= (wr_train * 0.8) and avg_test > 0

            status = "ROBUSTO" if is_robust else "SOBREAJUSTADO"
            return (
                is_robust,
                f"{status} (Train: {wr_train:.1f}% WR | Test: {wr_test:.1f}% WR)",
            )

        except Exception as e:
            return False, f"Error en validación: {e}"

    def get_elite_patterns(self):
        """Retorna todos los patrones elite (pueden desclasificarse si no cumplen criterios)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT * FROM elite_patterns ORDER BY win_rate DESC, total_trades DESC"
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def recalculate_elite_patterns(self):
        """
        Recalcula TODOS los elite patterns desde cero.
        Elimina los que ya no cumplen criterios y agrega los nuevos.
        Uso: Para corregir datos corruptos o al iniciar el sistema.
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Obtener símbolos que cumplen el mínimo de trades
            c.execute("""
                SELECT 
                    symbol,
                    COUNT(*) as trades_count,
                    AVG(pnl_percent) as avg_pnl,
                    100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) as win_rate
                FROM trades
                WHERE pnl_percent IS NOT NULL
                GROUP BY symbol
                HAVING trades_count >= 20
            """)

            rows = c.fetchall()
            now = datetime.now().isoformat()

            # Limpiar tabla elite primero
            c.execute("DELETE FROM elite_patterns")

            elite_count = 0
            for row in rows:
                symbol = row[0]
                trades_count = row[1]
                avg_pnl = row[2] or 0
                wr = row[3] or 0

                # Criteria: WR >= 60%, PnL > 0, 20+ trades
                if wr >= 60 and avg_pnl > 0:
                    # Calcular confidence
                    if trades_count >= 100:
                        confidence = "HIGH"
                    elif trades_count >= 50:
                        confidence = "MEDIUM"
                    else:
                        confidence = "LOW"

                    c.execute(
                        """
                        INSERT INTO elite_patterns 
                        (symbol, rsi_avg, adx_avg, vol_rel_avg, z_score_avg, dist_ema_avg, win_rate, avg_pnl, total_trades, confidence, first_seen, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            symbol,
                            50,
                            25,
                            1.0,
                            0,
                            0,
                            wr,
                            avg_pnl,
                            trades_count,
                            confidence,
                            now,
                            now,
                        ),
                    )
                    elite_count += 1

            conn.commit()
            conn.close()
            print(f"✅ Elite patterns recalculados: {elite_count} patrones válidos")
            return elite_count

        except Exception as e:
            print(f"❌ Error recalculando elite patterns: {e}")
            return 0

    def get_experimental_patterns(self):
        """Retorna patrones experimentales."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT * FROM experimental_patterns ORDER BY win_rate DESC, total_trades DESC"
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def cleanup_old_patterns(self, days=30):
        """Limpia patrones experimentales mayores a X días. Para elite_patterns no hace nada."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            cutoff = datetime.now() - timedelta(days=days)

            # Solo limpiamos experimental_patterns
            c.execute(
                "DELETE FROM experimental_patterns WHERE created_at < ?",
                (cutoff.isoformat(),),
            )

            deleted = c.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                print(
                    f"🧹 Limpiados {deleted} patrones experimentales mayores a {days} días"
                )
            return deleted
        except Exception as e:
            print(f"⚠️ Error limpiando patrones: {e}")
            return 0

    def get_pattern_weight(self, symbol):
        """Retorna el peso del patrón (70% elite, 30% experimental)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Verificar si es elite
            c.execute(
                "SELECT win_rate, total_trades FROM elite_patterns WHERE symbol = ?",
                (symbol,),
            )
            elite = c.fetchone()

            if elite:
                conn.close()
                return 0.70  # 70% peso para elite

            # Verificar si es experimental
            c.execute(
                "SELECT win_rate, total_trades FROM experimental_patterns WHERE symbol = ?",
                (symbol,),
            )
            exp = c.fetchone()

            conn.close()

            if exp:
                return 0.30  # 30% peso para experimental

            return 0.15  # Default si no hay historial
        except Exception:
            return 0.15

    def get_agent_reputation(self, context_type=None):
        """Recupera la reputación de los agentes, opcionalmente filtrada por contexto."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            if context_type:
                # Buscamos reputación específica para este contexto (Autopsia Contextual)
                c.execute(
                    "SELECT agent_id, reputation FROM agent_reputation_context WHERE context_type = ?",
                    (context_type,),
                )
                rows = c.fetchall()
                # Si no hay datos suficientes para este contexto, hacemos fallback a la general
                if not rows:
                    c.execute("SELECT agent_id, reputation FROM agent_reputation")
                    rows = c.fetchall()
            else:
                c.execute("SELECT agent_id, reputation FROM agent_reputation")
                rows = c.fetchall()

            conn.close()
            return {row["agent_id"]: row["reputation"] for row in rows}
        except Exception:
            return {
                a: 100.0
                for a in [
                    "MT",
                    "SR",
                    "G",
                ]
            }

    def update_agent_reputation(
        self, agent_votes, pnl_percent, context_type="ALCISTA_VOLATIL"
    ):
        """
        [v109 SMART] Ajusta la confianza que el bot tiene en cada experto.
        Lógica: Win y voto > 70 (+1) | Loss y voto > 70 (-2 penaliza doble).
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            for agent, vote in agent_votes.items():
                impact = 0
                if pnl_percent > 0 and vote > 70:
                    impact = 1.0  # El agente tuvo razón: Premia
                elif pnl_percent < 0 and vote > 70:
                    impact = -2.0  # El agente nos engañó: Penaliza doble

                if impact != 0:
                    # 1. Reputación General
                    c.execute(
                        """
                        UPDATE agent_reputation 
                        SET reputation = MAX(10.0, MIN(200.0, reputation + ?)),
                            total_trades = total_trades + 1,
                            wins = wins + (CASE WHEN ? > 0 THEN 1 ELSE 0 END),
                            losses = losses + (CASE WHEN ? < 0 THEN 1 ELSE 0 END)
                        WHERE agent_id = ?
                    """,
                        (impact, pnl_percent, pnl_percent, agent),
                    )

                    # 2. Reputación Contextual (Regímenes)
                    c.execute(
                        """
                        INSERT INTO agent_reputation_context (agent_id, context_type, reputation, total_trades)
                        VALUES (?, ?, 100.0 + ?, 1)
                        ON CONFLICT(agent_id, context_type) DO UPDATE SET
                            reputation = MAX(10.0, MIN(200.0, reputation + ?)),
                            total_trades = total_trades + 1
                    """,
                        (agent, context_type, impact, impact),
                    )

            conn.commit()
            conn.close()

            self.update_meta_learning(agent_votes, pnl_percent, context_type)
        except Exception as e:
            print(f"❌ Error actualizando reputación: {e}")

    def update_meta_learning(self, agent_votes, pnl_percent, context_type):
        """
        [META-APRENDIZAJE v118]
        Los agentes aprenden de sus propios errores ajustando sus thresholds.
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            is_win = pnl_percent > 0

            for agent, vote in agent_votes.items():
                c.execute(
                    """
                    INSERT INTO agent_meta_learning (agent_id, context_type, avg_vote_when_wrong, avg_vote_when_right, sample_count, optimal_threshold)
                    VALUES (?, ?, ?, ?, 1, 60.0)
                    ON CONFLICT(agent_id, context_type) DO UPDATE SET
                        avg_vote_when_wrong = (avg_vote_when_wrong * sample_count + ?) / (sample_count + 1),
                        avg_vote_when_right = (avg_vote_when_right * sample_count + ?) / (sample_count + 1),
                        sample_count = sample_count + 1,
                        optimal_threshold = CASE 
                            WHEN ? > 0 AND avg_vote_when_right > avg_vote_when_wrong THEN MIN(80.0, optimal_threshold + 0.5)
                            WHEN ? < 0 AND avg_vote_when_wrong > avg_vote_when_right THEN MAX(40.0, optimal_threshold - 0.5)
                            ELSE optimal_threshold
                        END
                """,
                    (
                        agent,
                        context_type,
                        vote if not is_win else 50.0,
                        vote if is_win else 50.0,
                        vote if not is_win else 50.0,
                        vote if is_win else 50.0,
                        pnl_percent,
                        pnl_percent,
                    ),
                )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ Meta-aprendizaje error: {e}")

    def get_agent_performance(self, context_type=None, primary_ids=None):
        """
        [ROLLING REPUTATION v118.6] - Fase 3.1
        Calcula el rendimiento individual de cada agente en los últimos 50 trades.
        Implementa Meta-Learning Dinámico (Ventanilla Deslizante).
        NOTA: Incluye tanto trades REALES como SHADOW para una adaptación ultra-rápida.

        Args:
            context_type: Contexto para filtrar (opcional).
            primary_ids: Lista de IDs de agente a devolver. Si es None, usa los
                         IDs legacy. El caller principal (orchestrator) pasa
                         ["MT", "SR", "G"].
        """
        legacy_agents = ["T", "V", "J", "G", "C", "L", "F", "S", "O", "R", "M", "D", "E", "K"]
        if primary_ids is None:
            agents = legacy_agents
        else:
            agents = primary_ids
        performance = {a: 100.0 for a in agents}

        try:
            import json

            conn = self._get_conn()
            conn.row_factory = lambda cursor, row: dict(
                zip([col[0] for col in cursor.description], row)
            )
            c = conn.cursor()

            # 1. Obtener los últimos 50 trades que tengan snapshot (votos)
            c.execute("""
                SELECT pnl_percent, market_snapshot 
                FROM trades 
                WHERE market_snapshot IS NOT NULL 
                ORDER BY timestamp DESC 
                LIMIT 50
            """)
            rows = c.fetchall()
            conn.close()

            if not rows:
                return performance

            # 2. Rastrear aciertos (votos > 50 en trades ganadores o votos < 50 en perdedores)
            #    Primero intentamos primary_ids; si no hay match, caemos a legacy_agents
            #    para mantener compatibilidad con snapshots viejos en la DB.
            if primary_ids:
                all_candidates = [primary_ids, legacy_agents]
            else:
                all_candidates = [legacy_agents]

            hits = {a: 0 for a in agents}
            totals = {a: 0 for a in agents}
            matched_any = {a: False for a in agents}

            for row in rows:
                try:
                    pnl = row["pnl_percent"]
                    snap = json.loads(row["market_snapshot"])
                    votos = snap.get("votos", {})

                    if not votos:
                        continue

                    for candidate_list in all_candidates:
                        for a in candidate_list:
                            if a in votos:
                                if a not in totals:
                                    continue  # skip agents not in our output set
                                voto = votos[a]
                                totals[a] += 1
                                matched_any[a] = True
                                if (pnl > 0 and voto >= 50) or (pnl < 0 and voto < 50):
                                    hits[a] += 1
                                break  # prefer first match in priority order
                except Exception:
                    continue

            # 3. Calcular Score (Basado en Win Rate reciente)
            for a in agents:
                if totals[a] > 0:
                    wr = hits[a] / totals[a]
                    performance[a] = wr * 200.0
                else:
                    performance[a] = 100.0

            return performance

        except Exception as e:
            print(f"⚠️ Rolling Reputation error: {e}")
            return {a: 100.0 for a in agents}

    def get_genetic_history(self, symbol):
        """Recupera el historial de mutaciones de un par."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT timestamp, sl_mult, tp_mult, mutation_type FROM genetic_history WHERE symbol = ? ORDER BY id DESC LIMIT 10",
                (symbol,),
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error recuperando historial genético: {e}")
            return []

    def evolve_genetics(self, symbol):
        """Punto #3: Algoritmo Genético. Muta parámetros basado en supervivencia."""
        try:
            stats = self.get_symbol_stats(symbol)
            if stats["count"] < 5:
                return False

            wr = stats["wr_short"]
            avg_pnl = stats["avg_pnl"]
            fitness = wr * avg_pnl

            current = self.get_genetic_params(symbol)
            if not current:
                current = {"sl_mult": 1.0, "tp_mult": 2.0}  # Base defaults

            new_sl, new_tp = current["sl_mult"], current["tp_mult"]
            mutated = False
            m_type = "AUTO"

            # A. Mutación por Supervivencia (Si va mal)
            if wr < 40 or avg_pnl < 0:
                mutated = True
                m_type = "SURVIVAL"
                if random.random() > 0.5:
                    new_sl *= random.uniform(1.1, 1.3)  # Más aire
                else:
                    new_tp *= random.uniform(0.8, 0.95)  # TP más corto
            # B. Mutación por Codicia (Si va bien)
            elif wr > 70 and avg_pnl > 0.5:
                mutated = True
                m_type = "GREED"
                if random.random() > 0.5:
                    new_sl *= random.uniform(0.9, 1.0)  # SL más ajustado
                else:
                    new_tp *= random.uniform(1.05, 1.2)  # TP más lejos

            if mutated:
                new_sl = max(0.2, min(4.0, new_sl))
                new_tp = max(0.5, min(8.0, new_tp))
                self.update_genetic_params(
                    symbol, new_sl, new_tp, fitness, mutation_type=m_type
                )
                return True
        except Exception as e:
            print(f"⚠️ Genetic Error: {e}")
        return False

    def get_stats(self):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT count(*) FROM trades WHERE is_shadow = 0 AND pnl_percent != -99.0"
            )
            row_real = c.fetchone()
            c.execute(
                "SELECT count(*) FROM trades WHERE is_shadow = 1 AND pnl_percent != -99.0"
            )
            row_shadow = c.fetchone()

            # --- CALCULO DE WIN RATE DE SOMBRA RECIENTE (v105.5.1) ---
            # Excluimos -99.0 (Vetos/Errores) para no sesgar el éxito de la IA
            c.execute(
                "SELECT pnl_percent FROM trades WHERE is_shadow = 1 AND pnl_percent != -99.0 ORDER BY id DESC LIMIT 50"
            )
            shadow_rows = c.fetchall()

            c.execute(
                "SELECT pnl_percent FROM trades WHERE is_shadow = 0 AND pnl_percent != -99.0 ORDER BY id DESC LIMIT 50"
            )
            real_rows = c.fetchall()

            swr = 50.0  # Default neutral
            if shadow_rows:
                wins = sum(1 for r in shadow_rows if r["pnl_percent"] > 0)
                swr = (wins / len(shadow_rows)) * 100

            rwr = None
            if real_rows:
                real_wins = sum(1 for r in real_rows if r["pnl_percent"] > 0)
                rwr = (real_wins / len(real_rows)) * 100

            conn.close()
            return {
                "total_trades": row_real[0] or 0,
                "shadow_trades": row_shadow[0] or 0,
                "shadow_win_rate": swr,
                "real_win_rate": rwr,
            }
        except Exception:
            return {
                "total_trades": 0,
                "shadow_trades": 0,
                "shadow_win_rate": 50.0,
                "real_win_rate": None,
            }

    def get_last_n_trades(self, limit=100):
        """Recupera los últimos N trades cerrados para auditoría."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                SELECT symbol, side, entry_price, exit_price, pnl_percent, pnl, timestamp, is_shadow, fees
                FROM trades 
                WHERE side IN ('BUY', 'SELL') 
                ORDER BY id DESC LIMIT ?
            """,
                (limit,),
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error recuperando últimos {limit} trades: {e}")
            return []

    def save_active_trade_state(self, symbol, state_data):
        return run_save_active_trade_state(self, symbol, state_data)

    def load_active_trade_states(self):
        return run_load_active_trade_states(self)

    def delete_active_trade_state(self, symbol):
        return run_delete_active_trade_state(self, symbol)

    def upsert_confidence_exit_audit(self, event_data: dict):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            now_iso = datetime.now().isoformat()
            entry_client_order_id = event_data.get("entry_client_order_id")
            if not entry_client_order_id:
                conn.close()
                return None

            current = c.execute(
                "SELECT id, defer_count, first_floor_ts FROM confidence_exit_audit WHERE entry_client_order_id = ?",
                (entry_client_order_id,),
            ).fetchone()
            defer_increment = int(event_data.get("defer_increment", 0) or 0)
            defer_count = int((current[1] if current else 0) or 0) + defer_increment
            first_floor_ts = event_data.get("first_floor_ts") or (
                current[2] if current and current[2] else now_iso
            )

            c.execute(
                """
                INSERT INTO confidence_exit_audit (
                    entry_client_order_id, symbol, side, is_shadow, entry_price, amount,
                    entry_time, entry_confidence, floor_confidence, confidence_drop_pct,
                    floor_price, gross_pnl_at_conf_drop_usd, gross_pnl_at_conf_drop_pct,
                    fee_floor_usd, fee_floor_pct, fee_noise_zone, guard_reason,
                    trigger_reason, votes_json, dominant_killer, first_floor_ts,
                    defer_count, last_defer_ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_client_order_id) DO UPDATE SET
                    floor_confidence = excluded.floor_confidence,
                    confidence_drop_pct = excluded.confidence_drop_pct,
                    floor_price = excluded.floor_price,
                    gross_pnl_at_conf_drop_usd = excluded.gross_pnl_at_conf_drop_usd,
                    gross_pnl_at_conf_drop_pct = excluded.gross_pnl_at_conf_drop_pct,
                    fee_floor_usd = excluded.fee_floor_usd,
                    fee_floor_pct = excluded.fee_floor_pct,
                    fee_noise_zone = excluded.fee_noise_zone,
                    guard_reason = excluded.guard_reason,
                    trigger_reason = excluded.trigger_reason,
                    votes_json = excluded.votes_json,
                    dominant_killer = excluded.dominant_killer,
                    defer_count = excluded.defer_count,
                    last_defer_ts = excluded.last_defer_ts
                """,
                (
                    entry_client_order_id,
                    event_data.get("symbol"),
                    event_data.get("side"),
                    1 if event_data.get("is_shadow") else 0,
                    event_data.get("entry_price"),
                    event_data.get("amount"),
                    event_data.get("entry_time"),
                    event_data.get("entry_confidence"),
                    event_data.get("floor_confidence"),
                    event_data.get("confidence_drop_pct"),
                    event_data.get("floor_price"),
                    event_data.get("gross_pnl_at_conf_drop_usd"),
                    event_data.get("gross_pnl_at_conf_drop_pct"),
                    event_data.get("fee_floor_usd"),
                    event_data.get("fee_floor_pct"),
                    1 if event_data.get("fee_noise_zone") else 0,
                    event_data.get("guard_reason"),
                    event_data.get("trigger_reason"),
                    json.dumps(event_data.get("votes") or {}),
                    event_data.get("dominant_killer"),
                    first_floor_ts,
                    defer_count,
                    now_iso if defer_increment > 0 else event_data.get("last_defer_ts"),
                ),
            )
            audit_id = c.execute(
                "SELECT id FROM confidence_exit_audit WHERE entry_client_order_id = ?",
                (entry_client_order_id,),
            ).fetchone()[0]
            conn.commit()
            conn.close()
            return audit_id
        except Exception as e:
            print(f"❌ Error upsert confidence exit audit: {e}")
            return None

    def finalize_confidence_exit_audit(
        self,
        entry_client_order_id: str,
        trade_id: int,
        final_reason: str,
        final_pnl_usd: float,
        final_pnl_percent: float,
    ):
        try:
            if not entry_client_order_id:
                return
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                UPDATE confidence_exit_audit
                SET final_trade_id = ?,
                    final_ts = ?,
                    final_reason = ?,
                    final_pnl_usd = ?,
                    final_pnl_percent = ?
                WHERE entry_client_order_id = ?
                """,
                (
                    trade_id,
                    datetime.now().isoformat(),
                    final_reason,
                    final_pnl_usd,
                    final_pnl_percent,
                    entry_client_order_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error finalizando confidence exit audit: {e}")

    def get_recent_exit_confidence_stagnation(self, limit: int = 10):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            rows = c.execute(
                """
                SELECT exit_confidence
                FROM trades
                WHERE exit_confidence IS NOT NULL
                  AND exit_confidence > 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            conn.close()
            values = [float(r["exit_confidence"] or 0.0) for r in rows]
            if len(values) < limit:
                return None
            arr = np.array(values, dtype=float)
            return {
                "count": len(values),
                "mean": float(arr.mean()),
                "stddev": float(arr.std()),
                "min": float(arr.min()),
                "max": float(arr.max()),
            }
        except Exception as e:
            print(f"❌ Error leyendo estancamiento de confianza: {e}")
            return None

    def get_recent_performance(self, last_n=5):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT pnl_percent FROM trades WHERE is_shadow = 0 ORDER BY id DESC LIMIT ?",
                (last_n,),
            )
            rows = c.fetchall()
            conn.close()

            if not rows:
                return 0, 0

            wins = sum(1 for row in rows if row["pnl_percent"] > 0)
            losses = len(rows) - wins
            return wins, losses
        except Exception:
            return 0, 0

    def get_hourly_performance(self):
        """Analiza el rendimiento por hora del día."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("""
                SELECT timestamp, pnl_percent 
                FROM trades 
                WHERE timestamp IS NOT NULL
            """)
            rows = c.fetchall()
            conn.close()

            from collections import defaultdict

            hourly_stats = defaultdict(lambda: {"wins": 0, "total": 0})

            for row in rows:
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                    hour = ts.hour
                    hourly_stats[hour]["total"] += 1
                    if row["pnl_percent"] > 0:
                        hourly_stats[hour]["wins"] += 1
                except (ValueError, TypeError):
                    continue  # Timestamp inválido, saltar

            return dict(hourly_stats)
        except Exception:
            return {}

    def get_sector_performance(self):
        """Analiza el rendimiento por sector usando market_snapshot."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("""
                SELECT symbol, pnl_percent 
                FROM trades 
                WHERE symbol IS NOT NULL
            """)
            rows = c.fetchall()
            conn.close()

            from collections import defaultdict
            from config import Config

            sector_stats = defaultdict(lambda: {"wins": 0, "total": 0, "pnl_sum": 0.0})

            for row in rows:
                symbol_base = row["symbol"].split("/")[0]
                sector = "OTHE"

                if hasattr(Config, "SECTORS"):
                    for sec_name, coins in Config.SECTORS.items():
                        if any(coin.lower() in symbol_base.lower() for coin in coins):
                            sector = sec_name
                            break

                sector_stats[sector]["total"] += 1
                sector_stats[sector]["pnl_sum"] += row["pnl_percent"]
                if row["pnl_percent"] > 0:
                    sector_stats[sector]["wins"] += 1

            return dict(sector_stats)
        except Exception:
            return {}

    def update_hourly_blacklist(self, hours):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("DELETE FROM hourly_blacklist")
            for h in hours:
                c.execute(
                    "INSERT INTO hourly_blacklist (hour, reason) VALUES (?, ?)",
                    (h, "Low Win Rate"),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando blacklist horaria: {e}")

    def get_hourly_blacklist(self):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT hour FROM hourly_blacklist")
            rows = c.fetchall()
            conn.close()
            return [row["hour"] for row in rows]
        except Exception:
            return []

    def update_sector_blacklist(self, sectors):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("DELETE FROM sector_blacklist")
            for s in sectors:
                c.execute(
                    "INSERT INTO sector_blacklist (sector, reason) VALUES (?, ?)",
                    (s, "Low Performance"),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando blacklist de sectores: {e}")

    def get_sector_blacklist(self):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT sector FROM sector_blacklist")
            rows = c.fetchall()
            conn.close()
            return [row["sector"] for row in rows]
        except Exception:
            return []

    def update_symbol_blacklist(self, symbols):
        """Actualiza la lista de símbolos vetados."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("DELETE FROM symbol_blacklist")
            from datetime import datetime

            for s in symbols:
                c.execute(
                    "INSERT INTO symbol_blacklist (symbol, reason, added_date) VALUES (?, ?, ?)",
                    (s, "Low Performance", datetime.now().strftime("%Y-%m-%d")),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando blacklist de símbolos: {e}")

    def get_symbol_blacklist(self):
        """Obtiene la lista de símbolos vetados."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT symbol FROM symbol_blacklist")
            rows = c.fetchall()
            conn.close()
            return [row["symbol"] for row in rows]
        except Exception:
            return []

    def auto_blacklist_poor_performers(
        self, min_trades=5, max_loss_pct=-5.0, max_wr=40.0
    ):
        """
        [v118] Auto-blacklist símbolos con mal rendimiento.
         - Símbolos con menos de X% de WR en los últimos Y trades
         - Símbolos con PnL promedio negativo mayor al threshold
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Buscar símbolos con bajo rendimiento
            c.execute(
                """
                SELECT 
                    symbol,
                    COUNT(*) as trades,
                    AVG(pnl_percent) as avg_pnl,
                    SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
                FROM trades 
                WHERE is_shadow = 0 AND symbol IS NOT NULL
                GROUP BY symbol
                HAVING trades >= ? AND (wr < ? OR avg_pnl < ?)
            """,
                (min_trades, max_wr, max_loss_pct),
            )

            rows = c.fetchall()
            conn.close()

            # Obtener blacklist actual
            current_blacklist = self.get_symbol_blacklist()
            new_blacklist = list(current_blacklist)

            symbols_to_blacklist = []
            for row in rows:
                symbol = row["symbol"].split("/")[0]  # Quitar /USDT
                if symbol not in current_blacklist:
                    symbols_to_blacklist.append(symbol)
                    new_blacklist.append(symbol)
                    print(
                        f"⚠️ Auto-Blacklist: {symbol} (WR: {row['wr']:.1f}%, Avg: {row['avg_pnl']:.2f}%)"
                    )

            if symbols_to_blacklist:
                self.update_symbol_blacklist(new_blacklist)
                print(
                    f"✅ Auto-Blacklist actualizada: {len(symbols_to_blacklist)} símbolos añadidos"
                )
                return symbols_to_blacklist

            return []
        except Exception as e:
            print(f"❌ Error en auto_blacklist: {e}")
            return []

    def get_last_train_timestamp(self):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT value FROM system_meta WHERE key = 'last_train_timestamp'"
            )
            row = c.fetchone()
            conn.close()
            if row and row["value"]:
                return datetime.fromisoformat(row["value"])
            # Si no existe, devolvemos una fecha muy antigua para forzar el primer entrenamiento
            return datetime(2000, 1, 1)
        except Exception:
            return datetime(2000, 1, 1)

    def update_last_train_timestamp(self, timestamp):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO system_meta (key, value)
                VALUES (?, ?)
            """,
                ("last_train_timestamp", timestamp.isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando timestamp de entrenamiento: {e}")

    def get_metadata(self, key):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT value FROM system_meta WHERE key = ?", (key,))
            row = c.fetchone()
            conn.close()
            if row and row["value"]:
                val = row["value"]
                # 1. Intentar parsear como ISO datetime
                try:
                    return datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    ...

                # 2. Intentar parsear como entero
                if isinstance(val, str) and (
                    val.isdigit() or (val.startswith("-") and val[1:].isdigit())
                ):
                    return int(val)

                # 3. Intentar parsear como float
                try:
                    return float(val)
                except (ValueError, TypeError):
                    ...

                return val
            return None
        except Exception:
            return None

    def set_metadata(self, key, value):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            val_str = value.isoformat() if isinstance(value, datetime) else str(value)
            c.execute(
                """
                INSERT OR REPLACE INTO system_meta (key, value)
                VALUES (?, ?)
            """,
                (key, val_str),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando metadata {key}: {e}")

    def get_metadata_json(self, key, default=None):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT value FROM system_meta WHERE key = ?", (key,))
            row = c.fetchone()
            conn.close()
            if not row or not row["value"]:
                return default
            return json.loads(row["value"])
        except Exception:
            return default

    def set_metadata_json(self, key, value):
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO system_meta (key, value)
                VALUES (?, ?)
            """,
                (key, json.dumps(value, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando metadata JSON {key}: {e}")

    def get_daily_real_pnl(self, current_balance=1.0):
        """Calcula el rendimiento real en base al capital total ($)"""
        try:
            hoy = datetime.now().strftime("%Y-%m-%d")
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT SUM(pnl) FROM trades WHERE is_shadow=0 AND timestamp LIKE ?",
                (f"{hoy}%",),
            )
            usd_res = c.fetchone()[0]
            conn.close()

            usd_hoy = float(usd_res) if usd_res else 0.0
            percent_real = (
                (usd_hoy / current_balance * 100) if current_balance > 0 else 0.0
            )
            return percent_real, usd_hoy
        except Exception as e:
            print(f"❌ Error obteniendo PnL diario: {e}")
            return None, None

    def get_weekly_stats(self):
        """Obtiene estadísticas de la semana actual (tendencia)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Obtener inicio de semana (lunes)
            today = datetime.now()
            start_of_week = today - timedelta(days=today.weekday())
            start_date = start_of_week.strftime("%Y-%m-%d")

            c.execute(
                """
                SELECT 
                    COUNT(*) as trades,
                    ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
                    ROUND(SUM(pnl_percent), 2) as pnl,
                    ROUND(AVG(pnl_percent), 2) as avg_pnl
                FROM trades 
                WHERE is_shadow = 0 AND timestamp >= ?
            """,
                (start_date,),
            )

            row = c.fetchone()
            conn.close()

            if not row or row[0] == 0:
                return {"wr": 0, "pnl": 0, "trades": 0, "drift": "NINGUNO"}

            # Detectar drift (si WR < 40%)
            drift = "NINGUNO"
            if row[1] < 40:
                drift = "BAJO WR"
            elif row[2] < -5:
                drift = "NEGATIVO"

            return {
                "wr": row[1] or 0,
                "pnl": row[2] or 0,
                "trades": row[0] or 0,
                "avg_pnl": row[3] or 0,
                "drift": drift,
            }
        except Exception:
            return {"wr": 0, "pnl": 0, "trades": 0, "drift": "ERROR"}

    def get_monthly_stats(self):
        """Obtiene estadísticas del mes actual (tendencia)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Obtener inicio de mes
            today = datetime.now()
            start_date = today.strftime("%Y-%m-01")

            c.execute(
                """
                SELECT 
                    COUNT(*) as trades,
                    ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
                    ROUND(SUM(pnl_percent), 2) as pnl,
                    ROUND(AVG(pnl_percent), 2) as avg_pnl
                FROM trades 
                WHERE is_shadow = 0 AND timestamp >= ?
            """,
                (start_date,),
            )

            row = c.fetchone()

            # Obtener mejores y peores símbolos del mes
            c.execute(
                """
                SELECT 
                    symbol,
                    ROUND(AVG(pnl_percent), 2) as avg_pnl,
                    ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
                    COUNT(*) as cnt
                FROM trades 
                WHERE is_shadow = 0 AND timestamp >= ?
                GROUP BY symbol
                HAVING cnt >= 2
                ORDER BY avg_pnl DESC
                LIMIT 5
            """,
                (start_date,),
            )

            best = [{"symbol": r[0], "pnl": r[1], "wr": r[2]} for r in c.fetchall()]

            c.execute(
                """
                SELECT 
                    symbol,
                    ROUND(AVG(pnl_percent), 2) as avg_pnl,
                    ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
                    COUNT(*) as cnt
                FROM trades 
                WHERE is_shadow = 0 AND timestamp >= ?
                GROUP BY symbol
                HAVING cnt >= 2
                ORDER BY avg_pnl ASC
                LIMIT 5
            """,
                (start_date,),
            )

            worst = [{"symbol": r[0], "pnl": r[1], "wr": r[2]} for r in c.fetchall()]

            conn.close()

            if not row or row[0] == 0:
                return {
                    "wr": 0,
                    "pnl": 0,
                    "trades": 0,
                    "best_symbols": [],
                    "worst_symbols": [],
                }

            return {
                "wr": row[1] or 0,
                "pnl": row[2] or 0,
                "trades": row[0] or 0,
                "avg_pnl": row[3] or 0,
                "best_symbols": best,
                "worst_symbols": worst,
            }
        except Exception:
            return {
                "wr": 0,
                "pnl": 0,
                "trades": 0,
                "best_symbols": [],
                "worst_symbols": [],
            }

    def reset_daily_stats(self):
        """Limpia el historial de trades de hoy."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            hoy = datetime.now().strftime("%Y-%m-%d")
            c.execute("DELETE FROM trades WHERE timestamp LIKE ?", (f"{hoy}%",))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error reseteando estadísticas diarias: {e}")
            return False

    def get_symbol_stats(self, symbol):
        """Recupera estadísticas históricas detalladas (RAG / Memoria Corto Plazo)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            # Recuperamos PnL de los últimos 50 trades
            c.execute(
                "SELECT pnl_percent FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT 50",
                (symbol,),
            )
            rows = c.fetchall()
            conn.close()

            if not rows:
                return {"wr_short": 50.0, "wr_medium": 50.0, "avg_pnl": 0.0, "count": 0}

            # Memoria de Corto Plazo (Últimos 10 trades)
            recent_rows = rows[:10]
            wins_short = sum(1 for r in recent_rows if r["pnl_percent"] > 0)
            wr_short = (wins_short / len(recent_rows)) * 100 if recent_rows else 50.0

            # Memoria de Mediano Plazo (Últimos 50 trades)
            wins_medium = sum(1 for r in rows if r["pnl_percent"] > 0)
            wr_medium = (wins_medium / len(rows)) * 100

            # Rentabilidad Promedio
            avg_pnl = sum(r["pnl_percent"] for r in rows) / len(rows)

            return {
                "wr_short": wr_short,
                "wr_medium": wr_medium,
                "avg_pnl": avg_pnl,
                "count": len(rows),
            }
        except Exception:
            return {"wr_short": 50.0, "wr_medium": 50.0, "avg_pnl": 0.0, "count": 0}

    def check_consecutive_losses(self, symbol, limit=3):
        """Strike System: Devuelve True si las últimas 'limit' operaciones fueron pérdidas."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT pnl_percent FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
            rows = c.fetchall()
            conn.close()

            if len(rows) < limit:
                return False
            # Si todas las filas recuperadas tienen PnL negativo, es un Strike
            return all(r["pnl_percent"] < 0 for r in rows)
        except Exception:
            return False

    def get_todays_trades(self):
        """Obtiene todos los trades cerrados hoy."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT * FROM trades WHERE timestamp LIKE ?", (f"{today}%",))
            rows = c.fetchall()
            conn.close()
            # Convert rows to dicts
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error obteniendo trades de hoy: {e}")
            return []

    def get_model_insights(self):
        """Extrae la lógica actual del modelo Random Forest."""
        try:
            import pandas as pd

            model = safe_pickle_load("ghost_brain.pkl")

            # 1. Obtener importancia de indicadores
            # [GHOST v2] Actualizado para coincidir con ghost_trainer.py
            features = [
                "rsi",
                "adx",
                "vol_rel",
                "dist_ema",
                "bb_pos",
                "z_score",
                "atr_pct",
            ]

            # Validación de seguridad por si el modelo es antiguo
            if len(model.feature_importances_) != len(features):
                features = (
                    ["rsi", "adx", "vol_rel"]
                    if len(model.feature_importances_) == 3
                    else features[: len(model.feature_importances_)]
                )

            importances = zip(features, model.feature_importances_)
            sorted_features = sorted(importances, key=lambda x: x[1], reverse=True)

            # 2. Generar una "Regla Descubierta" basada en los últimos 50 trades ganadores
            conn = self._get_conn()
            best_trades = pd.read_sql_query(
                "SELECT symbol FROM trades WHERE pnl_percent > 3 ORDER BY id DESC LIMIT 5",
                conn,
            )
            conn.close()

            symbols = ", ".join(best_trades["symbol"].unique())
            top_feat = sorted_features[0][0].upper()

            learned_rule = f"Alta efectividad en {symbols} usando {top_feat} como filtro principal."

            return {"top_features": sorted_features[:3], "learned_rule": learned_rule}
        except Exception as e:
            return {
                "top_features": [],
                "learned_rule": f"Cerebro aún en formación: {e}",
            }

    def get_stats_by_trend(self):
        """Calcula el rendimiento real y shadow por cada tipo de tendencia."""
        try:
            conn = self._get_conn()
            # Usamos json_extract para entrar en el snapshot de cada trade
            query = """
                SELECT 
                    json_extract(market_snapshot, '$.trend') as trend_type,
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(pnl_percent) as avg_pnl
                FROM trades
                WHERE market_snapshot IS NOT NULL AND json_extract(market_snapshot, '$.trend') IS NOT NULL
                GROUP BY trend_type
            """
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            conn.close()

            stats = {}
            for row in rows:
                label = row[0] or "INDETERMINADO"
                total = row[1]
                wins = row[2]
                avg_pnl = row[3]
                winrate = (wins / total * 100) if total > 0 else 0
                stats[label] = {
                    "total": total,
                    "winrate": round(winrate, 1),
                    "avg_pnl": round(avg_pnl, 2),
                }
            return stats
        except Exception:
            return {}

    def rotate_history(self, days_to_keep=90):
        """Mueve trades antiguos a sniper_archive.db y limpia la base principal."""
        try:
            from datetime import timedelta

            limite = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            # FASE 4: Backup con fecha
            backup_name = (
                f"history/trades_backup_{datetime.now().strftime('%Y%m%d')}.db"
            )
            os.makedirs("history", exist_ok=True)

            conn = self._get_conn()
            c = conn.cursor()

            # 1. Crear/Conectar base de datos de archivo
            c.execute(f"ATTACH DATABASE '{backup_name}' AS archive")

            # 2. Crear tabla en el archivo si no existe
            c.execute("""
                CREATE TABLE IF NOT EXISTS archive.trades AS 
                SELECT * FROM main.trades WHERE 1=0
            """)

            # 3. Mover datos: Insertar en archivo y borrar de la principal
            c.execute(
                "INSERT INTO archive.trades SELECT * FROM main.trades WHERE timestamp < ?",
                (limite,),
            )
            c.execute("DELETE FROM main.trades WHERE timestamp < ?", (limite,))

            count = conn.total_changes
            conn.commit()
            c.execute("DETACH DATABASE archive")
            conn.close()

            if count > 0:
                print(f"📦 ARCHIVADO: {count} trades antiguos movidos a {backup_name}")
            return backup_name
        except Exception as e:
            print(f"❌ Error en rotación de historial: {e}")
            return "Error"

    def weekly_maintenance(self, shadow_days_to_keep=30, signal_days_to_keep=30):
        """Mantenimiento semanal: purga telemetría/alertas antiguas y VACUUM."""
        result = {
            "shadow_deleted": 0,
            "signal_deleted": 0,
            "vacuum_ok": False,
            "cutoff": None,
            "signal_cutoff": None,
            "error": None,
        }
        try:
            cutoff = (_utc_now_naive() - timedelta(days=shadow_days_to_keep)).isoformat()
            result["cutoff"] = cutoff
            signal_cutoff = (_utc_now_naive() - timedelta(days=signal_days_to_keep)).isoformat()
            result["signal_cutoff"] = signal_cutoff

            conn = self._get_conn()
            c = conn.cursor()

            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_telemetry'"
            )
            has_shadow = c.fetchone() is not None

            if has_shadow:
                c.execute("DELETE FROM shadow_telemetry WHERE timestamp < ?", (cutoff,))
                result["shadow_deleted"] = c.rowcount if c.rowcount >= 0 else 0

            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_alerts'"
            )
            has_signal_alerts = c.fetchone() is not None

            if has_signal_alerts:
                c.execute("DELETE FROM signal_alerts WHERE ts < ?", (signal_cutoff,))
                result["signal_deleted"] = c.rowcount if c.rowcount >= 0 else 0

            conn.commit()
            c.execute("VACUUM")
            conn.close()

            result["vacuum_ok"] = True
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    def get_trades_pending_post_mortem(self):
        """Recupera trades cerrados recientemente para análisis post-mortem."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            # Recuperamos trades sin análisis post-mortem
            c.execute(
                "SELECT * FROM trades WHERE post_mortem_data IS NULL AND exit_price IS NOT NULL ORDER BY id DESC LIMIT 50"
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error recovering trades for post-mortem: {e}")
            return []

    def get_trade_by_id(self, trade_id):
        """Recupera un trade específico por ID con todos los detalles."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"⚠️ Error retrieving trade {trade_id}: {e}")
            return None

    def get_similar_trades(self, rsi, adx, limit=5):
        """Busca trades similares en el historial (para contexto RAG)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                SELECT id, symbol, pnl_percent, rsi, adx, funding_rate, vol_rel, timestamp
                FROM trades 
                WHERE rsi BETWEEN ? AND ? 
                AND adx BETWEEN ? AND ?
                ORDER BY id DESC LIMIT ?
            """,
                (rsi - 10, rsi + 10, adx - 10, adx + 10, limit),
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def update_post_mortem(self, trade_id, data):
        """Guarda el análisis post-mortem en el trade."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "UPDATE trades SET post_mortem_data = ? WHERE id = ?",
                (json.dumps(data), trade_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error actualizando post-mortem: {e}")

    def get_winrate_in_window(self, hours_lookback=24, offset_hours=0):
        """Calcula el Win Rate en una ventana de tiempo específica."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            end_time = datetime.now() - timedelta(hours=offset_hours)
            start_time = end_time - timedelta(hours=hours_lookback)
            c.execute(
                "SELECT pnl_percent FROM trades WHERE timestamp BETWEEN ? AND ?",
                (start_time.isoformat(), end_time.isoformat()),
            )
            rows = c.fetchall()
            conn.close()
            if not rows or len(rows) < 5:
                return None  # Mínimo 5 trades para relevancia
            wins = sum(1 for r in rows if r["pnl_percent"] > 0)
            return (wins / len(rows)) * 100
        except Exception:
            return None

    def get_paper_trades_history(self, limit=50):
        """Recupera historial de trades NO-Shadow (Paper o Real) para auditoría."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT * FROM trades WHERE is_shadow = 0 ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error recuperando historial paper trades: {e}")
            return []

    def check_performance_drop(self):
        """Verifica si el rendimiento ha caído drásticamente (10%) en las últimas 24h."""
        current_wr = self.get_winrate_in_window(24, 0)
        prev_wr = self.get_winrate_in_window(24, 24)
        if current_wr is not None and prev_wr is not None:
            drop = prev_wr - current_wr
            if drop >= 10.0:
                return True, current_wr, prev_wr
        return False, 0.0, 0.0

    def get_elite_insights_stats(self):
        """Extrae métricas avanzadas para el Dashboard Elite Insights."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # 1. Estado del Mercado (Último snapshot)
            c.execute(
                "SELECT market_snapshot FROM trades WHERE market_snapshot IS NOT NULL ORDER BY id DESC LIMIT 1"
            )
            last_row = c.fetchone()
            market_state = "⚪ NEUTRAL"
            if last_row:
                try:
                    snap = json.loads(last_row[0])
                    trend = snap.get("trend", "RANGO")
                    market_state = (
                        "🟢 ALCISTA"
                        if trend == "UP"
                        else ("🔴 BAJISTA" if trend == "DOWN" else "🟡 LATERAL")
                    )
                except (json.JSONDecodeError, KeyError, TypeError, IndexError):
                    market_state = "⚪ NEUTRAL"  # Snapshot corrupto, usar default

            # 2. Nivel de Aprendizaje (1-10)
            c.execute("SELECT COUNT(*) FROM trades WHERE market_snapshot IS NOT NULL")
            total_xp = c.fetchone()[0]
            level = min(int(total_xp / 500) + 1, 10)  # Nivel sube cada 500 experiencias

            # 3. Micro-Simulaciones (Shadow Trades Hoy)
            today = datetime.now().strftime("%Y-%m-%d")
            c.execute(
                "SELECT COUNT(*), AVG(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) FROM trades WHERE is_shadow=1 AND timestamp LIKE ?",
                (f"{today}%",),
            )
            row_sim = c.fetchone()
            sim_count = row_sim[0] or 0
            sim_wr = (row_sim[1] or 0) * 100

            # 4. Inventario Estratégico (Wins por Tendencia)
            c.execute(
                "SELECT json_extract(market_snapshot, '$.trend') as trend, COUNT(*) FROM trades WHERE pnl_percent > 0 GROUP BY trend"
            )
            inventory = {r[0]: r[1] for r in c.fetchall()}

            # 5. Escudo de Seguridad (Vetos Totales)
            c.execute("SELECT COUNT(*) FROM trades WHERE side='VETO_ERROR'")
            veto_count = c.fetchone()[0] or 0

            conn.close()
            return {
                "market_state": market_state,
                "level": level,
                "sim_count": sim_count,
                "sim_wr": sim_wr,
                "inventory": inventory,
                "veto_count": veto_count,
            }
        except Exception as e:
            print(f"Error Elite Insights: {e}")
            return {
                "market_state": "ERROR",
                "level": 0,
                "sim_count": 0,
                "sim_wr": 0,
                "inventory": {},
                "veto_count": 0,
            }

    def check_eureka_status(self, symbol):
        """Analiza simulaciones recientes para detectar 'Eureka' (Éxito) o 'Falla' (Veto)."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            # Últimos 10 trades shadow
            c.execute(
                "SELECT pnl_percent, market_snapshot FROM trades WHERE symbol = ? AND is_shadow = 1 ORDER BY id DESC LIMIT 10",
                (symbol,),
            )
            rows = c.fetchall()
            conn.close()

            if len(rows) < 5:
                return None, {}  # Mínimo 5 datos para relevancia

            wins = sum(1 for r in rows if r["pnl_percent"] > 0)
            total = len(rows)
            wr = (wins / total) * 100

            last_snap = (
                json.loads(rows[0]["market_snapshot"])
                if rows[0]["market_snapshot"]
                else {}
            )
            trend = last_snap.get("trend", "NEUTRAL")
            context = f"RSI: {last_snap.get('rsi', 0):.0f} | ADX: {last_snap.get('adx', 0):.0f}"

            if wr >= 80.0:
                return "EUREKA", {
                    "wr": wr,
                    "count": total,
                    "trend": trend,
                    "context": context,
                }

            if wr <= 20.0 and total >= 5:
                return "FAILURE", {"wr": wr, "count": total, "trend": trend}

            return None, {}
        except Exception:
            return None, {}

    def get_contextual_performance_score(
        self, symbol: str, current_rsi: float, current_adx: float
    ) -> float:
        """
        [AGENTE JUEZ - RAG] Busca trades en situaciones similares para decidir.
        """
        try:
            # Sanitización de inputs (Cirugía Láser v107.0)
            if current_rsi is None or current_adx is None:
                return 50.0

            conn = self._get_conn()
            c = conn.cursor()

            # Buscamos trades con RSI +/- 5 y ADX +/- 5
            c.execute(
                """
                SELECT pnl_percent FROM trades 
                WHERE symbol = ? 
                AND rsi BETWEEN ? AND ? 
                AND adx BETWEEN ? AND ?
                ORDER BY id DESC LIMIT 10
            """,
                (
                    symbol,
                    current_rsi - 5,
                    current_rsi + 5,
                    current_adx - 5,
                    current_adx + 5,
                ),
            )

            rows = c.fetchall()

            if not rows or len(rows) < 3:
                # Si no hay suficiente contexto específico, caemos al Win Rate general
                c.execute(
                    "SELECT pnl_percent FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT 15",
                    (symbol,),
                )
                rows = c.fetchall()

            conn.close()

            if not rows:
                return 50.0  # Neutralidad si es un par nuevo

            wins = sum(1 for r in rows if r["pnl_percent"] > 0)
            return (wins / len(rows)) * 100
        except Exception:
            return 50.0

    def log_equity(self, balance):
        """Registra el balance actual para la curva de equidad."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            # Solo guardamos un punto por hora para no saturar
            ts = datetime.now().strftime("%Y-%m-%d %H:00:00")
            c.execute(
                "INSERT OR REPLACE INTO equity_history (timestamp, balance) VALUES (?, ?)",
                (ts, balance),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error log equity: {e}")

    def get_equity_curve(self, limit=100):
        """Recupera la historia del balance."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT timestamp, balance FROM equity_history ORDER BY timestamp ASC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
            conn.close()
            return [{"time": r["timestamp"], "value": r["balance"]} for r in rows]
        except Exception:
            return []

    def get_rag_inference(self, symbol, current_features):
        return run_rag_inference(self, symbol, current_features, Config)

    # =====================================================
    # 📊 ANÁLISIS DINÁMICO DE HORARIOS (v110.3)
    # =====================================================

    def get_optimal_hours(self, min_trades=20):
        """Retorna los horarios con mejor WR basándose en trades recientes."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Últimos 7 días
            from datetime import timedelta

            fecha_limite = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

            query = f"""
                SELECT 
                    CAST(strftime('%H', timestamp) AS INTEGER) as hora,
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(pnl_percent) as avg_pnl
                FROM trades 
                WHERE timestamp >= '{fecha_limite}' AND pnl_percent != -99.0
                GROUP BY hora
                HAVING trades >= {min_trades}
                ORDER BY (wins * 1.0 / trades) DESC
            """
            c.execute(query)
            rows = c.fetchall()
            conn.close()

            # Retornar los 4 mejores horarios
            optimal = []
            for row in rows:
                wr = (row[2] / row[1] * 100) if row[1] > 0 else 0
                optimal.append(
                    {"hour": row[0], "wr": wr, "trades": row[1], "avg_pnl": row[3]}
                )

            return optimal[:4]  # Top 4 horas
        except Exception:
            return []

    def get_worst_hours(self, min_trades=20):
        """Retorna los horarios con peor WR basándose en trades recientes."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            from datetime import timedelta

            fecha_limite = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

            query = f"""
                SELECT 
                    CAST(strftime('%H', timestamp) AS INTEGER) as hora,
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) as wins
                FROM trades 
                WHERE timestamp >= '{fecha_limite}' AND pnl_percent != -99.0
                GROUP BY hora
                HAVING trades >= {min_trades}
                ORDER BY (wins * 1.0 / trades) ASC
            """
            c.execute(query)
            rows = c.fetchall()
            conn.close()

            worst = []
            for row in rows:
                wr = (row[2] / row[1] * 100) if row[1] > 0 else 0
                worst.append({"hour": row[0], "wr": wr, "trades": row[1]})

            return worst[:4]  # Peores 4 horas
        except Exception:
            return []

    def get_pnl_history(self, limit=10):
        """Recupera el historial reciente de PnL % para el dashboard."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT pnl_percent FROM trades WHERE is_shadow = 0 AND pnl_percent IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
            conn.close()
            return [row["pnl_percent"] for row in rows]
        except Exception:
            return []

    # =====================================================
    # 💎 PRIORIZACIÓN DE SÍMBOLOS (v110.3)
    # =====================================================

    def get_symbol_performance(self, symbol):
        """Retorna el rendimiento de un símbolo específico."""
        try:
            conn = self._get_conn()
            c = conn.cursor()

            c.execute(
                """
                SELECT 
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(pnl_percent) as avg_pnl
                FROM trades 
                WHERE symbol = ? AND pnl_percent != -99.0
            """,
                (symbol,),
            )

            row = c.fetchone()
            conn.close()

            if row and row[0] > 0:
                wr = (row[1] / row[0] * 100) if row[0] > 0 else 0
                return {"trades": row[0], "wr": wr, "avg_pnl": row[2] or 0}
            return {"trades": 0, "wr": 50, "avg_pnl": 0}
        except Exception:
            return {"trades": 0, "wr": 50, "avg_pnl": 0}

    # ============================================================
    # 🧠 TRADE CONTEXT VAULT
    # ============================================================

    def save_trade_context_snapshot(
        self, symbol: str, side: str, context_json: dict,
        entry_timestamp: str, is_shadow: bool = True,
    ) -> int | None:
        try:
            clean = {
                k: v for k, v in context_json.items()
                if not hasattr(v, 'shape') and not isinstance(v, pd.DataFrame)
            }
            json_str = json.dumps(clean, default=str)

            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO trade_context_snapshots
                    (symbol, side, is_shadow, entry_timestamp, context_json, context_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, side, int(bool(is_shadow)),
                    entry_timestamp, json_str,
                    self._compute_context_hash(clean),
                ),
            )
            snapshot_id = c.lastrowid
            conn.commit()
            conn.close()
            return snapshot_id
        except Exception as e:
            print(f"❌ Error guardando trade_context_snapshot {symbol}: {e}")
            return None

    def update_trade_context_result(
        self, trade_id: int, pnl_percent: float,
        exit_timestamp: str, is_winner: int = 0,
    ) -> bool:
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                UPDATE trade_context_snapshots
                SET trade_id = ?, pnl_percent = ?,
                    exit_timestamp = ?, is_winner = ?
                WHERE id = (
                    SELECT id FROM trade_context_snapshots
                    WHERE trade_id IS NULL
                      AND symbol IN (SELECT symbol FROM trades WHERE id = ?)
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (trade_id, pnl_percent, exit_timestamp, is_winner, trade_id),
            )
            affected = c.rowcount
            conn.commit()
            conn.close()
            if affected == 0:
                print(f"⚠️ No se encontró snapshot para trade_id={trade_id}")
                return False
            return True
        except Exception as e:
            print(f"❌ Error actualizando trade_context_result {trade_id}: {e}")
            return False

    @staticmethod
    def _compute_context_hash(context: dict) -> str:
        import hashlib
        flat = []
        for k in sorted(context.keys()):
            v = context[k]
            if isinstance(v, (int, float, bool, str)):
                flat.append(f"{k}={v}")
            elif isinstance(v, (list, tuple)):
                flat.append(f"{k}={','.join(str(x)[:20] for x in v[:5])}")
            elif isinstance(v, dict):
                sub = ",".join(f"{sk}={sv}" for sk, sv in sorted(v.items())[:5])
                flat.append(f"{k}={{{sub}}}")
        raw = "|".join(flat)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_similarity_vector(context: dict) -> list[float]:
        """Extrae vector normalizado para búsqueda eficiente."""
        try:
            votos = context.get("votos") or {}
            votes_vals = [float(v) for v in votos.values() if isinstance(v, (int, float))]
            avg_vote = sum(votes_vals) / len(votes_vals) if votes_vals else 50.0

            return [
                float(context.get("prob_final", 50.0)) / 100.0,
                avg_vote / 100.0,
                float(context.get("rsi", 50.0)) / 100.0,
                min(float(context.get("adx", 0.0)) / 100.0, 1.0),
                min(float(context.get("atr_pct", 0.0)) * 10.0, 1.0),
                min(float(context.get("vol_rel", 0.0)), 1.0),
                max(-1.0, min(1.0, float(context.get("z_score", 0.0)) / 3.0)),
                max(0.0, min(1.0, float(context.get("bb_pos", 0.5)))),
                max(-1.0, min(1.0, float(context.get("funding_rate", 0.0)) * 100.0)),
                float(context.get("heuristic_confidence", 50.0)) / 100.0,
                min(float(context.get("spread", 0.0)) * 1000.0, 1.0),
                min(float(context.get("btc_dominance", 0.0)) / 100.0, 1.0),
                min(float(context.get("eth_dominance", 0.0)) / 100.0, 1.0),
                float(context.get("fear_greed_index", 50.0)) / 100.0,
                min(float(context.get("total_market_cap", 0.0)) / 5e12, 1.0),
            ]
        except Exception:
            return []

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def find_similar_contexts(
        self, context: dict, limit: int = 10,
    ) -> list[dict]:
        try:
            start_t = time.perf_counter()
            query_vec = self._extract_similarity_vector(context)
            if not query_vec:
                return []

            conn = self._get_conn()
            c = conn.cursor()
            query_side = str(context.get("side") or "").upper()
            c.execute(
                """
                SELECT id, symbol, side, pnl_percent, is_winner,
                       entry_timestamp, context_json
                FROM trade_context_snapshots
                WHERE context_json IS NOT NULL
                  AND is_winner IS NOT NULL
                  AND exit_timestamp IS NOT NULL
                  AND pnl_percent IS NOT NULL
                ORDER BY id DESC LIMIT 500
                """,
            )
            rows_meta = []
            stored_vectors = []
            for row in c.fetchall():
                if query_side and str(row["side"] or "").upper() != query_side:
                    continue
                try:
                    stored = json.loads(row["context_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                stored_vec = self._extract_similarity_vector(stored)
                if not stored_vec:
                    continue
                rows_meta.append({
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "pnl_percent": row["pnl_percent"],
                    "is_winner": row["is_winner"],
                    "entry_timestamp": row["entry_timestamp"],
                })
                stored_vectors.append(stored_vec)
            conn.close()

            if not rows_meta:
                return []

            query_arr = np.asarray(query_vec, dtype=float)
            matrix = np.asarray(stored_vectors, dtype=float)
            query_norm = float(np.linalg.norm(query_arr))
            if query_norm == 0.0:
                return []
            row_norms = np.linalg.norm(matrix, axis=1)
            similarities = np.divide(
                matrix @ query_arr,
                row_norms * query_norm,
                out=np.zeros(len(rows_meta), dtype=float),
                where=row_norms > 0.0,
            )
            scored = []
            for meta, sim in zip(rows_meta, similarities.tolist()):
                meta["similarity"] = float(sim)
                scored.append(meta)
            scored.sort(key=lambda x: x["similarity"], reverse=True)

            elapsed_ms = (time.perf_counter() - start_t) * 1000

            # Registro estructurado de decisión operativa
            if elapsed_ms > RAG_LATENCY_WARN_MS:
                print(f"⚠️ [PERF_ADVISORY] RAG lento: {elapsed_ms:.2f}ms para {len(scored)} snapshots.")
            elif elapsed_ms > RAG_LATENCY_OK_MS:
                if random.random() < 0.05:  # Log ocasional para baselines
                    print(f"⏱️ [PERF_BASE] RAG latency: {elapsed_ms:.2f}ms | Count: {len(scored)}")

            return scored[:limit]
        except Exception as e:
            print(f"⚠️ Error en find_similar_contexts: {e}")
            return []

    def cleanup_stale_snapshots(self, max_age_days: int = 30) -> int:
        try:
            conn = self._get_conn()
            c = conn.cursor()
            cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
            c.execute(
                "DELETE FROM trade_context_snapshots WHERE entry_timestamp < ?",
                (cutoff,),
            )
            deleted = c.rowcount
            conn.commit()
            conn.close()
            return deleted
        except Exception as e:
            print(f"⚠️ Error limpiando snapshots viejos: {e}")
            return 0
