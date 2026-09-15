from __future__ import annotations

from typing import List, Dict, Optional, Tuple
import pandas as pd


def find_all_fvgs(df: pd.DataFrame, lookback: int = 20) -> Tuple[List[Dict], List[Dict]]:
    """
    Finds Fair Value Gaps in the last `lookback` bars.
    Returns: (bullish_fvgs, bearish_fvgs)
    """
    bull_fvgs = []
    bear_fvgs = []
    n = len(df)
    if n < 3:
        return bull_fvgs, bear_fvgs

    start = max(2, n - lookback)
    for i in range(start, n):
        b1 = df.iloc[i - 2]
        b2 = df.iloc[i - 1]
        b3 = df.iloc[i]

        b1_high = float(b1["high"])
        b1_low = float(b1["low"])
        b3_high = float(b3["high"])
        b3_low = float(b3["low"])

        # Bullish FVG: Bar 1 High < Bar 3 Low (Gap up)
        if b3_low > b1_high:
            gap_size = b3_low - b1_high
            bull_fvgs.append({
                "bar_index": i - 1,
                "time": b2["time"] if "time" in b2 else None,
                "bottom": b1_high,
                "top": b3_low,
                "gap_size": gap_size,
                "mitigated": False,
            })

        # Bearish FVG: Bar 1 Low > Bar 3 High (Gap down)
        if b1_low > b3_high:
            gap_size = b1_low - b3_high
            bear_fvgs.append({
                "bar_index": i - 1,
                "time": b2["time"] if "time" in b2 else None,
                "bottom": b3_high,
                "top": b1_low,
                "gap_size": gap_size,
                "mitigated": False,
            })

    return bull_fvgs, bear_fvgs


def recent_fvg(df: pd.DataFrame, lookback: int = 10) -> dict:
    """Returns the most recent bullish and bearish FVG zones."""
    bulls, bears = find_all_fvgs(df, lookback=lookback)
    return {
        "bullish": (bulls[-1]["bottom"], bulls[-1]["top"]) if bulls else None,
        "bearish": (bears[-1]["bottom"], bears[-1]["top"]) if bears else None,
    }


def check_fvg_mitigation(df: pd.DataFrame, lookback: int = 12) -> dict:
    """
    Checks if the CURRENT bar is retesting / mitigating an active FVG:
    - Bullish Mitigation: Price dipped into the FVG zone [bottom, top], did not break
      significantly below bottom, and shows rejection (closes positive or wicks up).
    - Bearish Mitigation: Price spiked into the FVG zone [bottom, top], did not break
      significantly above top, and shows rejection (closes negative or wicks down).
    """
    if len(df) < 4:
        return {"bullish_mitigation": False, "bearish_mitigation": False, "fvg_zone": None}

    cur = df.iloc[-1]
    cur_low = float(cur["low"])
    cur_high = float(cur["high"])
    cur_close = float(cur["close"])
    cur_open = float(cur["open"])

    # Search past FVGs (excluding the very current candle)
    past_df = df.iloc[:-1]
    bulls, bears = find_all_fvgs(past_df, lookback=lookback)

    bullish_mitigated = False
    bearish_mitigated = False
    matched_zone = None

    # Check recent unmitigated bullish FVGs
    for fvg in reversed(bulls):
        bottom, top = fvg["bottom"], fvg["top"]
        # Bar's low reached into or below the top of the gap
        if cur_low <= top and cur_close >= bottom:
            # Rejection: candle closed above open or high wick bounce
            if cur_close >= (cur_open + cur_low) / 2.0:
                bullish_mitigated = True
                matched_zone = (bottom, top)
                break

    # Check recent unmitigated bearish FVGs
    for fvg in reversed(bears):
        bottom, top = fvg["bottom"], fvg["top"]
        # Bar's high reached into or above the bottom of the gap
        if cur_high >= bottom and cur_close <= top:
            # Rejection: candle closed below open or low wick push
            if cur_close <= (cur_open + cur_high) / 2.0:
                bearish_mitigated = True
                matched_zone = (bottom, top)
                break

    return {
        "bullish_mitigation": bullish_mitigated,
        "bearish_mitigation": bearish_mitigated,
        "fvg_zone": matched_zone,
    }
