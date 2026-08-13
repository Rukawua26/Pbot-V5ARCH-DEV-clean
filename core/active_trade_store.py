import json
import sqlite3
import time
from contextlib import suppress
from datetime import datetime


def save_active_trade_state(brain, symbol, state_data) -> bool:
    data_to_save = state_data.copy()
    trade_key = str(data_to_save.get("trade_key") or symbol)
    data_to_save["trade_key"] = trade_key
    data_to_save["symbol"] = str(data_to_save.get("symbol") or symbol).split("|")[0]
    if "open_time" in data_to_save and isinstance(data_to_save["open_time"], datetime):
        data_to_save["open_time"] = data_to_save["open_time"].isoformat()

    for attempt in range(3):
        conn = None
        try:
            conn = brain._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO active_trades_state (symbol, state_data)
                VALUES (?, ?)
            """,
                (trade_key, json.dumps(data_to_save)),
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() and attempt < 2:
                time.sleep(0.05 * (2**attempt))
                continue
            print(f"❌ Error guardando estado de trade activo: {error}")
            return False
        except Exception as error:
            print(f"❌ Error guardando estado de trade activo: {error}")
            return False
    return False


def settle_simulated_trade_wallet(brain, symbol, wallet_key, wallet_state) -> bool:
    for attempt in range(3):
        try:
            conn = brain._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM active_trades_state WHERE symbol = ?",
                (symbol,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO system_meta (key, value) VALUES (?, ?)",
                (wallet_key, json.dumps(wallet_state, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError as error:
            with suppress(Exception):
                conn.rollback()
                conn.close()
            if "locked" in str(error).lower() and attempt < 2:
                time.sleep(0.05 * (2**attempt))
                continue
            print(f"❌ Error liquidando wallet simulado: {error}")
            return False
        except Exception as error:
            with suppress(Exception):
                conn.rollback()
                conn.close()
            print(f"❌ Error liquidando wallet simulado: {error}")
            return False
    return False


def load_active_trade_states(brain) -> dict:
    try:
        conn = brain._get_conn()
        c = conn.cursor()
        c.execute("SELECT symbol, state_data FROM active_trades_state")
        rows = c.fetchall()
        conn.close()

        loaded_states = {}
        for row in rows:
            state_data = json.loads(row["state_data"])
            if "open_time" in state_data and isinstance(state_data["open_time"], str):
                state_data["open_time"] = datetime.fromisoformat(state_data["open_time"])
            trade_key = str(state_data.get("trade_key") or row["symbol"])
            state_data["trade_key"] = trade_key
            loaded_states[trade_key] = state_data
        return loaded_states
    except Exception as error:
        print(f"❌ Error cargando estados de trades activos: {error}")
        return {}


def delete_active_trade_state(brain, symbol) -> None:
    for attempt in range(3):
        try:
            conn = brain._get_conn()
            c = conn.cursor()
            c.execute("DELETE FROM active_trades_state WHERE symbol = ?", (symbol,))
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() and attempt < 2:
                time.sleep(0.05 * (2**attempt))
                continue
            print(f"❌ Error eliminando estado de trade activo: {error}")
            return
        except Exception as error:
            print(f"❌ Error eliminando estado de trade activo: {error}")
            return
