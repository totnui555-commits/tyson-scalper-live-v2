from __future__ import annotations

import os
import tempfile
import pandas as pd

from state_store import StateStore
from strategy import build_signal
from structure import market_bias, liquidity_sweep
from utils import load_config


def generate_synthetic_data():
    # 1. Bias timeframe (M15): Consistent Higher Highs and Higher Lows
    t_bias = pd.date_range("2026-01-01T00:00:00Z", periods=40, freq="15min")
    r_bias = []
    base_b = 2600.0
    for i, t in enumerate(t_bias):
        o = base_b + i * 1.5
        c = o + 1.2
        r_bias.append([t, o, c + 0.8, o - 0.5, c, 500 + i * 10])
    m_bias = pd.DataFrame(r_bias, columns=["time", "open", "high", "low", "close", "tick_volume"])

    # 2. Execution timeframe (M5): Steady trend with a clear liquidity sweep & bullish engulfing
    t_exec = pd.date_range("2026-01-01T06:00:00Z", periods=45, freq="5min")
    r_exec = []
    base_e = 2640.0
    for i, t in enumerate(t_exec[:-2]):
        o = base_e + i * 0.35
        c = o + 0.25
        r_exec.append([t, o, c + 0.4, o - 0.3, c, 200 + i * 5])

    # Find prior swing low in window
    prior_key_low = min(x[3] for x in r_exec[-20:])

    # Bar -2: Red candle pulling down near key low
    p_prev = r_exec[-1][4]
    r_exec.append([t_exec[-2], p_prev, p_prev + 0.5, prior_key_low + 0.1, prior_key_low + 0.2, 400])

    # Bar -1: Sweeps below prior_key_low by 0.50, then closes high engulfing Bar -2 with strong displacement
    sweep_low = prior_key_low - 0.50
    close_price = prior_key_low + 2.50
    r_exec.append([t_exec[-1], prior_key_low + 0.15, close_price + 0.40, sweep_low, close_price, 1200])

    m_exec = pd.DataFrame(r_exec, columns=["time", "open", "high", "low", "close", "tick_volume"])
    return m_exec, m_bias


def main():
    cfg = load_config()
    m_exec, m_bias = generate_synthetic_data()

    # 1. Test Market Structure Bias
    bias = market_bias(m_bias, cfg["strategy"]["swing_lookback"])
    assert bias == "bullish", f"Expected bullish bias, got {bias}"

    # 2. Test Liquidity Sweep
    sw = liquidity_sweep(m_exec, cfg["strategy"]["liquidity_lookback"])
    assert sw["bullish"] is True, f"Expected bullish sweep, got {sw}"

    # 3. Test Signal Generation
    sig = build_signal(m_exec, m_bias, cfg)
    assert sig.side == "buy", f"Expected buy signal, got: {sig}"
    assert sig.score >= cfg["strategy"]["score_threshold"], f"Score {sig.score} below threshold: {sig.reason}"
    assert sig.stop_loss < sig.entry < sig.take_profit, f"Invalid levels: SL={sig.stop_loss}, Entry={sig.entry}, TP={sig.take_profit}"

    # 4. Verify Gold-Safe SL Floor
    min_sl_dist = float(cfg["filters"]["min_sl_distance_price"])
    risk_dist = float(sig.entry) - float(sig.stop_loss)
    assert risk_dist >= min_sl_dist, f"Risk distance {risk_dist:.2f} violates min_sl floor {min_sl_dist}"

    # 5. Test State Store & Deduplication
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "state.sqlite3")
        s = StateStore(db)
        assert s.reserve_signal("sig_gold_01", "XAUUSD", "buy", "2026-01-01T00:00:00Z") is True
        assert s.reserve_signal("sig_gold_01", "XAUUSD", "buy", "2026-01-01T00:00:00Z") is False
        s.update_attempt("sig_gold_01", "PAPER", message="ok")
        assert s.last_success_time() is not None

    print("\n[PASS] SMOKE TEST PASSED: Structure, Sweeps, Signals, SL Floor, and SQLite Storage verified.")


if __name__ == "__main__":
    main()
