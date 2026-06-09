from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class VectorBacktestResult:
    objective: float
    profit_factor: float
    max_drawdown: float
    net_return_pct: float
    trades: int
    gross_profit: float
    gross_loss: float


class VectorBacktester:
    """Motor de backtest vectorizado para MT + SR.

    Diseñado para evaluar miles de velas sin bucles candle-by-candle.
    """

    def __init__(self, candles: pd.DataFrame):
        required = {"time", "open", "high", "low", "close", "volume"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"Missing candle columns: {sorted(missing)}")

        work = candles.copy()
        if pd.api.types.is_numeric_dtype(work["time"]):
            work["time"] = pd.to_datetime(work["time"], unit="ms", utc=True)
        elif not pd.api.types.is_datetime64_any_dtype(work["time"]):
            work["time"] = pd.to_datetime(work["time"], utc=True, errors="coerce")

        work = work.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
        self.df = work
        self.close = work["close"].astype(float).to_numpy()
        self.high = work["high"].astype(float).to_numpy()
        self.low = work["low"].astype(float).to_numpy()

    @staticmethod
    def _score_to_probability(score: float, side: int) -> float:
        if side == 0:
            return 0.50
        deviation = abs(float(score) - 50.0)
        prob = 0.50 + min(deviation / 50.0, 1.0) * 0.45
        return float(np.clip(prob, 0.50, 0.95))

    @staticmethod
    def _alma_weights(window: int, offset: float, sigma: float) -> np.ndarray:
        m = int(offset * (window - 1))
        s = window / max(sigma, 1e-9)
        idx = np.arange(window)
        w = np.exp(-((idx - m) ** 2) / (2 * s * s))
        w_sum = float(w.sum())
        if w_sum <= 0:
            return np.ones(window, dtype=float) / window
        return w / w_sum

    @staticmethod
    def _rolling_weighted(series: np.ndarray, weights: np.ndarray) -> np.ndarray:
        window = len(weights)
        out = np.full(series.shape[0], np.nan, dtype=float)
        if series.shape[0] < window:
            return out
        conv = np.convolve(series, weights[::-1], mode="valid")
        out[window - 1 :] = conv
        return out

    @staticmethod
    def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
        out = np.full(arr.shape[0], np.nan, dtype=float)
        if arr.shape[0] < window:
            return out
        kernel = np.ones(window, dtype=float) / window
        conv = np.convolve(arr, kernel, mode="valid")
        out[window - 1 :] = conv
        return out

    @staticmethod
    def _rolling_entropy(returns: np.ndarray, bins: int, window: int = 20) -> np.ndarray:
        out = np.zeros(returns.shape[0], dtype=float)
        if returns.shape[0] < window:
            return out

        wins = np.lib.stride_tricks.sliding_window_view(returns, window_shape=window)
        mins = wins.min(axis=1)
        maxs = wins.max(axis=1)
        spans = maxs - mins

        safe_spans = np.where(spans <= 0, 1.0, spans)
        norm = (wins - mins[:, None]) / safe_spans[:, None]
        idx = np.floor(norm * bins).astype(int)
        idx = np.clip(idx, 0, bins - 1)

        one_hot = np.eye(bins, dtype=float)[idx]
        counts = one_hot.sum(axis=1)
        probs = counts / float(window)

        with np.errstate(divide="ignore", invalid="ignore"):
            entropy = -(probs * np.log2(probs))
        entropy = np.nan_to_num(entropy, nan=0.0, posinf=0.0, neginf=0.0).sum(axis=1)

        entropy = np.where(spans <= 0, 0.0, entropy)
        out[window - 1 :] = entropy
        return out

    def _signal_components(
        self,
        alma_offset: float,
        alma_sigma: float,
        z_score_threshold: float,
        entropy_bins: int,
        adx_threshold: float,
        strategy_mode: str = "mt_sr_regime",
    ) -> dict[str, np.ndarray]:
        # MT vectorizado
        w9 = self._alma_weights(9, alma_offset, alma_sigma)
        w20 = self._alma_weights(20, alma_offset, alma_sigma)
        alma_short = self._rolling_weighted(self.close, w9)
        alma_long = self._rolling_weighted(self.close, w20)

        mom_now = np.divide(
            alma_short - alma_long,
            alma_long,
            out=np.zeros_like(alma_short),
            where=np.abs(alma_long) > 1e-12,
        )
        mom_prev = np.roll(mom_now, 1)
        mom_prev[0] = 0.0

        mt_vote = np.full(self.close.shape[0], 50.0, dtype=float)
        mt_vote = np.where((mom_now > 0.001) & (mom_prev > 0.0005), 70.0, mt_vote)
        mt_vote = np.where((mom_now < -0.003) & (mom_prev < -0.001), 30.0, mt_vote)

        # SR vectorizado (Z dinámico + entropía)
        prev_close = np.roll(self.close, 1)
        prev_close[0] = self.close[0]

        tr1 = self.high - self.low
        tr2 = np.abs(self.high - prev_close)
        tr3 = np.abs(self.low - prev_close)
        tr = np.maximum.reduce([tr1, tr2, tr3])

        atr = self._rolling_mean(tr, 14)

        # ADX vectorizado (periodo 14)
        up_move = np.diff(self.high, prepend=self.high[0])
        down_move = -np.diff(self.low, prepend=self.low[0])
        plus_dm = np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0)

        plus_di = 100.0 * np.divide(
            self._rolling_mean(plus_dm, 14),
            atr,
            out=np.zeros_like(atr),
            where=np.abs(atr) > 1e-12,
        )
        minus_di = 100.0 * np.divide(
            self._rolling_mean(minus_dm, 14),
            atr,
            out=np.zeros_like(atr),
            where=np.abs(atr) > 1e-12,
        )
        dx = 100.0 * np.divide(
            np.abs(plus_di - minus_di),
            plus_di + minus_di,
            out=np.zeros_like(plus_di),
            where=np.abs(plus_di + minus_di) > 1e-12,
        )
        adx = self._rolling_mean(dx, 14)
        adx = np.nan_to_num(adx, nan=0.0, posinf=0.0, neginf=0.0)

        sma20 = self._rolling_mean(self.close, 20)

        z_dynamic = np.divide(
            self.close - sma20,
            atr * 1.5,
            out=np.zeros_like(self.close, dtype=float),
            where=np.abs(atr) > 1e-12,
        )
        z_dynamic = np.nan_to_num(z_dynamic, nan=0.0, posinf=0.0, neginf=0.0)

        ret = np.empty_like(self.close)
        ret[0] = 0.0
        ret[1:] = np.diff(self.close) / np.where(
            np.abs(self.close[:-1]) > 1e-12, self.close[:-1], 1.0
        )
        entropy = self._rolling_entropy(ret, bins=max(2, int(entropy_bins)), window=20)

        sr_vote = np.full(self.close.shape[0], 50.0, dtype=float)
        sr_vote = np.where(z_dynamic > z_score_threshold, 20.0 + (entropy * 5.0), sr_vote)
        sr_vote = np.where(z_dynamic < -z_score_threshold, 80.0 - (entropy * 5.0), sr_vote)
        sr_vote = np.clip(sr_vote, 0.0, 100.0)

        # Árbitro de régimen por ADX
        # - ADX > adx_threshold: mercado en tendencia => prioriza MT
        # - ADX < 20: mercado lateral => prioriza SR
        # - zona media: mezcla neutral
        mt_weight = np.where(adx > adx_threshold, 1.0, np.where(adx < 20.0, 0.0, 0.5))
        sr_weight = np.where(adx > adx_threshold, 0.0, np.where(adx < 20.0, 1.0, 0.5))

        # Señales vectorizadas. strategy_mode permite ablation testing sin tocar
        # el simulador de ejecución ni los supuestos de comisiones/slippage.
        if strategy_mode == "mt_sr_regime":
            score = (mt_vote * mt_weight) + (sr_vote * sr_weight)
        elif strategy_mode == "mt_only":
            score = mt_vote
        elif strategy_mode == "sr_only":
            score = sr_vote
        elif strategy_mode == "equal_weight":
            score = (mt_vote * 0.5) + (sr_vote * 0.5)
        else:
            raise ValueError(f"Unsupported strategy_mode: {strategy_mode}")
        raw_signal = np.where(score >= 60.0, 1.0, np.where(score <= 40.0, -1.0, 0.0))
        signal = np.roll(raw_signal, 1)
        signal[0] = 0.0
        return {
            "mt_vote": mt_vote,
            "sr_vote": sr_vote,
            "adx": adx,
            "score": score,
            "raw_signal": raw_signal,
            "signal": signal,
        }

    def signal_frame(
        self,
        alma_offset: float,
        alma_sigma: float,
        z_score_threshold: float,
        entropy_bins: int,
        adx_threshold: float,
        strategy_mode: str = "mt_sr_regime",
    ) -> pd.DataFrame:
        components = self._signal_components(
            alma_offset=alma_offset,
            alma_sigma=alma_sigma,
            z_score_threshold=z_score_threshold,
            entropy_bins=entropy_bins,
            adx_threshold=adx_threshold,
            strategy_mode=strategy_mode,
        )
        signal = components["signal"]
        source_score = np.roll(components["score"], 1)
        source_raw_signal = np.roll(components["raw_signal"], 1)
        source_score[0] = np.nan
        source_raw_signal[0] = 0.0
        proxy_label = np.where(signal > 0, "BUY", np.where(signal < 0, "SELL", "NONE"))
        return pd.DataFrame(
            {
                "time": self.df["time"],
                "close": self.close,
                "mt_vote": components["mt_vote"],
                "sr_vote": components["sr_vote"],
                "adx": components["adx"],
                "score": components["score"],
                "raw_signal": components["raw_signal"],
                "signal_source_score": source_score,
                "signal_source_raw_signal": source_raw_signal,
                "signal": signal,
                "proxy_label": proxy_label,
            }
        )

    def evaluate(
        self,
        alma_offset: float,
        alma_sigma: float,
        z_score_threshold: float,
        entropy_bins: int,
        adx_threshold: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        fee_rate: float = 0.0004,
        strategy_mode: str = "mt_sr_regime",
        min_probability_threshold: float = 0.0,
    ) -> VectorBacktestResult:
        components = self._signal_components(
            alma_offset=alma_offset,
            alma_sigma=alma_sigma,
            z_score_threshold=z_score_threshold,
            entropy_bins=entropy_bins,
            adx_threshold=adx_threshold,
            strategy_mode=strategy_mode,
        )
        signal = components["signal"]
        score = components["score"]

        # Simulador event-driven de trades con TP/SL (no overlap de posiciones)
        n = self.close.shape[0]
        sl = max(float(stop_loss_pct), 0.01) / 100.0
        tp = max(float(take_profit_pct), 0.01) / 100.0

        trade_returns = []
        equity_curve = [1.0]

        i = 0
        while i < n:
            side = int(signal[i])
            if side == 0:
                i += 1
                continue

            candle_score = float(score[max(0, i - 1)])
            prob = self._score_to_probability(candle_score, side)
            if prob < min_probability_threshold:
                i += 1
                continue

            entry_price = float(self.close[i])
            if entry_price <= 0:
                i += 1
                continue

            if side == 1:
                sl_price = entry_price * (1.0 - sl)
                tp_price = entry_price * (1.0 + tp)
            else:
                sl_price = entry_price * (1.0 + sl)
                tp_price = entry_price * (1.0 - tp)

            exit_price = float(self.close[-1])
            exit_idx = n - 1

            j = i + 1
            while j < n:
                hi = float(self.high[j])
                lo = float(self.low[j])

                if side == 1:
                    hit_sl = lo <= sl_price
                    hit_tp = hi >= tp_price
                    if hit_sl or hit_tp:
                        # Regla conservadora: si toca ambos en la misma vela, asumimos SL primero.
                        if hit_sl:
                            exit_price = sl_price
                        else:
                            exit_price = tp_price
                        exit_idx = j
                        break
                else:
                    hit_sl = hi >= sl_price
                    hit_tp = lo <= tp_price
                    if hit_sl or hit_tp:
                        if hit_sl:
                            exit_price = sl_price
                        else:
                            exit_price = tp_price
                        exit_idx = j
                        break

                j += 1

            gross_ret = ((exit_price - entry_price) / entry_price) * float(side)
            trade_ret = gross_ret - (2.0 * fee_rate)
            trade_returns.append(float(trade_ret))

            next_equity = max(1e-9, equity_curve[-1] * (1.0 + trade_ret))
            equity_curve.append(next_equity)

            i = exit_idx + 1

        trade_arr = (
            np.array(trade_returns, dtype=float) if trade_returns else np.array([], dtype=float)
        )
        gross_profit = float(trade_arr[trade_arr > 0].sum()) if trade_arr.size else 0.0
        gross_loss = float(-trade_arr[trade_arr < 0].sum()) if trade_arr.size else 0.0

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = 10.0 if gross_profit > 0 else 0.0

        eq = np.array(equity_curve, dtype=float)
        rolling_peak = np.maximum.accumulate(eq)
        drawdown = np.where(rolling_peak > 0, 1.0 - (eq / rolling_peak), 0.0)
        max_dd = float(drawdown.max()) if drawdown.size else 0.0

        net_return_pct = float((eq[-1] - 1.0) * 100.0) if eq.size else 0.0
        trades = int(len(trade_returns))

        low_trade_penalty = max(0, 12 - trades) * 0.05
        objective = float(profit_factor - (max_dd * 4.0) - low_trade_penalty)

        return VectorBacktestResult(
            objective=objective,
            profit_factor=float(profit_factor),
            max_drawdown=max_dd,
            net_return_pct=net_return_pct,
            trades=trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
        )

    def metadata(self) -> dict[str, str]:
        start = self.df["time"].iloc[0]
        end = self.df["time"].iloc[-1]
        rows = len(self.df)
        return {
            "rows": str(rows),
            "start": str(start),
            "end": str(end),
        }
