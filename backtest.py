from __future__ import annotations

import sys
import pandas as pd

from strategy import build_signal
from utils import load_config, in_session


def resample_tf(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    d = df.set_index("time")
    res = d.resample(freq).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "tick_volume": "sum",
    }).dropna().reset_index()
    return res


def max_consecutive_losses(rs):
    best = cur = 0
    for r in rs:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def backtest(path: str):
    cfg = load_config()
    bcfg = cfg["backtest"]
    rcfg = cfg.get("risk", {})
    spread = float(bcfg["spread_price"])
    slippage = float(bcfg["slippage_price"])
    max_hold = int(bcfg["max_hold_bars"])
    rr = float(cfg["strategy"]["rr"])
    be_trigger_r = float(rcfg.get("breakeven_trigger_r", 1.0))
    be_buffer = float(rcfg.get("breakeven_buffer_price", 0.15))

    raw_df = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(raw_df.columns)
    if missing:
        raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    raw_df["time"] = pd.to_datetime(raw_df["time"], utc=True)
    raw_df = raw_df.sort_values("time").drop_duplicates("time").reset_index(drop=True)

    # Detect base timeframe (e.g. M1 or M5)
    time_diff = (raw_df["time"].iloc[1] - raw_df["time"].iloc[0]).total_seconds()
    if time_diff <= 70:  # M1 data
        m_exec = resample_tf(raw_df, "5min")
        m_bias = resample_tf(raw_df, "15min")
    else:  # already M5 data
        m_exec = raw_df
        m_bias = resample_tf(raw_df, "15min")

    trades = []
    i = 60
    while i < len(m_exec) - 2:
        hist_exec = m_exec.iloc[:i + 1]
        t = hist_exec.iloc[-1]["time"]
        hist_bias = m_bias[m_bias["time"] <= t]
        if len(hist_bias) < 25 or not in_session(cfg, t):
            i += 1
            continue

        sig = build_signal(hist_exec, hist_bias, cfg)
        if sig.side == "none" or sig.atr is None or sig.atr < float(cfg["filters"]["min_atr_price"]):
            i += 1
            continue

        if sig.side == "buy":
            entry = float(sig.entry) + spread + slippage
            sl = float(sig.stop_loss)
            if entry <= sl:
                i += 1
                continue
            risk = entry - sl
            tp = entry + rr * risk
        else:
            entry = float(sig.entry) - slippage
            sl = float(sig.stop_loss)
            if entry >= sl:
                i += 1
                continue
            risk = sl - entry
            tp = entry - rr * risk

        outcome = None
        exit_i = None
        current_sl = sl
        be_active = False
        trail_active = False
        max_r = 0.0

        trail_start_r = float(rcfg.get("trail_start_r", 1.20))
        trail_dist_r = float(rcfg.get("trail_distance_r", 0.75))

        for j in range(i + 1, min(i + 1 + max_hold, len(m_exec))):
            bar = m_exec.iloc[j]
            bar_h = float(bar["high"])
            bar_l = float(bar["low"])

            if sig.side == "buy":
                cur_r = (bar_h - entry) / risk
                max_r = max(max_r, cur_r)

                # A. Breakeven at +0.80R
                if not be_active and max_r >= be_trigger_r:
                    current_sl = max(current_sl, entry + be_buffer)
                    be_active = True

                # B. Dynamic Trailing Stop (Let Profits Run!)
                if max_r >= trail_start_r:
                    trail_sl = bar_h - trail_dist_r * risk
                    if trail_sl > current_sl:
                        current_sl = trail_sl
                        trail_active = True

                # Check if SL or Trailing SL touched
                if bar_l <= current_sl:
                    if be_active or trail_active:
                        outcome = round((current_sl - entry) / risk, 3)
                    else:
                        outcome = -1.0
                    exit_i = j
                    break

            else:  # Sell
                cur_r = (entry - (bar_l + spread)) / risk
                max_r = max(max_r, cur_r)

                # A. Breakeven at +0.80R
                if not be_active and max_r >= be_trigger_r:
                    current_sl = min(current_sl, entry - be_buffer)
                    be_active = True

                # B. Dynamic Trailing Stop (Let Profits Run!)
                if max_r >= trail_start_r:
                    trail_sl = (bar_l + spread) + trail_dist_r * risk
                    if trail_sl < current_sl:
                        current_sl = trail_sl
                        trail_active = True

                # Check if SL or Trailing SL touched
                if (bar_h + spread) >= current_sl:
                    if be_active or trail_active:
                        outcome = round((entry - current_sl) / risk, 3)
                    else:
                        outcome = -1.0
                    exit_i = j
                    break

        # Max hold exit
        if outcome is None and exit_i is None and (i + max_hold < len(m_exec)):
            final_bar = m_exec.iloc[min(i + max_hold, len(m_exec) - 1)]
            final_close = float(final_bar["close"])
            raw_pnl = (final_close - entry) if sig.side == "buy" else (entry - final_close - spread)
            pnl_r = round(raw_pnl / risk, 3)
            if be_active or trail_active:
                outcome = max(0.05, pnl_r)
            else:
                outcome = pnl_r
            exit_i = min(i + max_hold, len(m_exec) - 1)

        if outcome is not None:
            trades.append({
                "signal_time": t,
                "exit_time": m_exec.iloc[exit_i]["time"],
                "side": sig.side,
                "score": sig.score,
                "entry_with_cost": entry,
                "sl": sl,
                "tp": tp,
                "R": outcome,
                "max_r": round(max_r, 2),
                "trail_active": trail_active,
                "be_active": be_active,
                "reason": sig.reason,
            })
            # If loss, enforce a 30-minute cooling off period (6 bars)
            if outcome < 0:
                i = exit_i + 6
            else:
                i = exit_i + 1
        else:
            i += 1

    out = pd.DataFrame(trades)
    if out.empty:
        print("No completed trades found.")
        return

    wins = out[out["R"] > 0]["R"].sum()
    losses = abs(out[out["R"] < 0]["R"].sum())
    pf = wins / losses if losses else float("inf")
    eq = out["R"].cumsum()
    dd = eq - eq.cummax()

    print("\n=== Tyson Gold Scalper v2 — Multi-TF Backtest ===")
    print(f"Total Trades        : {len(out)}")
    print(f"Win Rate            : {(out['R'] > 0).mean()*100:.2f}%")
    print(f"Expectancy          : {out['R'].mean():.3f} R/trade")
    print(f"Profit Factor       : {pf:.3f}")
    print(f"Total Return        : {out['R'].sum():.2f} R")
    print(f"Max Drawdown        : {dd.min():.2f} R")
    print(f"Max Consecutive Loss: {max_consecutive_losses(out['R'])}")
    print(f"Max Profit Runner   : +{out['max_r'].max():.2f} R")
    print(f"Trailing SL Exits   : {out['trail_active'].sum()}")

    out.to_csv("backtest_trades.csv", index=False)
    print("Saved: backtest_trades.csv")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python backtest.py XAUUSD_M1.csv")
    backtest(sys.argv[1])
