from __future__ import annotations

from typing import List, Tuple
import pandas as pd


def find_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> Tuple[List[dict], List[dict]]:
    """
    Identifies fractal Swing Highs and Swing Lows.
    Returns: (swing_highs, swing_lows) where each item is a dict with index, time, price.
    """
    highs = []
    lows = []
    n = len(df)
    if n < left + right + 1:
        return highs, lows

    high_vals = df["high"].values
    low_vals = df["low"].values
    time_vals = df["time"].values if "time" in df.columns else range(n)

    # Only evaluate up to n - 1 - right so right bars are confirmed
    for i in range(left, n - right):
        current_h = high_vals[i]
        current_l = low_vals[i]

        is_high = True
        for offset in range(-left, right + 1):
            if offset != 0 and high_vals[i + offset] >= current_h:
                is_high = False
                break
        if is_high:
            highs.append({"index": i, "time": time_vals[i], "price": float(current_h)})

        is_low = True
        for offset in range(-left, right + 1):
            if offset != 0 and low_vals[i + offset] <= current_l:
                is_low = False
                break
        if is_low:
            lows.append({"index": i, "time": time_vals[i], "price": float(current_l)})

    return highs, lows


def market_bias(df: pd.DataFrame, lookback: int = 30, swing_bars: int = 2) -> str:
    """
    Determines market trend based on:
    1. Primary: True Swing Highs (HH/LH) and Swing Lows (HL/LL)
    2. Secondary: Break of Structure (BOS) from latest swing points
    3. Tertiary: Multi-period EMA slope and direction for runaway/trending phases
    """
    if len(df) < min(15, lookback):
        return "neutral"

    sub_df = df.iloc[-lookback:].copy() if len(df) >= lookback else df.copy()
    highs, lows = find_swings(sub_df, left=swing_bars, right=swing_bars)

    # 1. Need at least 2 highs and 2 lows to establish standard market structure
    if len(highs) >= 2 and len(lows) >= 2:
        last_h, prev_h = highs[-1]["price"], highs[-2]["price"]
        last_l, prev_l = lows[-1]["price"], lows[-2]["price"]

        if last_h > prev_h and last_l >= prev_l:
            return "bullish"
        if last_h <= prev_h and last_l < prev_l:
            return "bearish"

    # 2. Break of Structure (BOS) from latest swing
    latest_close = float(sub_df.iloc[-1]["close"])
    if highs and latest_close > highs[-1]["price"]:
        return "bullish"
    if lows and latest_close < lows[-1]["price"]:
        return "bearish"

    # 3. Dynamic EMA Trend Direction (Robust for strong runaway trends)
    span_fast = min(12, max(4, len(sub_df) // 3))
    span_slow = min(26, max(8, len(sub_df) // 2))
    ema_fast = sub_df["close"].ewm(span=span_fast, adjust=False).mean()
    ema_slow = sub_df["close"].ewm(span=span_slow, adjust=False).mean()

    fast_val = float(ema_fast.iloc[-1])
    slow_val = float(ema_slow.iloc[-1])

    if latest_close > fast_val >= slow_val and latest_close > float(sub_df.iloc[0]["close"]):
        return "bullish"
    if latest_close < fast_val <= slow_val and latest_close < float(sub_df.iloc[0]["close"]):
        return "bearish"

    return "neutral"


def liquidity_sweep(df: pd.DataFrame, lookback: int = 20, swing_bars: int = 2) -> dict:
    """
    Detects Liquidity Sweeps:
    - Bullish Sweep: Current bar dipped below a prior key swing low, but closed back ABOVE it.
    - Bearish Sweep: Current bar spiked above a prior key swing high, but closed back BELOW it.
    """
    if len(df) < lookback + 2:
        return {"bullish": False, "bearish": False, "bull_level": 0.0, "bear_level": 0.0}

    # Search swings in the lookback window up to previous candle
    window = df.iloc[-(lookback + 2):-1].copy()
    cur = df.iloc[-1]

    highs, lows = find_swings(window, left=swing_bars, right=1)

    cur_high = float(cur["high"])
    cur_low = float(cur["low"])
    cur_close = float(cur["close"])

    # Fallback to absolute window extremes if no distinct swing points found
    key_low = lows[-1]["price"] if lows else float(window["low"].min())
    key_high = highs[-1]["price"] if highs else float(window["high"].max())

    bullish_sweep = (cur_low < key_low) and (cur_close > key_low)
    bearish_sweep = (cur_high > key_high) and (cur_close < key_high)

    return {
        "bullish": bool(bullish_sweep),
        "bearish": bool(bearish_sweep),
        "bull_level": float(key_low),
        "bear_level": float(key_high),
        "low_wick": float(cur_close - cur_low) if bullish_sweep else 0.0,
        "high_wick": float(cur_high - cur_close) if bearish_sweep else 0.0,
    }
