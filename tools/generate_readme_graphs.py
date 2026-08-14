#!/usr/bin/env python3
"""Genera los 6 graficos oscuros de rendimiento para el README.

Salida: docs/README/graph_*.png

Cada grafico esta enfocado en una metrica clave del bot:
  1. graph_equity_curve.png       - Curva de balance con area rellena
  2. graph_pnl_distribution.png   - Barras PnL% por trade
  3. graph_consensus_probability.png - Linea prob_final por ronda de consenso
  4. graph_blocked_reasons.png    - Barras horizontales de razones de veto
  5. graph_daily_pnl_calendar.png - Heatmap PnL diario (matriz 7x4)
  6. graph_winrate_by_symbol.png  - Barras agrupadas win/loss por simbolo

Los datos son los mismos que expone el dashboard mock; replicarlos aqui evita
acoplar la generacion al servidor mock y mantiene el script autocontenido.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

matplotlib.use("Agg")

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "README"
)
os.makedirs(OUT_DIR, exist_ok=True)

# --- Tema oscuro ---
BG = "#0a0e1a"
PANEL = "#0f172a"
GRID = "#1e293b"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
GREEN = "#39ff14"
RED = "#ff0055"
BLUE = "#00f2ff"
AMBER = "#ffb300"
PURPLE = "#a855f7"

plt.rcParams.update(
    {
        "figure.facecolor": BG,
        "axes.facecolor": PANEL,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT,
        "axes.titlecolor": TEXT,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": GRID,
        "grid.linestyle": "-",
        "grid.linewidth": 0.6,
        "font.family": "monospace",
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.facecolor": PANEL,
        "legend.edgecolor": GRID,
        "legend.labelcolor": TEXT,
    }
)

W, H = 13.6, 7.2  # inches (16:9, dpi=100 -> 1360x720)
DPI = 100


# --- Datos (replican los endpoints del dashboard) ---
def equity_series():
    pts = []
    bal = 2380.0
    for i in range(60):
        bal += (i % 7 - 3) * 1.8 + 0.5
        pts.append(round(bal, 2))
    return pts


def trades_data():
    syms = [
        "ETHUSDT",
        "BTCUSDT",
        "SOLUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "DOGEUSDT",
        "XRPUSDT",
        "ADAUSDT",
    ]
    rows = []
    for i, s in enumerate(syms):
        pnl = (i * 3.7) - 5 + (0.5 if i % 3 else -0.8)
        rows.append(
            {
                "symbol": s,
                "pnl": round(pnl, 2),
                "pnl_percent": round(pnl / 2.4, 2),
                "side": "BUY" if i % 2 else "SELL",
                "is_shadow": bool(i % 2),
            }
        )
    return rows


def consensus_rounds():
    base_ts = 1723616380
    rounds = [
        ("ETHUSDT", "BUY", 78.4, "EXECUTED"),
        ("BTCUSDT", "SELL", 72.1, "EXECUTED"),
        ("SOLUSDT", "BUY", 74.6, "BLOCKED"),
        ("AVAXUSDT", "BUY", 69.8, "BLOCKED"),
        ("LINKUSDT", "BUY", 76.3, "EXECUTED"),
        ("DOGEUSDT", "SELL", 71.4, "OBSERVED"),
        ("ETHUSDT", "BUY", 80.2, "EXECUTED"),
        ("XRPUSDT", "BUY", 68.5, "BLOCKED"),
    ]
    out = []
    for i, (sym, side, prob, status) in enumerate(rounds):
        out.append(
            {
                "ts": base_ts - i * 400,
                "symbol": sym,
                "side": side,
                "prob": prob,
                "status": status,
            }
        )
    return out


def blocked_reasons():
    return [
        ("RISK_REWARD_VETO", 18),
        ("MARKOV_RANGE_VETO", 12),
        ("MIN_ATR_PCT_VETO", 9),
        ("OI_DELTA_VETO", 6),
        ("MTF_VETO", 5),
        ("BULL_TREND_VETO", 4),
        ("FEAR_GREED_VETO", 3),
    ]


def calendar_pnl():
    base = datetime(2026, 7, 1)
    days = []
    for i in range(28):
        d = base + timedelta(days=i)
        wins = 4 + (i % 5)
        losses = 4 + ((i + 2) % 4)
        pnl = round(wins * 2.4 - losses * 1.1, 2)
        days.append({"date": d, "pnl": pnl})
    return days


def winrate_by_symbol():
    syms = [
        "ETHUSDT",
        "BTCUSDT",
        "SOLUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "DOGEUSDT",
        "XRPUSDT",
        "ADAUSDT",
    ]
    wins = [6, 5, 4, 3, 5, 2, 4, 3]
    losses = [2, 3, 3, 4, 1, 5, 3, 4]
    return syms, wins, losses


# --- Helpers de estilo ---
def style_axes(ax, title, xlabel=None, ylabel=None):
    ax.set_title(title, pad=14, loc="left")
    ax.grid(True, alpha=0.5)
    if xlabel:
        ax.set_xlabel(xlabel, labelpad=8)
    if ylabel:
        ax.set_ylabel(ylabel, labelpad=8)
    ax.tick_params(length=0)


def annotate_pct(ax, x, y, text, color=TEXT, size=10, weight="bold"):
    ax.text(
        x,
        y,
        text,
        color=color,
        fontsize=size,
        fontweight=weight,
        ha="center",
        va="center",
        fontfamily="monospace",
    )


# --- 1. Curva de equity ---
def chart_equity():
    series = equity_series()
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    x = np.arange(len(series))
    y = np.array(series)

    # Area rellena con gradiente
    ax.fill_between(x, y, y.min(), color=BLUE, alpha=0.18, linewidth=0)
    ax.plot(x, y, color=BLUE, linewidth=2.2, solid_capstyle="round")

    # Marcadores de inicio/fin
    ax.scatter(
        [0],
        [y[0]],
        color=AMBER,
        s=90,
        zorder=5,
        edgecolor=BG,
        linewidth=2,
        label=f"Inicio ${y[0]:.2f}",
    )
    ax.scatter(
        [len(y) - 1],
        [y[-1]],
        color=GREEN,
        s=90,
        zorder=5,
        edgecolor=BG,
        linewidth=2,
        label=f"Fin ${y[-1]:.2f}",
    )

    # Linea horizontal del balance inicial
    ax.axhline(y[0], color=MUTED, linestyle="--", linewidth=0.8, alpha=0.6)

    # Delta %
    delta_pct = (y[-1] - y[0]) / y[0] * 100
    color_delta = GREEN if delta_pct >= 0 else RED
    ax.text(
        0.99,
        0.05,
        f"Δ {delta_pct:+.2f}%",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        color=color_delta,
        fontsize=18,
        fontweight="bold",
    )

    style_axes(ax, "Curva de equity", "Ciclo de trading", "Balance (USDT)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("$%.0f"))
    ax.xaxis.set_major_locator(mticker.MaxNLocator(10))
    ax.legend(loc="upper left", framealpha=0.9)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_equity_curve.png"))
    plt.close(fig)


# --- 2. Distribucion de PnL por trade ---
def chart_pnl_distribution():
    rows = trades_data()
    symbols = [r["symbol"] for r in rows]
    pnls = [r["pnl_percent"] for r in rows]
    colors = [GREEN if p >= 0 else RED for p in pnls]

    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    bars = ax.bar(range(len(symbols)), pnls, color=colors, edgecolor=BG, linewidth=1.5)

    # Etiquetas de valor sobre cada barra
    for i, (bar, p) in enumerate(zip(bars, pnls)):
        h = bar.get_height()
        va = "bottom" if h >= 0 else "top"
        offset = 0.15 if h >= 0 else -0.15
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            h + offset,
            f"{p:+.2f}%",
            ha="center",
            va=va,
            color=GREEN if h >= 0 else RED,
            fontsize=10,
            fontweight="bold",
        )

    ax.axhline(0, color=MUTED, linewidth=1)
    ax.set_xticks(range(len(symbols)))
    ax.set_xticklabels(symbols, rotation=0)
    style_axes(ax, "Distribución de PnL % por trade", "Símbolo", "PnL (%)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%+.1f%%"))

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_pnl_distribution.png"))
    plt.close(fig)


# --- 3. Probabilidad de consenso ---
def chart_consensus():
    rounds = consensus_rounds()
    rounds_rev = list(reversed(rounds))
    probs = [r["prob"] for r in rounds_rev]
    x = np.arange(len(probs))

    status_color = {"EXECUTED": GREEN, "BLOCKED": RED, "OBSERVED": AMBER}
    colors = [status_color[r["status"]] for r in rounds_rev]

    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    ax.plot(x, probs, color=BLUE, linewidth=2.2, alpha=0.9, zorder=2)
    ax.fill_between(x, probs, 0, color=BLUE, alpha=0.10)

    for xi, (prob, color) in enumerate(zip(probs, colors)):
        ax.scatter([xi], [prob], color=color, s=110, zorder=5, edgecolor=BG, linewidth=1.8)

    # Umbrales de decision
    ax.axhline(75, color=GREEN, linestyle="--", linewidth=0.9, alpha=0.6)
    ax.text(len(x) - 0.5, 75 + 1, "SHADOW ≥ 75%", color=GREEN, fontsize=9, ha="right", va="bottom")
    ax.axhline(65, color=AMBER, linestyle="--", linewidth=0.9, alpha=0.6)
    ax.text(
        len(x) - 0.5,
        65 + 1,
        "≥ 65% umbral observación",
        color=AMBER,
        fontsize=9,
        ha="right",
        va="bottom",
    )

    # Leyenda de estados
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor=GREEN, label="EXECUTED"),
        Patch(facecolor=RED, label="BLOCKED"),
        Patch(facecolor=AMBER, label="OBSERVED"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", framealpha=0.9)

    style_axes(
        ax, "Probabilidad final por ronda de consenso", "Ronda (cronológica)", "prob_final (%)"
    )
    ax.set_ylim(50, 100)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_consensus_probability.png"))
    plt.close(fig)


# --- 4. Razones de bloqueo ---
def chart_blocked():
    reasons = blocked_reasons()
    labels = [r[0] for r in reasons]
    counts = [r[1] for r in reasons]
    y = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    bars = ax.barh(y, counts, color=RED, alpha=0.85, edgecolor=BG, linewidth=1.2)
    # Resaltar el mas frecuente
    bars[0].set_color(PURPLE)

    for i, (bar, c) in enumerate(zip(bars, counts)):
        ax.text(
            c + 0.4,
            bar.get_y() + bar.get_height() / 2,
            f"{c}",
            va="center",
            ha="left",
            color=TEXT,
            fontsize=11,
            fontweight="bold",
        )

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    style_axes(ax, "Frecuencia de razones de veto (ventana reciente)", "Conteo", "Razón")
    ax.set_xlim(0, max(counts) * 1.18)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_blocked_reasons.png"))
    plt.close(fig)


# --- 5. Heatmap PnL diario ---
def chart_calendar():
    days = calendar_pnl()
    # Layout: 4 semanas x 7 dias
    n_days = len(days)
    n_weeks = (n_days + 6) // 7

    grid = np.full((n_weeks, 7), np.nan)
    for i, d in enumerate(days):
        week = i // 7
        dow = i % 7
        grid[week, dow] = d["pnl"]

    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("pnl", [RED, "#1a1a2e", GREEN])
    vmax = max(abs(np.nanmin(grid)), abs(np.nanmax(grid)))
    im = ax.imshow(grid, cmap=cmap, vmin=-vmax, vmax=vmax, aspect="auto")

    # Etiquetas
    dow_labels = ["L", "M", "X", "J", "V", "S", "D"]
    ax.set_xticks(range(7))
    ax.set_xticklabels(dow_labels)
    ax.set_yticks(range(n_weeks))
    ax.set_yticklabels([f"Sem {i + 1}" for i in range(n_weeks)])

    # Valores sobre cada celda
    for i in range(n_weeks):
        for j in range(7):
            v = grid[i, j]
            if np.isnan(v):
                ax.text(j, i, "—", ha="center", va="center", color=MUTED, fontsize=10)
                continue
            txt_color = TEXT
            ax.text(
                j,
                i,
                f"{v:+.1f}",
                ha="center",
                va="center",
                color=txt_color,
                fontsize=9,
                fontweight="bold",
            )

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cbar.ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("$%+.0f"))
    cbar.ax.tick_params(colors=MUTED)

    style_axes(ax, "PnL diario (heatmap semanal)", "Día", "Semana")
    ax.tick_params(top=False, bottom=True, labeltop=False, labelbottom=True)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_daily_pnl_calendar.png"))
    plt.close(fig)


# --- 6. Winrate por simbolo ---
def chart_winrate():
    symbols, wins, losses = winrate_by_symbol()
    x = np.arange(len(symbols))
    w = 0.38

    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    ax.bar(x - w / 2, wins, w, color=GREEN, label="Ganados", edgecolor=BG, linewidth=1.2)
    ax.bar(x + w / 2, losses, w, color=RED, label="Perdidos", edgecolor=BG, linewidth=1.2)

    # WR% encima
    for i, (w_n, l_n) in enumerate(zip(wins, losses)):
        total = w_n + l_n
        wr = w_n / total * 100 if total > 0 else 0
        ax.text(
            i,
            max(w_n, l_n) + 0.3,
            f"{wr:.0f}%",
            ha="center",
            va="bottom",
            color=GREEN if wr >= 50 else RED,
            fontsize=9,
            fontweight="bold",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(symbols, rotation=0)
    style_axes(ax, "Ganados / Perdidos por símbolo", "Símbolo", "Conteo de trades")
    ax.legend(loc="upper right", framealpha=0.9)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "graph_winrate_by_symbol.png"))
    plt.close(fig)


def main():
    chart_equity()
    chart_pnl_distribution()
    chart_consensus()
    chart_blocked()
    chart_calendar()
    chart_winrate()
    print("Generados 6 graficos en", OUT_DIR)


if __name__ == "__main__":
    main()
