import ccxt
import requests

from config import Config


def _build_exchange(session):
    exchange_config = {
        "apiKey": Config.BINANCE_API_KEY,
        "secret": Config.BINANCE_API_SECRET,
        "options": {
            "defaultType": "future",
            "recvWindow": 60000,
            "fetchCurrencies": False,
            "warnOnFetchOpenOrdersWithoutSymbol": False,
        },
        "enableRateLimit": True,
        "adjustForTimeDifference": True,
        "session": session,
        "timeout": 30000,
    }
    return ccxt.binance(exchange_config)


def _is_public_sandbox_limitation(error) -> bool:
    return "does not have a testnet/sandbox URL for public endpoints" in str(error)


def _is_timestamp_drift_error(error) -> bool:
    message = str(error)
    return "-1021" in message or "Timestamp for this request" in message


def _sync_exchange_time(bot, exchange, reason: str) -> None:
    if not hasattr(exchange, "load_time_difference"):
        raise RuntimeError("Exchange no soporta load_time_difference()")

    offset = exchange.load_time_difference()
    bot.log(f"⏱️ Binance time sync aplicado ({reason}): offset={offset}ms")


def _call_auth_read_with_time_resync(bot, action_name: str, call_fn):
    try:
        return call_fn()
    except Exception as error:
        if not _is_timestamp_drift_error(error):
            raise
        bot.log(f"⚠️ Binance timestamp drift en {action_name}: {error}. Resincronizando...")
        _sync_exchange_time(bot, bot.execution.exchange, action_name)
        return call_fn()


def connect_to_binance(bot):
    try:
        bot.log("Conectando a Binance...")

        if not Config.PAPER_MODE and not Config.ALLOW_REAL_TRADING:
            raise RuntimeError(
                "REAL_MODE_BLOCKED: ALLOW_REAL_TRADING no está habilitado. "
                "Configure ALLOW_REAL_TRADING=true en el entorno para operar con capital real. "
                "Esta protección evita activación accidental."
            )

        # [v118] Soporte para Testnet
        # [V118-PRO] Session pooling para evitar fugas de sockets
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=100, pool_maxsize=100, max_retries=3, pool_block=False
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        exchange = _build_exchange(session)
        if Config.USE_TESTNET:
            bot.log("⚠️ MODO TESTNET ACTIVADO")
            if not hasattr(exchange, "set_sandbox_mode"):
                raise RuntimeError(
                    "La clase de exchange actual no soporta sandbox/testnet de forma nativa."
                )
            exchange.options["disableFuturesSandboxWarning"] = True
            try:
                exchange.set_sandbox_mode(True)
            except Exception as error:
                raise RuntimeError(
                    f"No se pudo activar testnet/sandbox en Binance Futures: {error}"
                ) from error

        bot.execution.exchange = exchange
        bot.data_service.exchange = bot.execution.exchange
        try:
            bot.execution.load_markets()
        except Exception as error:
            if Config.PAPER_MODE and Config.USE_TESTNET and _is_public_sandbox_limitation(error):
                bot.log(
                    "⚠️ Sandbox/testnet no soporta endpoints públicos en este backend. "
                    "Continuando en PAPER con mercado público real."
                )
                exchange = _build_exchange(session)
                bot.execution.exchange = exchange
                bot.data_service.exchange = bot.execution.exchange
                bot.execution.load_markets()
            else:
                raise

        if Config.PAPER_MODE:
            if not float(getattr(bot, "balance", 0.0) or 0.0):
                balance_lock = getattr(bot, "balance_lock", None)
                if balance_lock:
                    with balance_lock:
                        bot.balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
                else:
                    bot.balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
            if not float(getattr(bot, "available_balance", 0.0) or 0.0):
                bot.available_balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
            if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                bot.daily_initial_balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
            if Config.BINANCE_API_KEY and Config.BINANCE_API_SECRET:
                try:
                    _call_auth_read_with_time_resync(
                        bot, "fetch_balance(PAPER)", bot.execution.fetch_balance
                    )
                    bot.log("✅ Conectado: API Keys válidas y permisos de Futuros activos.")
                except Exception as error:
                    bot.log(
                        "⚠️ PAPER_MODE: credenciales Binance no válidas o no operativas. "
                        f"Se continúa solo con endpoints públicos: {error}"
                    )
            else:
                bot.log("ℹ️ PAPER_MODE: sin API keys, usando solo endpoints públicos.")
            bot.is_hedge_mode = False
            bot.log(
                f"🧾 PAPER capital virtual inicializado en ${float(getattr(Config, 'PAPER_INITIAL_BALANCE', 1000.0)):.2f}"
            )
        else:
            # Verificación explícita de permisos
            try:
                try:
                    _sync_exchange_time(bot, exchange, "bootstrap REAL")
                except Exception as error:
                    bot.log(
                        f"⚠️ Binance time sync previo falló; se validará con request autenticada: {error}"
                    )

                _call_auth_read_with_time_resync(
                    bot, "fetch_balance(REAL)", bot.execution.fetch_balance
                )
                bot.log("✅ Conectado: API Keys válidas y permisos de Futuros activos.")

                try:
                    # Detectar si la cuenta está en Hedge Mode o One-Way
                    # FIX: Usar símbolo válido para evitar error de parámetro
                    if hasattr(bot.execution.exchange, "fetch_position_mode"):
                        try:
                            # Intentar primero con símbolo BTC
                            mode = _call_auth_read_with_time_resync(
                                bot,
                                "fetch_position_mode(BTC/USDT:USDT)",
                                lambda: bot.execution.fetch_position_mode(symbol="BTC/USDT:USDT"),
                            )
                            bot.is_hedge_mode = mode.get("hedged", False)
                        except Exception:
                            # Fallback: intentar sin símbolo
                            mode = _call_auth_read_with_time_resync(
                                bot,
                                "fetch_position_mode",
                                bot.execution.fetch_position_mode,
                            )
                            bot.is_hedge_mode = mode.get("hedged", False)
                    else:
                        # Fallback a endpoint directo
                        mode = _call_auth_read_with_time_resync(
                            bot,
                            "get_position_side_dual",
                            bot.execution.get_position_side_dual,
                        )
                        bot.is_hedge_mode = mode["dualSidePosition"]
                    bot.log(f"ℹ️ Modo de Posición: {'HEDGE' if bot.is_hedge_mode else 'ONE-WAY'}")
                except Exception as error:
                    bot.is_paused = True
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    raise RuntimeError(
                        f"No se pudo detectar modo Hedge/OneWay en REAL: {error}"
                    ) from error
            except Exception as error:
                bot.log(
                    f"❌ CONEXIÓN RECHAZADA: Error verificando permisos/balance. Revise sus API Keys. {error}"
                )
                raise RuntimeError(
                    f"Credenciales/permisos Binance inválidos o insuficientes: {error}"
                ) from error

        if not Config.PAPER_MODE:
            mode_tag = "TESTNET" if Config.USE_TESTNET else "REAL"
            bot.log(f"🔥 MODO {mode_tag}: sincronizando wallet...")
            bot.sync_wallet()
        mode_label = (
            "TESTNET"
            if Config.USE_TESTNET
            else ("📝 PAPER (Simulado)" if Config.PAPER_MODE else "🔥 REAL (Dinero Real)")
        )
        bot.log(f"🛡️ MODO OPERATIVO: {mode_label}")
    except Exception as error:
        bot.log(f"❌ ERROR FATAL: {error}")
        raise RuntimeError(f"No se pudo inicializar conexión Binance: {error}") from error
