from __future__ import annotations

import pandas as pd

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def _tf(name: str):
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not installed")
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }
    if name not in mapping:
        raise ValueError(f"Unsupported timeframe: {name}")
    return mapping[name]


def get_rates(symbol: str, timeframe: str, bars: int = 500) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, _tf(timeframe), 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No rates for {symbol} {timeframe}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    cols = ["time", "open", "high", "low", "close", "tick_volume"]
    return df[cols]
