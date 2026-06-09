from typing import Any, TypedDict

import pandas as pd

# --- CCXT STRUCTURES ---


class CCXTBalanceInfo(TypedDict, total=False):
    """Estructura de la clave 'info' dentro la respuesta de fetch_balance() en Futuros."""

    totalWalletBalance: str
    availableBalance: str


class CCXTBalanceTotal(TypedDict, total=False):
    """Estructura de la clave 'total' dentro la respuesta de fetch_balance()."""

    USDT: float


class CCXTBalanceResponse(TypedDict, total=False):
    """Estructura completa de la respuesta de fetch_balance() de Binance (CCXT)."""

    info: CCXTBalanceInfo
    total: CCXTBalanceTotal
    USDT: dict[str, Any]


class CCXTOrder(TypedDict, total=False):
    """Estructura de la orden devuelta por create_order en CCXT."""

    id: str
    symbol: str
    type: str
    side: str
    price: float
    amount: float
    filled: float
    status: str
    info: dict[str, Any]


# --- STRATEGY CONTEXT STRUCTURE ---


class RSIData(TypedDict, total=False):
    val: float


class ADXData(TypedDict, total=False):
    val: float


class SignalContext(TypedDict, total=False):
    """
    Contexto enriquecido generado por el motor de estrategia (los 14 agentes).
    Contiene métricas técnicas y de probabilidad IA.
    """

    atr_pct: float
    prob_final: float
    trend: str
    spread: float
    volume: float
    funding_rate: float
    df_15m: pd.DataFrame | None
    rsi: RSIData
    adx: ADXData
    z_score: float
    vol_24h: float
    tier: str
    mode: str
    veto_reason: str | None
    filter_veto: str | None
