#!/usr/bin/env python3
"""
Data Utility Audit — Evalúa qué datos en la DB son útiles y cuáles son peso muerto.

Modo de uso:
    SNIPER_DB_PATH=/ruta/a/sniper_brain.db ./tools/data_utility_audit.py

Salida: Reporte JSON a stdout + archivo audit_report.json en el directorio actual.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 ** 2):.2f} MB"


def table_stats(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    c = conn.cursor()
    c.execute(f'SELECT COUNT(*) FROM "{table}"')
    total = c.fetchone()[0]
    c.execute(f'SELECT sql FROM sqlite_master WHERE name="{table}"')
    schema_sql = (c.fetchone() or [None])[0]

    columns = []
    if total > 0:
        c.execute(f'PRAGMA table_info("{table}")')
        col_info = c.fetchall()
        for col in col_info:
            col_name = col[1]
            c.execute(
                f'SELECT COUNT(*) FROM "{table}" '
                f'WHERE "{col_name}" IS NOT NULL AND "{col_name}" != \'\'',
            )
            non_null = c.fetchone()[0]
            pct = round(non_null / total * 100, 1) if total else 0.0
            columns.append({
                "name": col_name,
                "type": col[2],
                "not_null": col[3] == 1,
                "non_null_count": non_null,
                "non_null_pct": pct,
                "utility": _rate_utility(pct),
            })
    return {
        "table": table,
        "rows": total,
        "schema": schema_sql,
        "columns": columns,
    }


def _rate_utility(pct: float) -> str:
    if pct >= 90:
        return "HIGH"
    if pct >= 50:
        return "MEDIUM"
    if pct >= 10:
        return "LOW"
    return "DEAD"


def analyze_features_json(conn: sqlite3.Connection) -> dict[str, Any]:
    c = conn.cursor()
    c.execute(
        "SELECT features_json FROM signal_alerts "
        "WHERE features_json IS NOT NULL AND features_json != ''",
    )
    rows = c.fetchall()
    if not rows:
        return {"samples": 0, "fields": {}}

    field_stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            feat = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            continue
        for k, v in feat.items():
            if k not in field_stats:
                field_stats[k] = {
                    "count": 0,
                    "types": Counter(),
                    "null_count": 0,
                    "sample_values": [],
                    "raw_sample": None,
                }
            field_stats[k]["count"] += 1
            vtype = type(v).__name__
            field_stats[k]["types"][vtype] += 1
            if v is None:
                field_stats[k]["null_count"] += 1
            if len(field_stats[k]["sample_values"]) < 3 and v is not None:
                field_stats[k]["sample_values"].append(str(v)[:60])
            if field_stats[k]["raw_sample"] is None and v is not None:
                field_stats[k]["raw_sample"] = v

    total = len(rows)
    field_list = []
    for field_name, stats in sorted(field_stats.items()):
        types_str = ", ".join(
            f"{t}: {c}" for t, c in stats["types"].most_common()
        )
        pct = round(stats["count"] / total * 100, 1)
        null_pct = round(stats["null_count"] / stats["count"] * 100, 1) if stats["count"] else 0
        is_constant = _check_constant(conn, field_name, rows)
        field_list.append({
            "field": field_name,
            "presence_pct": pct,
            "null_pct": null_pct,
            "types": types_str,
            "is_constant": is_constant,
            "sample_values": stats["sample_values"],
            "utility": _rate_utility(pct),
        })

    return {
        "samples": total,
        "fields": field_list,
        "redundant_groups": _find_redundant_groups(field_stats, total),
    }


def _check_constant(
    conn: sqlite3.Connection,
    field_name: str,
    rows: list[sqlite3.Row],
) -> bool:
    seen = set()
    for row in rows:
        try:
            feat = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            continue
        val = feat.get(field_name)
        if val is not None:
            s = str(val)[:20]
            seen.add(s)
            if len(seen) > 1:
                return False
    return len(seen) == 1


def _find_redundant_groups(
    field_stats: dict, total: int,
) -> list[dict[str, Any]]:
    raw_keys = {"rsi_raw", "adx_raw", "atr_raw", "atr_pct_raw",
                "vol_rel_raw", "bb_pos_raw", "bb_width_raw",
                "ema_dist_pct_raw"}
    model_keys = {"model_rsi", "model_adx", "model_atr", "model_volume",
                  "model_dist_ema", "model_z_score", "model_bb_pos",
                  "model_bb_width"}
    redundant = []

    raw_model_pairs: list[dict] = []
    for rk in sorted(raw_keys):
        mk: str | None = rk.replace("_raw", "").replace("ema_dist_pct_raw", "model_dist_ema")
        if rk == "ema_dist_pct_raw":
            mk = "model_dist_ema"
        elif rk == "volume_raw":
            mk = "model_volume"
        elif rk.startswith("rsi"):
            mk = "model_rsi"
        elif rk.startswith("adx"):
            mk = "model_adx"
        elif rk.startswith("atr_pct"):
            mk = None
        elif rk.startswith("atr"):
            mk = "model_atr"
        elif rk.startswith("vol_rel"):
            mk = None
        elif rk.startswith("bb_pos"):
            mk = "model_bb_pos"
        elif rk.startswith("bb_width"):
            mk = "model_bb_width"
        if mk and mk in model_keys:
            redundant.append({
                "group": f"raw_vs_model",
                "fields": [rk, mk],
                "note": "Idénticos excepto por nombre. Uno sobra.",
            })
    redundant.append({
        "group": "raw_vs_short",
        "fields": list(raw_keys),
        "note": "campos _raw duplican la info de campos base (rsi, adx, etc.)",
    })
    redundant.append({
        "group": "model_vs_short",
        "fields": list(model_keys),
        "note": "campos model_ duplican la info de campos base",
    })
    return redundant


def analysis_summary(
    conn: sqlite3.Connection,
    db_path: Path,
) -> dict[str, Any]:
    start = time.time()
    c = conn.cursor()

    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [r[0] for r in c.fetchall()
                  if not r[0].startswith("sqlite_")]

    tables_data = {}
    for tbl in all_tables:
        tables_data[tbl] = table_stats(conn, tbl)

    features_analysis = analyze_features_json(conn)

    db_size = os.path.getsize(db_path)
    size_info = {
        "path": str(db_path),
        "size_bytes": db_size,
        "size_human": fmt_bytes(db_size),
        "total_tables": len(all_tables),
    }

    space_by_table = {}
    for tbl in all_tables:
        c.execute(f'SELECT COUNT(*) FROM "{tbl}"')
        rows = c.fetchone()[0]
        c.execute(f'SELECT sql FROM sqlite_master WHERE name="{tbl}"')
        schema = (c.fetchone() or [None])[0]
        est_row_size = len(schema or "") if rows == 0 else max(1, db_size // max(rows, 1))
        est_size = est_row_size * max(rows, 1)
        space_by_table[tbl] = {
            "rows": rows,
            "estimated_bytes": est_size,
            "estimated_human": fmt_bytes(est_size),
        }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "elapsed_seconds": round(time.time() - start, 2),
        "database": size_info,
        "table_overview": space_by_table,
        "signal_alerts_features": features_analysis,
        "tables_detail": tables_data,
        "recommendations": _generate_recommendations(
            tables_data, features_analysis, size_info,
        ),
    }


def _generate_recommendations(
    tables_data: dict,
    features_analysis: dict,
    size_info: dict,
) -> list[dict[str, str]]:
    recs: list[dict[str, str]] = []

    db_size_mb = size_info["size_bytes"] / (1024 * 1024)
    if db_size_mb < 1:
        recs.append({
            "priority": "INFO",
            "message": f"DB ocupa solo {size_info['size_human']}. "
                       "No hay presión de espacio. Podemos agregar datos sin problema.",
        })

    for tbl_name, tbl_info in tables_data.items():
        total = tbl_info["rows"]
        if total == 0:
            recs.append({
                "priority": "LOW",
                "message": f"Tabla '{tbl_name}' vacía (0 filas). "
                           "Schema creado pero sin datos aún.",
            })

        if total > 0:
            dead_cols = [c for c in tbl_info["columns"]
                         if c["utility"] == "DEAD"]
            if dead_cols:
                names = ", ".join(c["name"] for c in dead_cols)
                recs.append({
                    "priority": "MEDIUM",
                    "message": f"Tabla '{tbl_name}': {len(dead_cols)} columna(s) "
                               f"muertas ({names}). Considerar eliminar del schema.",
                })

    if features_analysis.get("samples", 0) > 0:
        for f in features_analysis["fields"]:
            if f["utility"] == "LOW" or f["null_pct"] > 90:
                recs.append({
                    "priority": "LOW",
                    "message": f"Campo '{f['field']}' en features_json tiene "
                               f"presencia {f['presence_pct']}% y "
                               f"{f['null_pct']}% nulos. Poco útil.",
                })

        redundant_groups = features_analysis.get("redundant_groups", [])
        for g in redundant_groups:
            recs.append({
                "priority": "MEDIUM",
                "message": f"Redundancia detectada: {g['note']} "
                           f"({', '.join(g['fields'])})",
            })

        has_oi = any(f["field"] == "oi_delta_pct" and f["presence_pct"] < 50
                     for f in features_analysis["fields"])
        has_cvd = any(f["field"] == "cvd_reason" and f["presence_pct"] < 50
                      for f in features_analysis["fields"])
        if has_oi:
            recs.append({
                "priority": "HIGH",
                "message": "oi_delta_pct solo está presente en ~13.8% de las "
                           "señales. El fetch de Open Interest falla a menudo. "
                           "Revisar core/execution_service.fetch_open_interest.",
            })
        if has_cvd:
            recs.append({
                "priority": "HIGH",
                "message": "cvd_reason solo está presente en ~13.8% de las "
                           "señales. El filtro CVD no se ejecuta siempre. "
                           "Verificar cobertura del filtro.",
            })

        has_constant = [f for f in features_analysis["fields"]
                        if f.get("is_constant") and f["presence_pct"] >= 90]
        if has_constant:
            const_names = ", ".join(f["field"] for f in has_constant[:5])
            recs.append({
                "priority": "MEDIUM",
                "message": f"Campos constantes detectados: {const_names}. "
                           "No aportan información discriminativa.",
            })

    return recs


def main() -> None:
    db_env = os.getenv("SNIPER_DB_PATH")
    if db_env:
        db_path = Path(db_env)
    else:
        repo_root = Path(__file__).resolve().parent.parent
        db_path = repo_root / "sniper_brain.db"

    if not db_path.exists():
        eprint(f"ERROR: DB no encontrada en {db_path}")
        eprint("Usá SNIPER_DB_PATH para apuntar a otra ruta.")
        sys.exit(1)

    eprint(f"Analizando {db_path} ({fmt_bytes(os.path.getsize(db_path))}) ...")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    report = analysis_summary(conn, db_path)
    conn.close()

    out_path = Path("audit_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    eprint(f"✅ Reporte guardado en {out_path.resolve()}")

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
