#!/usr/bin/env python3
"""
audit_correl.py — SNIPER AI v118
=================================
Matriz de Correlación de Super-Agentes MT ↔ G.

Lee los últimos 100 registros de votos de agentes desde la tabla
`shadow_telemetry` de la DB, extrae los votos de MT y G, y calcula
el coeficiente de correlación de Pearson.

Criterio de calidad:
  - Correlación < 0.60 → ✅ Los agentes son suficientemente independientes.
  - Correlación ≥ 0.60 → ⚠️  Los agentes están sobre-solapados; revisar features.

Uso:
    python tools/audit_correl.py
    python tools/audit_correl.py --limit 200   (últimos N votos)
    python tools/audit_correl.py --threshold_warning 0.60 --threshold_critical 0.75

Operational Guide:
    - Frequency: Daily or before every ML model promotion.
    - Min Samples: 10 paired votes required for statistical relevance.
    - Warning (0.60): Review feature overlap between MT and G agents.
    - Critical (0.75): Diversify G agent features or reduce its weight in consensus.
    - Automation: Can be integrated into CI/CD or runtime health checks.
"""

import sys
import os
import json
import argparse
import sqlite3

# --- UMBRALES OPERATIVOS v119 ---
CORREL_WARN = 0.60
CORREL_CRIT = 0.75
MIN_SAMPLES = 10

# Añadir raíz del proyecto al path para importar _DB_PATH
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_TOOLS_DIR)
sys.path.insert(0, _ROOT_DIR)

# Importar la ruta canónica de la DB desde learning.py
try:
    from tools.learning import _DB_PATH
except ImportError:
    _DB_PATH = os.path.join(_ROOT_DIR, "sniper_brain.db")

# ─────────────────────────────────────────────
# Intento de usar numpy/scipy para correlación
# ─────────────────────────────────────────────
try:
    import numpy as np

    def pearson(x, y):
        if len(x) < 2 or len(y) < 2:
            return 0.0
        if np.std(x) == 0 or np.std(y) == 0:
            return 0.0
        corr = float(np.corrcoef(x, y)[0, 1])
        if np.isnan(corr):
            return 0.0
        return corr
except ImportError:
    import math

    def pearson(x, y):
        """Correlación de Pearson en Python puro."""
        n = len(x)
        if n < 2:
            return 0.0
        mx, my = sum(x) / n, sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        if dx * dy == 0:
            return 0.0
        return num / (dx * dy)


def fetch_votes(db_path: str, limit: int) -> tuple[list, list]:
    """
    Extrae votos de MT y G desde shadow_telemetry.
    Espera registros con event_type='AGENT_VOTE' y
    data={agent: 'MT'|'G', vote: float}.

    Para mayor robustez, también busca en event_type='CONSENSUS_VOTE'
    si la estructura del data incluye 'mt_vote' / 'g_vote'.
    """
    if not os.path.exists(db_path):
        print(f"❌ DB no encontrada en: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Verificar que la tabla existe
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_telemetry'"
    )
    if not c.fetchone():
        conn.close()
        print(
            "⚠️  Tabla shadow_telemetry no existe aún. El bot necesita al menos un ciclo de flush."
        )
        sys.exit(0)

    # Estrategia 1: votos individuales por agente
    c.execute(
        """
        SELECT data FROM shadow_telemetry
        WHERE event_type IN ('AGENT_VOTE', 'AGENT_CONSENSUS')
        ORDER BY id DESC LIMIT ?
        """,
        (limit * 10,),  # traer más para filtrar MT y G
    )
    rows = c.fetchall()
    conn.close()

    mt_votes, g_votes = [], []
    paired = {}  # timestamp -> {MT: v, G: v}

    for row in rows:
        try:
            d = json.loads(row["data"])
            agent = d.get("agent", "").upper()
            vote = d.get("vote")
            ts = d.get("ts", d.get("timestamp", ""))

            if agent in ("MT", "G") and vote is not None:
                paired.setdefault(ts, {})[agent] = float(vote)
        except Exception:
            continue

    # También intentar extraer desde CONSENSUS_VOTE y AGENT_VOTES
    conn2 = sqlite3.connect(db_path, timeout=10.0)
    conn2.row_factory = sqlite3.Row
    c2 = conn2.cursor()
    c2.execute(
        """
        SELECT event_type, data FROM shadow_telemetry
        WHERE event_type IN ('CONSENSUS_VOTE', 'AGENT_VOTES')
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    )
    rows2 = c2.fetchall()
    conn2.close()

    for row in rows2:
        try:
            d = json.loads(row["data"])
            event_type = row["event_type"]

            if event_type == "AGENT_VOTES":
                votes = d.get("votes", {}) if isinstance(d, dict) else {}
                mt_v = votes.get("MT")
                g_v = votes.get("G")
            else:
                mt_v = d.get("mt_vote") or d.get("MT")
                g_v = d.get("g_vote") or d.get("G")

            if mt_v is not None and g_v is not None:
                mt_votes.append(float(mt_v))
                g_votes.append(float(g_v))
        except Exception:
            continue

    # Combinar pares completos del paired dict
    for ts, votes in paired.items():
        if "MT" in votes and "G" in votes:
            mt_votes.append(votes["MT"])
            g_votes.append(votes["G"])

    # Truncar al límite solicitado
    mt_votes = mt_votes[:limit]
    g_votes = g_votes[:limit]

    return mt_votes, g_votes


def main():
    parser = argparse.ArgumentParser(description="Auditoria de correlacion MT <-> G")
    parser.add_argument(
        "--limit", type=int, default=100, help="Número de votos a analizar"
    )
    parser.add_argument(
        "--fail-on-critical", action="store_true", help="Falla con código 1 si es crítico"
    )
    parser.add_argument(
        "--threshold_warning", type=float, default=CORREL_WARN, help="Umbral Warning"
    )
    parser.add_argument(
        "--threshold_critical", type=float, default=CORREL_CRIT, help="Umbral Crítico"
    )
    parser.add_argument(
        "--db", type=str, default=_DB_PATH, help="Ruta a sniper_brain.db"
    )
    args = parser.parse_args()

    print(f"\n{'=' * 55}")
    print("  SNIPER AI — Auditoría de Correlación v118")
    print(f"{'=' * 55}")
    print(f"  DB       : {args.db}")
    print(f"  Muestra  : últimos {args.limit} votos")
    print(f"  Umbrales : WARN={args.threshold_warning} | CRIT={args.threshold_critical}")
    print(f"{'=' * 55}\n")

    mt_votes, g_votes = fetch_votes(args.db, args.limit)
    n = min(len(mt_votes), len(g_votes))

    if n < MIN_SAMPLES:
        print(f"⚠️  Solo se encontraron {n} pares MT/G en shadow_telemetry.")
        print("    El bot necesita más ciclos de operación para generar estadísticas.")
        print("    Asegúrate de que el orquestador registre los votos con:")
        print(
            '    shadow_logger.log({"type": "AGENT_VOTE", "data": {"agent": "MT", "vote": score, "ts": ts}})'
        )
        sys.exit(0)

    mt_s = mt_votes[:n]
    g_s = g_votes[:n]

    corr = pearson(mt_s, g_s)

    print(f"  Pares analizados : {n}")
    print(f"  Voto MT  (media) : {sum(mt_s) / n:.2f}")
    print(f"  Voto G   (media) : {sum(g_s) / n:.2f}")
    print(f"  Correlacion Pearson MT<->G : {corr:.4f}")
    print()

    is_critical = False
    if corr < args.threshold_warning:
        print(f"  ✅ CORRELACIÓN ACEPTABLE ({corr:.4f} < {args.threshold_warning})")
        print("     Los agentes MT y G son suficientemente independientes.")
        print("     El consenso es robusto — no hay colapso de diversidad.")
    elif corr < args.threshold_critical:
        print(f"  ⚠️  ALERTA: CORRELACIÓN ELEVADA ({corr:.4f} ≥ {args.threshold_warning})")
        print("     MT y G estan sobre-solapados. Revisar features compartidos.")
    else:
        print(f"  ❌ CRÍTICO: DIVERSIDAD COLAPSADA ({corr:.4f} ≥ {args.threshold_critical})")
        print("     Agentes redundantes. El consenso TRINITY pierde validez.")
        print("     Accion: Diversificar features de G o degradar peso de consenso.")
        is_critical = True

    print("  Accion recomendada: revisar ghost_agent.py y mt_agent.py.")

    print(f"\n{'=' * 55}\n")

    if is_critical and args.fail_on_critical:
        sys.exit(1)


if __name__ == "__main__":
    main()
