from tools.learning import Brain
from tools.reporter import generate_terminal_audit_table
from rich.console import Console
import sqlite3
import os


def get_real_stats():
    """Calcula estadísticas reales desde la base de datos"""
    # Buscar la DB en el directorio raíz (un nivel arriba de /tools)
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(root_dir, "sniper_brain.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Total de símbolos únicos con trades
    c.execute("SELECT COUNT(DISTINCT symbol) FROM trades")
    total_symbols = c.fetchone()[0]

    # Distribución real de efectividad (mínimo 3 trades por símbolo)
    c.execute("""
        SELECT 
            CASE 
                WHEN wr >= 75 AND wr < 81 THEN '75-80%'
                WHEN wr >= 81 AND wr < 91 THEN '81-90%'
                WHEN wr >= 91 AND wr <= 100 THEN '91-100%'
                ELSE 'otro'
            END as rango,
            COUNT(*) as conteo
        FROM (
            SELECT symbol, 
                   100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) as wr,
                   COUNT(*) as trades
            FROM trades 
            GROUP BY symbol 
            HAVING trades >= 3
        )
        WHERE wr >= 75
        GROUP BY rango
    """)
    efectividad = dict(c.fetchall())

    # Total de trades
    c.execute("SELECT COUNT(*) FROM trades")
    total_trades = c.fetchone()[0]

    # Win rate general
    c.execute(
        "SELECT 100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) FROM trades"
    )
    win_rate_general = c.fetchone()[0]

    conn.close()

    return {
        "total_symbols": total_symbols,
        "total_trades": total_trades,
        "win_rate_general": win_rate_general,
        "efectividad_75_80": efectividad.get("75-80%", 0),
        "efectividad_81_90": efectividad.get("81-90%", 0),
        "efectividad_91_100": efectividad.get("91-100%", 0),
    }


def run_audit():
    console = Console()
    try:
        brain = Brain()
        trades = brain.get_last_n_trades(100)

        if not trades:
            console.print(
                "[yellow]📭 No hay trades registrados en la base de datos.[/yellow]"
            )
            return

        # Calcular estadísticas reales
        stats = get_real_stats()

        # Mostrar inteligencia REAL (no hardcodeada)
        console.print("[bold cyan]🧠 ESTADO DE INTELIGENCIA REAL[/bold cyan]")
        console.print(f"🔹 Símbolos únicos analizados: {stats['total_symbols']}")
        console.print(f"🔹 Total de trades: {stats['total_trades']}")
        console.print(f"🔹 Win rate general: {stats['win_rate_general']:.1f}%")
        console.print("━━━━━━━━━━━━━━━━━━━━")
        console.print("[bold]Efectividad real (símbolos con 3+ trades):[/bold]")

        if stats["efectividad_75_80"] > 0:
            console.print(
                f"🔹 Efectividad 75-80%: [green]{stats['efectividad_75_80']} patrones[/]"
            )
        if stats["efectividad_81_90"] > 0:
            console.print(
                f"🔹 Efectividad 81-90%: [bold green]{stats['efectividad_81_90']} patrones[/]"
            )
        if stats["efectividad_91_100"] > 0:
            console.print(
                f"🔹 Efectividad 91-100%: [bold yellow]{stats['efectividad_91_100']} patrones[/]"
            )

        if (
            stats["efectividad_75_80"] == 0
            and stats["efectividad_81_90"] == 0
            and stats["efectividad_91_100"] == 0
        ):
            console.print(
                "[yellow]🔸 No hay suficientes datos para calcular efectividad (mínimo 3 trades por símbolo)[/yellow]"
            )

        console.print("━━━━━━━━━━━━━━━━━━━━")

        table = generate_terminal_audit_table(trades)
        console.print(table)
        console.print("\n")

    except Exception as e:
        console.print(f"[bold red]❌ Error ejecutando auditoría:[/bold red] {e}")


if __name__ == "__main__":
    run_audit()
