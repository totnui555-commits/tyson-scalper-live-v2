from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from indicators import atr, rsi, ema, bullish_engulfing, bearish_engulfing
from structure import find_swings, market_bias, liquidity_sweep
from fvg import find_all_fvgs, check_fvg_mitigation
from strategy import build_signal
from utils import load_config, make_signal_id


class TestScalperCore(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_indicators_math(self):
        prices = [100.0 + i for i in range(30)]
        df = pd.DataFrame({
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": [p + 0.5 for p in prices],
        })
        atr_series = atr(df, period=14)
        self.assertEqual(len(atr_series), len(df))
        self.assertFalse(atr_series.iloc[-1] is None or np.isnan(atr_series.iloc[-1]))
        self.assertGreater(float(atr_series.iloc[-1]), 0.0)

        rsi_series = rsi(df, period=14)
        self.assertTrue(0 <= float(rsi_series.iloc[-1]) <= 100)

        ema_series = ema(df["close"], period=20)
        self.assertEqual(len(ema_series), len(df))

    def test_engulfing_detection(self):
        # Bullish Engulfing
        df_bull = pd.DataFrame({
            "open": [105.0, 101.0],
            "high": [106.0, 107.0],
            "low": [100.0, 100.5],
            "close": [101.0, 106.5],
        })
        self.assertTrue(bullish_engulfing(df_bull))
        self.assertFalse(bearish_engulfing(df_bull))

        # Bearish Engulfing
        df_bear = pd.DataFrame({
            "open": [101.0, 107.0],
            "high": [106.0, 107.5],
            "low": [100.5, 100.0],
            "close": [105.5, 100.5],
        })
        self.assertTrue(bearish_engulfing(df_bear))
        self.assertFalse(bullish_engulfing(df_bear))

    def test_market_structure_fractals(self):
        # Create a clear peak and valley pattern
        highs = [10, 12, 15, 13, 11, 14, 18, 16, 12]
        lows = [8, 10, 13, 11, 9, 12, 15, 13, 10]
        df = pd.DataFrame({
            "high": highs,
            "low": lows,
            "close": highs,
        })
        sw_highs, sw_lows = find_swings(df, left=1, right=1)
        self.assertGreater(len(sw_highs), 0)
        self.assertGreater(len(sw_lows), 0)

    def test_fvg_detection_and_mitigation(self):
        # Bar 0: Low 100, High 102
        # Bar 1: Strong impulse: Low 102, High 110
        # Bar 2: Low 105, High 112 -> Gap between Bar 0 High (102) and Bar 2 Low (105)
        # Bar 3: Pullback to 103 (inside gap 102-105) and closes at 104 -> Mitigation!
        df = pd.DataFrame({
            "open": [100.5, 102.0, 106.0, 105.0],
            "high": [102.0, 110.0, 112.0, 106.0],
            "low": [100.0, 102.0, 105.0, 103.0],
            "close": [101.5, 109.0, 111.0, 104.5],
        })
        bulls, bears = find_all_fvgs(df.iloc[:3])
        self.assertEqual(len(bulls), 1)
        self.assertEqual(bulls[0]["bottom"], 102.0)
        self.assertEqual(bulls[0]["top"], 105.0)

        mit = check_fvg_mitigation(df)
        self.assertTrue(mit["bullish_mitigation"])
        self.assertEqual(mit["fvg_zone"], (102.0, 105.0))

    def test_hard_gate_bias_protection(self):
        # If HTF bias is Bearish, buy signals MUST be blocked 100%
        t_bias = pd.date_range("2026-01-01T00:00:00Z", periods=40, freq="15min")
        r_bias = []
        base_b = 2700.0
        # Strong Downtrend
        for i, t in enumerate(t_bias):
            o = base_b - i * 2.0
            c = o - 1.5
            r_bias.append([t, o, o + 0.5, c - 0.5, c, 500])
        m_bias = pd.DataFrame(r_bias, columns=["time", "open", "high", "low", "close", "tick_volume"])
        bias = market_bias(m_bias, 20)
        self.assertEqual(bias, "bearish")

        # Fake execution data attempting to buy
        t_exec = pd.date_range("2026-01-01T06:00:00Z", periods=40, freq="5min")
        r_exec = []
        for i, t in enumerate(t_exec):
            r_exec.append([t, 2600.0, 2602.0, 2598.0, 2601.0, 200])
        m_exec = pd.DataFrame(r_exec, columns=["time", "open", "high", "low", "close", "tick_volume"])

        sig = build_signal(m_exec, m_bias, self.cfg)
        self.assertNotEqual(sig.side, "buy", "Hard gate failed to block buy during bearish bias!")

    def test_signal_id_hashing(self):
        h1 = make_signal_id("XAUUSD", "2026-01-01T12:00:00Z", "buy")
        h2 = make_signal_id("XAUUSD", "2026-01-01T12:00:00Z", "buy")
        h3 = make_signal_id("XAUUSD", "2026-01-01T12:00:00Z", "sell")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)


if __name__ == "__main__":
    unittest.main()
