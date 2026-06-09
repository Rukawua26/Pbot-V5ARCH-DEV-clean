"""
BINANCE API WEIGHT TRACKER
==========================
Sistema completo de monitoreo y protección contra rate limits de Binance Futures.

Binance Futures Rate Limits:
- Request Weight: 2400 per minute
- Order Rate: 50 per 10 seconds (per symbol)
- Open Orders: 200 per symbol
- Raw Requests: 6000 per minute

Endpoint Weights (Futures):
- GET /fapi/v1/ticker/bookTicker: 1
- GET /fapi/v1/ticker/price: 1
- GET /fapi/v1/ticker/24hr (single): 1
- GET /fapi/v1/ticker/24hr (all): 40
- GET /fapi/v1/klines: 1
- GET /fapi/v1/depth (limit 5-100): 1
- GET /fapi/v1/depth (limit 100-500): 2
- GET /fapi/v1/depth (limit 500-1000): 5
- GET /fapi/v1/depth (limit 1000-5000): 10
- GET /fapi/v2/ticker/price: 1
- GET /fapi/v1/premiumIndex: 1
- GET /fapi/v1/account: 5
- GET /fapi/v2/balance: 5
- GET /fapi/v2/positionRisk: 5
- POST /fapi/v1/order: 1
- DELETE /fapi/v1/order: 1
- DELETE /fapi/v1/allOpenOrders: 1
- POST /fapi/v1/leverage: 1
- GET /fapi/v1/exchangeInfo: 10
- GET /fapi/v1/premiumIndex (all): 10
"""

import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger("APIWeightTracker")


class BinanceWeightTracker:
    """
    Trackea el peso acumulado de las llamadas a la API de Binance
    con ventana deslizante de 1 minuto y protección automática.
    """

    # Binance Futures limits
    WEIGHT_LIMIT_PER_MINUTE = 2400
    ORDER_LIMIT_PER_10S = 50
    RAW_REQUEST_LIMIT_PER_MIN = 6000

    # Warning thresholds (percentage of limit)
    WARNING_THRESHOLD = 0.60  # 60% - warning
    CRITICAL_THRESHOLD = 0.80  # 80% - critical
    EMERGENCY_THRESHOLD = 0.95  # 95% - emergency, block non-essential

    # Endpoint weight mapping
    ENDPOINT_WEIGHTS = {
        # Market data - light
        "fapiPublicGetTickerBookTicker": 1,
        "fapiPublicGetTickerPrice": 1,
        "fapiPublicGetPremiumIndex": 1,
        "fetch_ticker": 1,
        "fetch_funding_rate": 1,
        "fetch_order_book": 1,  # limit <= 100
        "fetch_order_book_deep": 5,  # limit > 500
        "fetch_ohlcv": 1,
        "fetch_ohlcv_batch": 1,
        # Market data - heavy
        "fetch_tickers": 40,
        "fapiPublicGetTicker24hr": 40,
        "load_markets": 10,
        "fetch_markets": 10,
        # Account data
        "fetch_balance": 5,
        "fetch_positions": 5,
        "fetch_position": 5,
        "fetch_position_mode": 1,
        "fetch_account": 5,
        "fapiPrivateGetPositionSideDual": 1,
        # Trading
        "create_order": 1,
        "place_order": 1,
        "cancel_order": 1,
        "cancel_all_orders": 1,
        "set_leverage": 1,
        "fetch_my_trades": 5,
        "fetch_open_orders": 5,
        "fetch_closed_orders": 5,
    }

    def __init__(self):
        self._lock = threading.Lock()

        # Sliding window: list of (timestamp, weight, endpoint, category)
        self._weight_log = []

        # Per-endpoint counters (for current window)
        self._endpoint_counts = defaultdict(int)

        # Per-category counters
        self._category_counts = defaultdict(int)

        # Order tracking (for per-10s limit)
        self._order_log = []

        # Stats
        self._total_requests = 0
        self._total_weight = 0
        self._warnings_issued = 0
        self._blocks_issued = 0

        # Callback for alerts
        self._alert_callback = None

        # Track if we're in emergency mode
        self._emergency_mode = False
        self._emergency_since = 0

    def set_alert_callback(self, callback):
        """Set callback function for alerts: callback(level, message)"""
        self._alert_callback = callback

    def _alert(self, level: str, message: str):
        """Issue alert through callback and logging"""
        if self._alert_callback:
            self._alert_callback(level, message)

        if level == "WARNING":
            logger.warning(f"⚠️ {message}")
        elif level == "CRITICAL":
            logger.critical(f"🚨 {message}")
        elif level == "EMERGENCY":
            logger.critical(f"🛑 {message}")
        else:
            logger.info(f"ℹ️ {message}")

    def track(self, endpoint: str, weight: int | None = None, category: str = "market"):
        """
        Track an API call with its weight.

        Args:
            endpoint: Name of the API endpoint
            weight: Weight cost (auto-detected if None)
            category: Category - 'market', 'account', 'trading', 'essential'
        """
        now = time.time()

        if weight is None:
            weight = self.ENDPOINT_WEIGHTS.get(endpoint, 1)

        with self._lock:
            # Add to sliding window
            self._weight_log.append((now, weight, endpoint, category))
            self._endpoint_counts[endpoint] += 1
            self._category_counts[category] += weight

            # Track orders separately
            if category == "trading":
                self._order_log.append(now)

            # Update totals
            self._total_requests += 1
            self._total_weight += weight

            # Clean old entries (older than 60 seconds)
            cutoff = now - 60
            self._weight_log = [(t, w, e, c) for t, w, e, c in self._weight_log if t > cutoff]

            # Clean old order entries (older than 10 seconds)
            order_cutoff = now - 10
            self._order_log = [t for t in self._order_log if t > order_cutoff]

        # Check thresholds
        self._check_thresholds(now)

    def _check_thresholds(self, now: float):
        """Check if we're approaching rate limits"""
        with self._lock:
            # Calculate current weight in window
            cutoff = now - 60
            current_weight = sum(w for t, w, e, c in self._weight_log if t > cutoff)

            # Calculate order rate
            order_cutoff = now - 10
            current_orders = sum(1 for t in self._order_log if t > order_cutoff)

        usage_pct = current_weight / self.WEIGHT_LIMIT_PER_MINUTE
        order_usage_pct = current_orders / self.ORDER_LIMIT_PER_10S

        with self._lock:
            # Emergency mode check
            if usage_pct >= self.EMERGENCY_THRESHOLD:
                if not self._emergency_mode:
                    self._emergency_mode = True
                    self._emergency_since = now
                    self._blocks_issued += 1
                    self._alert(
                        "EMERGENCY",
                        f"API Weight EMERGENCY: {current_weight}/{self.WEIGHT_LIMIT_PER_MINUTE} "
                        f"({usage_pct * 100:.1f}%) - Non-essential calls blocked!",
                    )
                return

            # Clear emergency mode if below critical
            if self._emergency_mode and usage_pct < self.CRITICAL_THRESHOLD:
                duration = now - self._emergency_since
                self._emergency_mode = False
                self._alert(
                    "WARNING",
                    f"API Weight recovered from emergency after {duration:.0f}s. "
                    f"Current: {current_weight}/{self.WEIGHT_LIMIT_PER_MINUTE} ({usage_pct * 100:.1f}%)",
                )

            # Critical threshold
            if usage_pct >= self.CRITICAL_THRESHOLD:
                self._warnings_issued += 1
                self._alert(
                    "CRITICAL",
                    f"API Weight CRITICAL: {current_weight}/{self.WEIGHT_LIMIT_PER_MINUTE} "
                    f"({usage_pct * 100:.1f}%) - Reduce non-essential calls!",
                )
            elif usage_pct >= self.WARNING_THRESHOLD:
                self._warnings_issued += 1
                self._alert(
                    "WARNING",
                    f"API Weight WARNING: {current_weight}/{self.WEIGHT_LIMIT_PER_MINUTE} "
                    f"({usage_pct * 100:.1f}%)",
                )

            # Order rate check
            if order_usage_pct >= 0.8:
                self._alert(
                    "CRITICAL",
                    f"Order rate CRITICAL: {current_orders}/{self.ORDER_LIMIT_PER_10S} per 10s "
                    f"({order_usage_pct * 100:.1f}%)",
                )

    def should_block(self, category: str = "market") -> bool:
        """
        Check if a call should be blocked based on current usage.

        Returns True if the call should be blocked.
        """
        now = time.time()

        with self._lock:
            cutoff = now - 60
            current_weight = sum(w for t, w, e, c in self._weight_log if t > cutoff)

        usage_pct = current_weight / self.WEIGHT_LIMIT_PER_MINUTE

        # Emergency mode: block everything except essential
        if self._emergency_mode:
            if category not in ("essential", "trading"):
                return True

        # Critical: block non-essential categories
        if usage_pct >= self.CRITICAL_THRESHOLD:
            if category not in ("essential", "trading"):
                return True

        return False

    def get_current_weight(self) -> int:
        """Get current weight usage in the sliding window"""
        now = time.time()
        with self._lock:
            cutoff = now - 60
            return sum(w for t, w, e, c in self._weight_log if t > cutoff)

    def get_usage_percentage(self) -> float:
        """Get current usage as percentage of limit"""
        return self.get_current_weight() / self.WEIGHT_LIMIT_PER_MINUTE * 100

    def get_status(self) -> dict:
        """Get comprehensive status report"""
        now = time.time()

        with self._lock:
            cutoff = now - 60
            current_weight = sum(w for t, w, e, c in self._weight_log if t > cutoff)
            current_orders = sum(1 for t in self._order_log if t > now - 10)

            # Per-endpoint breakdown
            endpoint_breakdown = {}
            for t, w, e, c in self._weight_log:
                if t > cutoff:
                    if e not in endpoint_breakdown:
                        endpoint_breakdown[e] = {"count": 0, "weight": 0}
                    endpoint_breakdown[e]["count"] += 1
                    endpoint_breakdown[e]["weight"] += w

            # Per-category breakdown
            category_breakdown = {}
            for t, w, e, c in self._weight_log:
                if t > cutoff:
                    if c not in category_breakdown:
                        category_breakdown[c] = 0
                    category_breakdown[c] += w

        usage_pct = current_weight / self.WEIGHT_LIMIT_PER_MINUTE * 100
        order_usage_pct = current_orders / self.ORDER_LIMIT_PER_10S * 100

        # Determine status level
        if self._emergency_mode:
            level = "🛑 EMERGENCY"
        elif usage_pct >= 80:
            level = "🚨 CRITICAL"
        elif usage_pct >= 60:
            level = "⚠️ WARNING"
        else:
            level = "✅ OK"

        return {
            "level": level,
            "current_weight": current_weight,
            "limit": self.WEIGHT_LIMIT_PER_MINUTE,
            "usage_pct": round(usage_pct, 1),
            "remaining": self.WEIGHT_LIMIT_PER_MINUTE - current_weight,
            "orders_per_10s": current_orders,
            "order_limit": self.ORDER_LIMIT_PER_10S,
            "order_usage_pct": round(order_usage_pct, 1),
            "emergency_mode": self._emergency_mode,
            "total_requests": self._total_requests,
            "total_weight": self._total_weight,
            "warnings": self._warnings_issued,
            "blocks": self._blocks_issued,
            "endpoints": endpoint_breakdown,
            "categories": category_breakdown,
        }

    def get_formatted_report(self) -> str:
        """Get human-readable status report"""
        status = self.get_status()

        lines = [
            f"📊 API Weight Status: {status['level']}",
            f"   Weight: {status['current_weight']}/{status['limit']} ({status['usage_pct']}%)",
            f"   Remaining: {status['remaining']}",
            f"   Orders/10s: {status['orders_per_10s']}/{status['order_limit']} ({status['order_usage_pct']}%)",
            f"   Emergency: {'YES' if status['emergency_mode'] else 'NO'}",
            f"   Total Requests: {status['total_requests']}",
            f"   Warnings: {status['warnings']}, Blocks: {status['blocks']}",
        ]

        if status["endpoints"]:
            lines.append("   Top Endpoints:")
            sorted_endpoints = sorted(
                status["endpoints"].items(), key=lambda x: x[1]["weight"], reverse=True
            )[:5]
            for ep, data in sorted_endpoints:
                lines.append(f"     {ep}: {data['count']} calls, {data['weight']} weight")

        if status["categories"]:
            lines.append("   Categories:")
            for cat, weight in sorted(
                status["categories"].items(), key=lambda x: x[1], reverse=True
            ):
                lines.append(f"     {cat}: {weight} weight")

        return "\n".join(lines)

    def wait_if_needed(self, category: str = "market") -> float:
        """
        Returns seconds to wait before next API call in this category.
        Returns 0.0 if the call can proceed immediately.
        """
        now = time.time()

        with self._lock:
            cutoff = now - 60
            current_weight = sum(w for t, w, e, c in self._weight_log if t > cutoff)

        usage_pct = current_weight / self.WEIGHT_LIMIT_PER_MINUTE

        if self._emergency_mode and category not in ("essential", "trading"):
            remaining = max(0.0, usage_pct - self.CRITICAL_THRESHOLD)
            wait = 60.0 * remaining
            return min(wait, 15.0)

        if usage_pct >= self.CRITICAL_THRESHOLD and category == "market":
            remaining = max(0.0, usage_pct - self.WARNING_THRESHOLD)
            wait = 60.0 * remaining / max(usage_pct, 0.01)
            return min(wait, 10.0)

        return 0.0

    def reset_stats(self):
        """Reset cumulative stats (not the sliding window)"""
        with self._lock:
            self._total_requests = 0
            self._total_weight = 0
            self._warnings_issued = 0
            self._blocks_issued = 0
