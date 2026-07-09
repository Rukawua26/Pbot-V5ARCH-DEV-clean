import time

from config import Config
from core.cooldown_state import cleanup_expired_cooldowns
from core.time_utils import parse_datetime_utc, utc_now


def _is_not_expired_until(value, now_utc):
    try:
        return parse_datetime_utc(value) > now_utc
    except Exception:
        return False


def get_effective_triage_count(bot):
    target_count = max(1, int(getattr(Config, "TOP_TRIAGE_COUNT", 30) or 30))
    try:
        if getattr(bot, "market_regime", "UNKNOWN") == "BEAR_TREND":
            bear_max = max(
                1, int(getattr(Config, "BEAR_TREND_MAX_PAIRS", target_count) or target_count)
            )
            return min(target_count, bear_max)
    except Exception:
        bot.log("⚠️ BEAR_TREND target reduction omitido, usando TOP_TRIAGE_COUNT")
    return target_count


def get_candidate_pool_limit(bot):
    target_count = get_effective_triage_count(bot)
    multiplier = max(1, int(getattr(Config, "TRIAGE_CANDIDATE_POOL_MULTIPLIER", 2) or 2))
    max_pool = max(target_count, int(getattr(Config, "TRIAGE_MAX_CANDIDATE_POOL", 60) or 60))

    if hasattr(bot, "weight_tracker") and bot.weight_tracker:
        try:
            if bot.weight_tracker.should_block("market"):
                return target_count
        except Exception as error:
            bot.log(f"⚠️ API Weight check omitido para pool de triaje: {error}")

    return min(target_count * multiplier, max_pool)


def _snapshot_tickers(snapshot):
    return {item["symbol"]: item.get("ticker", {}) for item in snapshot}


def _compact_ticker(ticker, vol_24h=None, last=None):
    """Keep only fields consumed downstream; CCXT raw tickers retain large info blobs."""
    compact = {
        "last": float(last if last is not None else ticker.get("last", 0) or 0),
        "quoteVolume": float(vol_24h if vol_24h is not None else ticker.get("quoteVolume", 0) or 0),
    }
    for key in ("ask", "bid", "markPrice", "percentage"):
        value = ticker.get(key)
        if value is not None:
            compact[key] = value
    return compact


def apply_hard_operability_filters(bot, snapshot):
    load_controls = getattr(
        bot,
        "_load_runtime_symbol_controls",
        lambda: {"blocked": set(), "preferred": set()},
    )
    controls = load_controls()
    blocked = controls.get("blocked", set())

    clean_blacklist = set()
    if hasattr(bot.brain, "get_symbol_blacklist"):
        symbol_blacklist = bot.brain.get_symbol_blacklist()
        clean_blacklist = {s.split("/")[0] for s in symbol_blacklist}
        if clean_blacklist:
            bot.log(f"   - 🚫 Símbolos vetados: {sorted(clean_blacklist)}")

    filtered = []
    for item in snapshot:
        symbol = item["symbol"]
        base = symbol.split("/")[0]
        ticker = item.get("ticker", {})

        if base in blocked:
            continue

        if base in clean_blacklist:
            continue

        if bot.restricted_sectors:
            sector = next(
                (k for k, v in Config.SECTORS.items() if any(s.lower() in base.lower() for s in v)),
                "OTHE",
            )
            if sector in bot.restricted_sectors:
                continue

        try:
            if not bot.data_service.audit_symbol_maturity(symbol):
                continue
            is_safe, ar_reason = bot.risk_engine.check_anti_revenge_blacklist(symbol)
        except Exception as error:
            bot.log(f"⚠️ Error en filtros duros para {symbol}: {error}")
            continue

        if not is_safe:
            bot.log(f"🚫 [v118] ANTI-REVENGE: {symbol} bloqueado temporalmente: {ar_reason}")
            continue

        try:
            quote_volume = float(ticker.get("quoteVolume", item.get("vol_24h", 0)) or 0)
            percentage = float(ticker.get("percentage", 0) or 0)
            if abs(percentage) >= 40.0:
                continue
            if abs(percentage) > 15.0 and quote_volume < Config.MIN_VOLUME_24H * 2:
                bot.log(f"⚠️ Anti-Pump: {symbol} descartado (Volátil/Bajo Liq).")
                continue
        except Exception:
            filtered.append(item)
            continue

        filtered.append(item)

    return filtered


def apply_tactical_priority(bot, candidates):
    load_controls = getattr(
        bot,
        "_load_runtime_symbol_controls",
        lambda: {"blocked": set(), "preferred": set()},
    )
    controls = load_controls()
    preferred = controls.get("preferred", set())

    cat_a = []
    cat_b = []
    cat_c = []
    for item in candidates:
        ticker = item.get("ticker", {})
        price = float(ticker.get("last", item.get("last", 0)) or 0)
        volume = float(ticker.get("quoteVolume", item.get("vol_24h", 0)) or 0)
        if price < Config.PRICE_PRIORITY_LIMIT:
            if volume >= 10_000_000:
                cat_a.append(item)
            else:
                cat_b.append(item)
        else:
            cat_c.append(item)

    def get_symbol_score(item):
        try:
            perf = bot.brain.get_symbol_performance(item["symbol"])
            wr = perf.get("wr", 50)
            trades = perf.get("trades", 0)
            if trades >= 5:
                return wr * 0.7 + (min(trades, 50) * 0.3)
            return 50
        except Exception:
            return 50

    cat_a.sort(key=get_symbol_score, reverse=True)
    cat_b.sort(key=get_symbol_score, reverse=True)
    cat_c.sort(key=get_symbol_score, reverse=True)

    half_a = int(len(cat_a) * Config.RADAR_PRIORITY_HIGH_VOL_LOW_PRICE)
    half_b = int(len(cat_b) * Config.RADAR_PRIORITY_HIGH_WR)
    prioritized = (
        cat_a[:half_a]
        + cat_b[:half_b]
        + cat_a[half_a:]
        + cat_c[: int(len(cat_c) * Config.RADAR_PRIORITY_OTHERS)]
        + cat_b[half_b:]
        + cat_c[int(len(cat_c) * Config.RADAR_PRIORITY_OTHERS) :]
    )

    if preferred:
        preferred_items = [
            item for item in prioritized if item["symbol"].split("/")[0] in preferred
        ]
        other_items = [
            item for item in prioritized if item["symbol"].split("/")[0] not in preferred
        ]
        prioritized = preferred_items + other_items
        if preferred_items:
            bot.log(
                f"   - ⭐ Priorización táctica: {len(preferred_items)} símbolos MANTENER al frente"
            )

    return prioritized


def build_operable_targets(bot, snapshot):
    safe_candidates = apply_hard_operability_filters(bot, snapshot)
    tactical_targets = apply_tactical_priority(bot, safe_candidates)
    return tactical_targets[: get_effective_triage_count(bot)]


def seed_targets_state(bot, targets, snapshot):
    target_symbols = [item["symbol"] for item in targets]
    bot.pairs_to_scan = target_symbols
    snapshot_by_symbol = {item["symbol"]: item for item in snapshot}

    with bot.lock:
        existing_syms = {i["symbol"] for i in bot.scanner_history}
        for symbol in target_symbols:
            if symbol in existing_syms:
                continue
            base = symbol.split("/")[0]
            sector = next(
                (k for k, v in Config.SECTORS.items() if any(s.lower() in base.lower() for s in v)),
                "OTHE",
            )
            item = snapshot_by_symbol.get(symbol, {})
            vol_24h = float(item.get("vol_24h", 0.0) or 0.0)
            bot.scanner_history.append(
                {
                    "symbol": symbol,
                    "sector": sector,
                    "tech_checklist": "⏳ PENDING",
                    "ob": "⚪",
                    "ia_prob": "---",
                    "ia_shadow": "⏳",
                    "ia_real": "⏳",
                    "result": "EN COLA...",
                    "signal": "WAIT",
                    "rsi_val": 0,
                    "adx_val": 0,
                    "z_score": 0.0,
                    "vol_24h": vol_24h,
                    "trend_val": "N/A",
                    "funding_rate": 0.0,
                    "votos": {},
                }
            )

    btc_ticker = _snapshot_tickers(snapshot).get("BTC/USDT") or _snapshot_tickers(snapshot).get(
        "BTC/USDT:USDT"
    )
    if btc_ticker:
        bot.market_btc_price = float(btc_ticker["last"])

    bot.log(
        f"✅ Radar {Config.VERSION}: {len(target_symbols)} monedas en mira. BTC: ${bot.market_btc_price}"
    )
    bot.log(f"📋 Objetivos: {', '.join(target_symbols)}")


def acquire_targets(bot):
    """Fase 2: Selección Dinámica de Líderes con Prioridad Inteligente (v110.3)"""
    bot.log("🎯 Buscando pares líderes...")
    try:
        now = utc_now()
        # Limpieza de blacklists expiradas
        bot.blacklist = {s: e for s, e in bot.blacklist.items() if _is_not_expired_until(e, now)}
        cleanup_expired_cooldowns(bot)

        snapshot = bot._get_active_market_snapshot(pool_limit=get_candidate_pool_limit(bot))
        tickers = _snapshot_tickers(snapshot)
        if not snapshot:
            bot.log("⚠️ Snapshot dinámico vacío en acquire_targets.")
            if bot.market_btc_price == 0:
                try:
                    btc_t = bot.execution.fetch_ticker("BTC/USDT")
                    bot.market_btc_price = float(btc_t["last"])
                except Exception as error:
                    bot.log(f"⚠️ No se pudo rescatar BTC ticker con snapshot vacío: {error}")
            return {}

        targets = build_operable_targets(bot, snapshot)
        if not targets:
            bot.pairs_to_scan = []
            bot.log("⚠️ Snapshot válido, pero sin objetivos tras filtros operativos.")
            return tickers

        seed_targets_state(bot, targets, snapshot)

        # La lista se construye desde el mercado activo, no desde Config.PAIRS.
        if len(bot.pairs_to_scan) < get_effective_triage_count(bot):
            bot.log(
                f"⚠️ Solo {len(bot.pairs_to_scan)} pares filtrados (lista dinámica del mercado)."
            )

        # Auto-recuperación del precio BTC si no vino en el batch.
        if "BTC/USDT" in tickers or "BTC/USDT:USDT" in tickers:
            btc_ticker = tickers.get("BTC/USDT:USDT", tickers.get("BTC/USDT"))
            bot.market_btc_price = float(btc_ticker["last"])
        elif bot.market_btc_price == 0:
            # Intento forzado si no vino en el paquete
            try:
                btc_t = bot.execution.fetch_ticker("BTC/USDT")
                bot.market_btc_price = float(btc_t["last"])
            except Exception as error:
                bot.log(f"⚠️ No se pudo rescatar BTC ticker en acquire_targets: {error}")

        return tickers

    except Exception as e:
        bot.log(f"⚠️ Error en acquire_targets: {e}")
        # Fallback resiliente: reutilizar snapshot dinámico si está disponible.
        try:
            ranked = bot._get_active_market_snapshot(pool_limit=get_candidate_pool_limit(bot))
            targets = build_operable_targets(bot, ranked) if ranked else []
            if targets:
                seed_targets_state(bot, targets, ranked)
                bot.log(
                    f"♻️ Fallback acquire_targets: {len(bot.pairs_to_scan)} pares desde snapshot dinámico."
                )
                return {item["symbol"]: item.get("ticker", {}) for item in ranked}
        except Exception as error:
            bot.log(f"⚠️ Fallback snapshot dinámico falló en acquire_targets: {error}")
        # Último intento de rescate de BTC si todo lo demás falla.
        try:
            btc_t = bot.execution.fetch_ticker("BTC/USDT")
            bot.market_btc_price = float(btc_t["last"])
        except Exception as error:
            bot.log(f"⚠️ No se pudo rescatar BTC ticker en fallback final: {error}")
        # No vaciar radar si ya hay lista previa válida.
        if not bot.pairs_to_scan:
            bot.pairs_to_scan = []
        return {}


def get_active_market_snapshot(bot, pool_limit=None):
    """
    [DINÁMICO] Top liquidez por Config.TOP_TRIAGE_COUNT (default 30).

    Lógica:
      - Stateless: Ya no mantiene pares fijos por RVOL.
      - Refresh mercado cada 5 min (peso 40) para armar pool de liquidez diaria.
      - En cada ciclo (peso 1), evalúa spreads reales.
      - Ordena todos los futuros activos por quoteVolume (24h liquidez real).
      - Toma los top Config.TOP_TRIAGE_COUNT pares que pasen el filtro de spread.

    Returns:
        List[Dict]: Pares activos ordenados por volumen bruto desc.
    """
    try:
        # Inicializar cachés de mercado si no existen
        if not hasattr(bot, "_market_cache"):
            bot._market_cache = {}
        if not hasattr(bot, "_market_cache_ts"):
            bot._market_cache_ts = 0

        requested_limit = (
            pool_limit if pool_limit is not None else getattr(Config, "TOP_TRIAGE_COUNT", 25)
        )
        MAX_PAIRS = max(1, int(requested_limit or 25))
        # [BEAR_TREND] Reducir universo de pares en régimen bajista
        try:
            btc_regime = getattr(bot, "market_regime", "UNKNOWN")
            if btc_regime == "BEAR_TREND":
                if pool_limit is None:
                    MAX_PAIRS = min(
                        MAX_PAIRS,
                        max(1, int(getattr(Config, "BEAR_TREND_MAX_PAIRS", 15) or 15)),
                    )
        except Exception:
            bot.log("⚠️ BEAR_TREND pair reduction omitido, usando defaults")

        MAX_SPREAD = float(getattr(Config, "TRIAGE_SPREAD_MAX", 0.0005))
        MARKET_REFRESH = 600  # 10 min

        # --- CAPA 0: BookTicker para spreads reales (peso ~1) ---
        bid_ask_map = {}
        try:
            book_tickers = bot.execution.fetch_book_tickers()
            for bt in book_tickers:
                raw_sym = bt.get("symbol", "")
                bid_price = float(bt.get("bidPrice", 0) or 0)
                ask_price = float(bt.get("askPrice", 0) or 0)
                if raw_sym and bid_price > 0 and ask_price > 0:
                    bid_ask_map[raw_sym] = {"bid": bid_price, "ask": ask_price}
        except Exception as e:
            bot.log(f"⚠️ [TRIAJE] BookTicker falló: {e}")

        # --- CAPA 1: Refresh del mercado cada 5 min (peso 40) ---
        now = time.time()
        if now - bot._market_cache_ts > MARKET_REFRESH or not bot._market_cache:
            bot.log("📡 [TRIAJE ELITE] Refresh mercado completo (cada 5 min)...")
            try:
                if not bot.execution.has_markets_loaded():
                    bot.execution.load_markets()

                if (
                    hasattr(bot, "weight_tracker")
                    and bot.weight_tracker
                    and bot.weight_tracker.should_block("market")
                ):
                    bot.log("🛑 [TRIAJE] Saltando refresh mercado por presión de API Weight")
                else:
                    raw_tickers = bot.execution.fetch_tickers(params={"type": "future"})

                    # Construir pool de candidatos inicial
                    all_candidates = []
                    for symbol, ticker in raw_tickers.items():
                        if not (symbol.endswith("/USDT") or symbol.endswith("/USDT:USDT")):
                            continue
                        if any(
                            x in symbol for x in ["DOWN", "UP", "BEAR", "BULL", "_", "BUSD", "USDC"]
                        ):
                            continue
                        clean_sym = Config.sanitize_symbol(symbol)
                        if clean_sym and clean_sym.endswith("/USDT"):
                            vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                            last = float(ticker.get("last", 0) or 0)
                            if vol_24h <= 0:
                                base_vol = float(ticker.get("baseVolume", 0) or 0)
                                vol_24h = base_vol * last
                            compact_ticker = _compact_ticker(ticker, vol_24h=vol_24h, last=last)
                            all_candidates.append(
                                {
                                    "symbol": clean_sym,
                                    "ticker": compact_ticker,
                                    "vol_24h": vol_24h,
                                    "last": last,
                                }
                            )

                    bot._market_cache = {
                        "candidates": all_candidates,
                    }
                    bot._market_cache_ts = now
                    bot.log(f"✅ [TRIAJE] {len(all_candidates)} candidatos liquidez cacheados")
            except Exception as e_tickers:
                bot.log(f"⚠️ [TRIAJE] fetch_tickers falló: {e_tickers}")
                if getattr(bot, "_market_cache", None) is None:
                    bot._market_cache = {"candidates": []}

        all_candidates = bot._market_cache.get("candidates", [])

        # --- PASO 2: Ordenar estrictamente por liquidez (quoteVolume) ---
        # Garantiza que evaluamos los megacaps primero
        all_candidates.sort(key=lambda x: x["vol_24h"], reverse=True)

        ranked = []
        for cand in all_candidates:
            sym = cand["symbol"]
            ticker = cand["ticker"]
            last = cand["last"]
            vol_24h = cand["vol_24h"]

            # Spread check
            raw_key = sym.replace("/", "").replace(":USDT", "")
            book_data = bid_ask_map.get(raw_key)
            if book_data:
                spread = (book_data["ask"] - book_data["bid"]) / book_data["ask"]
            else:
                ask = float(ticker.get("ask", 0) or 0)
                bid = float(ticker.get("bid", 0) or 0)
                spread = (ask - bid) / last if (last > 0 and ask > bid) else None

            if spread is None or spread > MAX_SPREAD:
                continue

            # Agregar a los Top
            ranked.append(
                {
                    "symbol": sym,
                    "symbol_raw": sym,
                    "rvol": 1.0,  # Legacy alias fallback
                    "vol_24h": vol_24h,
                    "status": "ACTIVE",
                    "ticker": ticker,
                }
            )

            if len(ranked) >= MAX_PAIRS:
                break

        # [Opcional] Limpiar viejas variables stateful de memoria para ahorrar estado
        if hasattr(bot, "_dynamic_pair_list"):
            del bot._dynamic_pair_list
        if hasattr(bot, "_vol_ema"):
            del bot._vol_ema
        if hasattr(bot, "_market_scan_offset"):
            del bot._market_scan_offset

        top_symbols = [
            f"{item['symbol']} (${item['vol_24h'] / 1_000_000:.0f}M)" for item in ranked[:5]
        ]
        bot.log(
            f"🎯 ELITE TRIAJE: {len(ranked)}/{MAX_PAIRS} pares activos (Pura Liquidez) | "
            f"Top 5: {', '.join(top_symbols)}"
        )

        return ranked

    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        bot.log(f"⚠️ Error en get_active_market_snapshot: {e}")
        bot.log(f"TRACEBACK: {tb}")
        return []
