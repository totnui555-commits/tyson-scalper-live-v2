from __future__ import annotations

import numpy as np
import pandas as pd


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's Running Moving Average (RMA / alpha=1/period)."""
    return series.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return rma(tr, period)


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    res = 100 - (100 / (1 + rs))
    return res.fillna(50)


def bullish_engulfing(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    a, b = df.iloc[-2], df.iloc[-1]
    a_body = abs(float(a["close"] - a["open"]))
    b_body = abs(float(b["close"] - b["open"]))
    return (
        b["close"] > b["open"]  # current is green
        and a["close"] < a["open"]  # prior was red
        and float(b["close"]) >= float(a["open"])  # current close covers prior open
        and b_body >= a_body * 0.85  # significant body size
    )


def bearish_engulfing(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    a, b = df.iloc[-2], df.iloc[-1]
    a_body = abs(float(a["close"] - a["open"]))
    b_body = abs(float(b["close"] - b["open"]))
    return (
        b["close"] < b["open"]  # current is red
        and a["close"] > a["open"]  # prior was green
        and float(b["close"]) <= float(a["open"])  # current close covers prior open
        and b_body >= a_body * 0.85  # significant body size
    )
