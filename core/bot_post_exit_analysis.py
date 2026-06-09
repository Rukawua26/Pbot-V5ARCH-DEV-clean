import os

import pandas as pd


def load_local_candles(symbol, timeframe="1h"):
    try:
        file_name = f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}.parquet"
        path = os.path.join("data_storage", "candles", file_name)
        if not os.path.exists(path):
            return None
        df = pd.read_parquet(path)
        if df is None or df.empty:
            return None
        required = {"time", "high", "low"}
        if not required.issubset(set(df.columns)):
            return None
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    except Exception:
        return None


def calc_post_exit_drift(symbol, side, exit_ts_iso, exit_price, lookahead_bars=4):
    try:
        if not exit_ts_iso or float(exit_price) <= 0:
            return None
        df = load_local_candles(symbol, "1h")
        if df is None or df.empty:
            return None
        ts_ms = int(pd.to_datetime(exit_ts_iso).timestamp() * 1000)
        idx = df[df["time"] >= ts_ms].index
        if len(idx) == 0:
            return None
        i0 = int(idx[0])
        i1 = min(len(df) - 1, i0 + max(1, int(lookahead_bars)))
        window = df.iloc[i0 : i1 + 1]
        if window.empty:
            return None
        if side == "BUY":
            best = float(window["high"].max())
            return ((best - float(exit_price)) / float(exit_price)) * 100.0
        best = float(window["low"].min())
        return ((float(exit_price) - best) / float(exit_price)) * 100.0
    except Exception:
        return None
