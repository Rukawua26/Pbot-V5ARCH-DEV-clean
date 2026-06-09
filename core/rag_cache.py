import json

import numpy as np

DEFAULT_RAG_SCORES = {k: 50.0 for k in ["T", "V", "C", "L", "S"]}


def build_rag_vector(features: dict, btc_delta_key: str = "btc_delta") -> list[float]:
    ob_val = 0
    ob_status = features.get("ob_status", "NEUTRAL")
    if "BULL" in ob_status:
        ob_val = 1
    elif "BEAR" in ob_status:
        ob_val = -1

    return [
        features.get("rsi", 50),
        features.get("adx", 20),
        features.get("vol_rel", 1.0) * 10,
        features.get(btc_delta_key, 0.0) * 5,
        features.get("dist_ema", 0.0) * 100,
        features.get("z_score", 0.0),
        features.get("bb_pos", 0.5),
        ob_val,
    ]


def init_rag_cache(brain, max_trades: int) -> None:
    """Inicializa la caché RAG en RAM con un límite fijo de trades recientes."""
    brain.rag_cache_matrix = None
    brain.rag_cache_meta = []

    try:
        conn = brain._get_conn()
        c = conn.cursor()
        # Cargar solo los trades recientes necesarios para evitar crecimiento RAM infinito.
        c.execute(
            """
            SELECT symbol, pnl_percent, market_snapshot, timestamp
            FROM trades
            WHERE market_snapshot IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        """,
            (max_trades,),
        )
        rows = list(reversed(c.fetchall()))
        conn.close()

        if not rows:
            return

        matrix_data = []
        for row in rows:
            try:
                snap = json.loads(row["market_snapshot"])
                matrix_data.append(build_rag_vector(snap, btc_delta_key="btc_delta_tf"))
                brain.rag_cache_meta.append(
                    {
                        "symbol": row["symbol"],
                        "pnl": row["pnl_percent"],
                        "date": row["timestamp"],
                        "snap": snap,
                    }
                )
            except Exception:
                continue

        if matrix_data:
            brain.rag_cache_matrix = np.array(matrix_data)
            print(f"🧠 RAG Vector Cache inicializado: {len(brain.rag_cache_meta)} vectores en RAM.")
    except Exception as error:
        print(f"⚠️ Error cargando RAG Cache: {error}")


def update_rag_cache(brain, trade_data: dict, max_trades: int) -> None:
    """Añade un nuevo trade a la caché vectorial en tiempo real."""
    if getattr(brain, "rag_cache_matrix", None) is None:
        return

    try:
        snap = trade_data.get("market_snapshot", {})
        if not snap:
            return

        vec = build_rag_vector(snap, btc_delta_key="btc_delta_tf")
        brain.rag_cache_matrix = np.vstack([brain.rag_cache_matrix, vec])
        brain.rag_cache_meta.append(
            {
                "symbol": trade_data["symbol"],
                "pnl": trade_data["pnl_percent"],
                "date": trade_data.get("timestamp", ""),
                "snap": snap,
            }
        )
        if len(brain.rag_cache_meta) > max_trades:
            overflow = len(brain.rag_cache_meta) - max_trades
            brain.rag_cache_meta = brain.rag_cache_meta[overflow:]
            brain.rag_cache_matrix = brain.rag_cache_matrix[overflow:]
    except Exception as error:
        print(f"⚠️ Error actualizando RAG cache: {error}")


def get_rag_inference(brain, _symbol: str, current_features: dict, config) -> tuple[dict, list]:
    """
    [SISTEMA RAG VECTORIAL v118]
    Usa Similitud de Coseno optimizada con NumPy contra TODA la historia.
    Configurable via Config.RAG_ENABLED, RAG_SIMILARITY_THRESHOLD
    """
    try:
        if not getattr(config, "RAG_ENABLED", True):
            return dict(DEFAULT_RAG_SCORES), ["RAG_DISABLED"]

        if getattr(brain, "rag_cache_matrix", None) is None or len(brain.rag_cache_matrix) == 0:
            return dict(DEFAULT_RAG_SCORES), ["NO_CACHE"]

        curr_vec = np.array(build_rag_vector(current_features, btc_delta_key="btc_delta"))
        similarity_threshold = getattr(config, "RAG_SIMILARITY_THRESHOLD", 0.85)
        min_matches = getattr(config, "RAG_MIN_MATCHES", 3)

        dot_products = np.dot(brain.rag_cache_matrix, curr_vec)
        norms_matrix = np.linalg.norm(brain.rag_cache_matrix, axis=1)
        norm_curr = np.linalg.norm(curr_vec)

        denominator = norms_matrix * norm_curr
        denominator[denominator == 0] = 1e-10

        similarities = dot_products / denominator
        valid_indices = np.where(similarities > similarity_threshold)[0]

        if len(valid_indices) == 0:
            return dict(DEFAULT_RAG_SCORES), ["NO_MATCH"]

        if len(valid_indices) < min_matches:
            top_k = max(min_matches, len(valid_indices))
            top_k_idx = np.argsort(similarities)[-top_k:]
        else:
            valid_similarities = similarities[valid_indices]
            top_k_idx = valid_indices[np.argsort(valid_similarities)[-min_matches:][::-1]]

        top_matches = [brain.rag_cache_meta[idx] for idx in top_k_idx]
        wins = sum(1 for match in top_matches if match["pnl"] > 0)
        weighted_score = (wins / len(top_matches)) * 100
        final_scores = {k: weighted_score for k in ["T", "V", "C", "L", "S"]}
        evidence = [f"{match['symbol']} ({match['pnl']:+.1f}%)" for match in top_matches]

        return final_scores, evidence
    except Exception:
        return dict(DEFAULT_RAG_SCORES), []
