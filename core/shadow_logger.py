import json
import sqlite3
import threading
import time
from datetime import datetime
from queue import Empty, Queue

from core.learning_paths import DEFAULT_DB_PATH
from tools.notifier import send_telegram_msg


class AsyncShadowLogger:
    """
    [SHADOW LOGGING v118]
    Buffer de telemetría asíncrono. Evita bloqueos en el hilo principal
    al escribir en la DB en bloque cada 15 segundos.

    Resiliencia v118:
    - Reintentos x3 con backoff exponencial ante fallos de escritura.
    - Tras 3 fallos consecutivos: Alerta Crítica a Telegram + halt de trading real.
    - Flag público `is_trading_halted()` para que main.py lo consulte en cada ciclo.
    """

    def __init__(self, brain_db_path: str = DEFAULT_DB_PATH):
        self.buffer: list[dict] = []
        self.queue: Queue[dict] = Queue()
        self.db_path = brain_db_path
        self.lock = threading.Lock()
        self.FLUSH_INTERVAL = 15  # segundos
        self.stop_event = threading.Event()
        self._trading_halted = False  # [v118] Flag de emergencia
        self.consecutive_failures = 0  # [v118] Contador de fallos persistentes
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def log(self, entry: dict):
        """Añade una entrada al buffer de telemetría."""
        self.queue.put(entry)

    def is_trading_halted(self) -> bool:
        """[v118] Retorna True si la DB falló 3 veces: el trading real debe detenerse."""
        return self._trading_halted

    def _worker(self):
        last_flush = time.time()
        while not self.stop_event.is_set():
            try:
                try:
                    entry = self.queue.get(timeout=1.0)
                    with self.lock:
                        self.buffer.append(entry)
                except Empty:
                    continue
                except Exception as error:
                    print(f"⚠️ [SHADOW] Error leyendo cola async: {error}")

                if time.time() - last_flush > self.FLUSH_INTERVAL or len(self.buffer) > 100:
                    self._flush()
                    last_flush = time.time()
            except Exception as error:
                print(f"⚠️ Error en AsyncShadowLogger worker: {error}")

    def _flush(self):
        """[v118] Flush con retry x3 + Telegram Alert + Trading Halt ante fallos persistentes."""
        with self.lock:
            if not self.buffer:
                return

            last_error = None
            for attempt in range(1, 4):  # 3 intentos
                try:
                    conn = sqlite3.connect(self.db_path, timeout=30.0)
                    c = conn.cursor()
                    c.execute("""
                        CREATE TABLE IF NOT EXISTS shadow_telemetry (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT,
                            event_type TEXT,
                            data TEXT
                        )
                    """)
                    for entry in self.buffer:
                        c.execute(
                            "INSERT INTO shadow_telemetry (timestamp, event_type, data) VALUES (?, ?, ?)",
                            (
                                datetime.now().isoformat(),
                                entry.get("type", "GENERIC"),
                                json.dumps(entry.get("data", {})),
                            ),
                        )
                    conn.commit()
                    conn.close()
                    # Éxito: resetear contadores y buffer
                    self.buffer = []
                    self.consecutive_failures = 0
                    return
                except Exception as error:
                    last_error = error
                    print(f"⚠️ [SHADOW] Intento {attempt}/3 fallido al escribir en DB: {error}")
                    if attempt < 3:
                        time.sleep(2**attempt)  # 2s, 4s de espera entre intentos

            # --- Los 3 intentos fallaron ---
            self.consecutive_failures += 1
            alert_msg = (
                "🚨 ALERTA CRÍTICA SNIPER AI 🚨\n"
                "Fallo total de escritura en la caja negra (DB) tras 3 intentos.\n"
                f"Error: {last_error}\n"
                f"Ruta DB: {self.db_path}\n"
                "⛔ TRADING REAL DETENIDO. Intervención manual requerida."
            )
            try:
                send_telegram_msg(alert_msg)
            except Exception as error:
                print(f"❌ [SHADOW] Fallo al enviar alerta Telegram: {error}")

            self._trading_halted = True
            print(
                f"❌ [SHADOW] DB inaccesible tras 3 intentos. "
                f"Trading real DETENIDO. Error: {last_error}"
            )

    def force_flush(self):
        """Forzado inmediato de buffer a disco (usado al apagar el bot)."""
        print("💾 [SHADOW] Forzando flush final...")
        while not self.queue.empty():
            try:
                self.buffer.append(self.queue.get_nowait())
            except Exception:
                break
        self._flush()

    def stop(self):
        """Detiene el trabajador y limpia el buffer."""
        self.force_flush()
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)


class LazyShadowLogger:
    """Proxy lazy para evitar threads/DB al importar learning.py."""

    def __init__(self, brain_db_path: str = DEFAULT_DB_PATH):
        self.db_path = brain_db_path
        self._instance: AsyncShadowLogger | None = None
        self._lock = threading.Lock()

    def _get(self) -> AsyncShadowLogger:
        with self._lock:
            if self._instance is None:
                self._instance = AsyncShadowLogger(self.db_path)
            assert self._instance is not None
            return self._instance

    def log(self, entry: dict):
        self._get().log(entry)

    def is_trading_halted(self) -> bool:
        if self._instance is None:
            return False
        return self._instance.is_trading_halted()

    def force_flush(self):
        if self._instance is not None:
            self._instance.force_flush()

    def stop(self):
        if self._instance is not None:
            self._instance.stop()

    def is_started(self) -> bool:
        return self._instance is not None

    def __getattr__(self, name):
        return getattr(self._get(), name)


# Proxy global: conserva API, pero no arranca thread/DB hasta el primer uso real.
shadow_logger = LazyShadowLogger()
