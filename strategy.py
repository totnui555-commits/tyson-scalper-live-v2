from __future__ import annotations

import pandas as pd
from models import Signal
from indicators import atr, rsi, ema, bullish_engulfing, bearish_engulfing
from structure import market_bias, liquidity_sweep, find_swings
from fvg import check_fvg_mitigation


def build_signal(exec_df: pd.DataFrame, bias_df: pd.DataFrame, cfg: dict) -> Signal:
    """
    Gold Scalper Signal Engine:
    - Multi-timeframe confluence: Bias TF (M15/M5) -> Execution TF (M5/M1)
    - ICT / SMC setup: Liquidity Sweep + Fair Value Gap (FVG) Mitigation
    - Hard Gates: No trading against higher timeframe structure
    - Gold-safe SL Floor: Enforces minimum stop distance against spread drag
    """
    s = cfg["strategy"]
    f = cfg.get("filters", {})

    min_bars = max(s.get("liquidity_lookback", 20) + 5, s.get("atr_period", 14) + 5, s.get("rsi_period", 14) + 5)
    if len(exec_df) < min_bars or len(bias_df) < s.get("swing_lookback", 20):
        return Signal("none", 0, reason="insufficient_data")

    exec_df = exec_df.copy()
    exec_df["atr"] = atr(exec_df, s.get("atr_period", 14))
    exec_df["rsi"] = rsi(exec_df, s.get("rsi_period", 14))
    exec_df["ema20"] = ema(exec_df["close"], 20)
    exec_df["ema50"] = ema(exec_df["close"], 50)

    last = exec_df.iloc[-1]
    atr_now = float(last["atr"])
    if pd.isna(atr_now) or atr_now <= 0:
        return Signal("none", 0, reason="atr_unavailable")

    # 1. Higher Timeframe Market Structure (Hard Gate)
    bias = market_bias(bias_df, s.get("swing_lookback", 25))

    # 2. Execution TF Dynamics
    sweep = liquidity_sweep(exec_df, s.get("liquidity_lookback", 20))
    fvg_mit = check_fvg_mitigation(exec_df, s.get("fvg_lookback", 12))

    body = abs(float(last["close"] - last["open"]))
    displacement = body >= float(s.get("displacement_atr_mult", 0.85)) * atr_now
    rsi_now = float(last["rsi"])
    cur_close = float(last["close"])
    cur_ema20 = float(last["ema20"])
    cur_ema50 = float(last["ema50"])

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    # Higher Timeframe Bias Scoring (1 pt)
    if bias == "bullish":
        long_score += 1
        long_reasons.append(f"HTF bullish structure")
    elif bias == "bearish":
        short_score += 1
        short_reasons.append(f"HTF bearish structure")

    # Liquidity Sweep (High-probability trigger: +2 pts)
    if sweep["bullish"]:
        long_score += 2
        long_reasons.append("sell-side liquidity sweep")
    if sweep["bearish"]:
        short_score += 2
        short_reasons.append("buy-side liquidity sweep")

    # FVG Mitigation (High-probability trigger: +2 pts)
    if fvg_mit["bullish_mitigation"]:
        long_score += 2
        long_reasons.append("bullish FVG mitigation")
    if fvg_mit["bearish_mitigation"]:
        short_score += 2
        short_reasons.append("bearish FVG mitigation")

    # Momentum / Candle Confirmation (+1 pt)
    if bullish_engulfing(exec_df) or (displacement and last["close"] > last["open"]):
        long_score += 1
        long_reasons.append("bullish momentum/engulfing")
    if bearish_engulfing(exec_df) or (displacement and last["close"] < last["open"]):
        short_score += 1
        short_reasons.append("bearish momentum/engulfing")

    # Moving Average Alignment (+1 pt)
    if cur_close > cur_ema20 and cur_ema20 >= cur_ema50:
        long_score += 1
        long_reasons.append("EMA alignment (20>50)")
    if cur_close < cur_ema20 and cur_ema20 <= cur_ema50:
        short_score += 1
        short_reasons.append("EMA alignment (20<50)")

    # RSI Filter (+1 pt) - Healthy trend momentum zone (prevents buying overbought tops)
    if 42.0 <= rsi_now <= 60.0:
        long_score += 1
        long_reasons.append(f"RSI momentum ({rsi_now:.1f})")
    if 40.0 <= rsi_now <= 58.0:
        short_score += 1
        short_reasons.append(f"RSI momentum ({rsi_now:.1f})")

    threshold = int(s.get("score_threshold", 4))
    entry = float(last["close"])
    bar_time = pd.Timestamp(last["time"]).isoformat() if "time" in last else ""

    # HARD GATES: Absolutely no counter-trend entries against confirmed HTF bias
    if bias == "bearish" and long_score > short_score:
        return Signal("none", long_score, bar_time=bar_time, atr=atr_now,
                      reason=f"HARD GATE: Bullish signal blocked by Bearish HTF bias")
    if bias == "bullish" and short_score > long_score:
        return Signal("none", short_score, bar_time=bar_time, atr=atr_now,
                      reason=f"HARD GATE: Bearish signal blocked by Bullish HTF bias")

    # ICT Equilibrium Range Filter: Never buy in upper Premium, never sell in deep Discount
    look_n = int(s.get("fvg_lookback", 12))
    recent_bars = exec_df.iloc[-(look_n + 1):-1]
    range_high = float(recent_bars["high"].max())
    range_low = float(recent_bars["low"].min())
    eq = (range_high + range_low) / 2.0
    buffer = 0.15 * (range_high - range_low)

    if long_score >= threshold and entry > (eq + buffer):
        return Signal("none", long_score, bar_time=bar_time, atr=atr_now,
                      reason=f"VALUE GATE: Price in extreme Premium zone (> {eq+buffer:.2f}), skip buying local top")

    if short_score >= threshold and entry < (eq - buffer):
        return Signal("none", short_score, bar_time=bar_time, atr=atr_now,
                      reason=f"VALUE GATE: Price in extreme Discount zone (< {eq-buffer:.2f}), skip selling local bottom")

    # Minimum Stop Loss Distance Floor for Gold (e.g. $2.50)
    min_sl_dist = float(f.get("min_sl_distance_price", 2.50))
    sl_buffer = float(s.get("sl_atr_buffer", 0.25)) * atr_now
    rr = float(s.get("rr", 1.50))

    # --- LONG SIGNAL ---
    if long_score >= threshold and long_score > short_score:
        # Stop loss anchored below swing low or sweep level
        sl_anchor = float(last["low"])
        if sweep["bullish"] and sweep["bull_level"] > 0:
            sl_anchor = min(sl_anchor, sweep["bull_level"])
        if fvg_mit["bullish_mitigation"] and fvg_mit["fvg_zone"]:
            sl_anchor = min(sl_anchor, fvg_mit["fvg_zone"][0])

        sl = min(sl_anchor - sl_buffer, entry - atr_now)
        # Enforce minimum stop loss floor
        if entry - sl < min_sl_dist:
            sl = entry - min_sl_dist

        risk = entry - sl
        if risk <= 0:
            return Signal("none", 0, reason="invalid_long_risk")

        tp = entry + rr * risk
        return Signal("buy", long_score, bar_time, entry, sl, tp, atr_now, "; ".join(long_reasons))

    # --- SHORT SIGNAL ---
    if short_score >= threshold and short_score > long_score:
        # Stop loss anchored above swing high or sweep level
        sl_anchor = float(last["high"])
        if sweep["bearish"] and sweep["bear_level"] > 0:
            sl_anchor = max(sl_anchor, sweep["bear_level"])
        if fvg_mit["bearish_mitigation"] and fvg_mit["fvg_zone"]:
            sl_anchor = max(sl_anchor, fvg_mit["fvg_zone"][1])

        sl = max(sl_anchor + sl_buffer, entry + atr_now)
        # Enforce minimum stop loss floor
        if sl - entry < min_sl_dist:
            sl = entry + min_sl_dist

        risk = sl - entry
        if risk <= 0:
            return Signal("none", 0, reason="invalid_short_risk")

        tp = entry - rr * risk
        return Signal("sell", short_score, bar_time, entry, sl, tp, atr_now, "; ".join(short_reasons))

    return Signal("none", max(long_score, short_score), bar_time=bar_time, atr=atr_now,
                  reason=f"no_setup | bias={bias} long={long_score} short={short_score}")
