import time


def terminal_command_listener(bot):
    """Escucha comandos directamente desde la terminal."""
    while bot.is_running:
        try:
            # Usar input() en un hilo separado puede ser ruidoso con Rich,
            # pero es lo que el usuario pidió.
            cmd = input().strip().lower()
            if cmd == "audit":
                bot.log("📋 Cargando reporte de auditoría en terminal...")
                with bot.db_lock:
                    trades = bot.brain.get_last_n_trades(100)
                from tools.reporter import generate_terminal_audit_table

                table = generate_terminal_audit_table(trades)

                # Usar la consola de Rich para imprimir la tabla limpiamente
                from rich.console import Console

                console = Console()
                console.print("\n")
                console.print(table)
                console.print("\n[dim]Presione ENTER para continuar...[/]")
            elif cmd == "help":
                print("\nComandos de consola: audit, help, exit\n")
            elif cmd == "exit":
                bot.is_running = False
                break
        except EOFError:
            break
        except Exception:
            time.sleep(1)


def prioritize_targets(bot):
    """[ESCANEO DINÁMICO] Reordena la lista de escaneo por volatilidad (Change 24h)."""
    try:
        # [MEJORA] Ejecutar cada 2 minutos (120s) para ser más ágil detectando movimientos
        if time.time() - getattr(bot, "_last_sort_time", 0) < 120:
            return

        bot.log("🌪️ ESCANEO DINÁMICO: Reordenando pares por volatilidad...")
        tickers = bot.execution.fetch_tickers(bot.pairs_to_scan)

        def get_vol(symbol):
            # Búsqueda robusta del ticker
            ticker = (
                tickers.get(symbol)
                or tickers.get(symbol.replace("/", ""))
                or tickers.get(symbol.split(":")[0])
            )
            return abs(float(ticker.get("percentage", 0) or 0)) if ticker else 0.0

        bot.pairs_to_scan.sort(key=get_vol, reverse=True)
        bot._last_sort_time = time.time()

        # Log de confirmación
        top_3 = [f"{symbol} ({get_vol(symbol):.1f}%)" for symbol in bot.pairs_to_scan[:3]]
        bot.log(f"🔥 Top Volatilidad: {', '.join(top_3)}")
    except Exception as error:
        bot.log(f"⚠️ Error en Escaneo Dinámico: {error}")
