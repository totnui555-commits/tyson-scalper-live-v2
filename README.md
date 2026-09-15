# Tyson Scalper Agent — Gold Scalper v2 (Production Grade)

This repository is a sanitized source snapshot. Runtime `config.yaml`, broker
account identifiers, generated backtest data, and live-arm files are intentionally
kept out of version control. Copy `config.example.yaml` to `config.yaml` and set
account/server allowlists locally before running any paper-mode checks. Keep
`allow_live_trading: false` until an independent operational review is complete.

Professional XAUUSD (Gold) automated scalping bot for MetaTrader 5 with quantitative edge, multi-tier risk controls, two-step execution, and active trade lifecycle management.

---

## 🌟 Key Upgrades & Architecture

1. **Multi-Timeframe Confluence (M15 Bias / M5 Execution)**:
   - Eliminates M1 Spread Drag where spreads previously consumed up to 80% of Stop Loss.
   - Stop Loss is comfortably positioned ($3.00 - $6.00) with a guaranteed **`min_sl_distance_price` floor ($2.50)**.
   - **`max_spread_to_sl_ratio` Guard**: Blocks any trade if broker spread exceeds 20% of the stop loss distance.

2. **Genuine SMC / ICT Strategy**:
   - **Hard Gate HTF Trend**: Strictly blocks counter-trend entries against higher timeframe structure.
   - **Liquidity Sweeps**: Detects true wick grabs of fractal Swing Highs / Swing Lows.
   - **FVG Mitigation**: Verifies that price actively retests and rejects from an unmitigated Fair Value Gap.
   - **Momentum Confluence**: Confirmed via EMA alignment, Wilder's RSI momentum window, and body displacement.

3. **Execution Engine Hardening**:
   - **Two-Step Market Execution**: Automatically detects and adapts to ECN/STP brokers with Market Execution (`TRADE_EXECUTION_MARKET` or error 10016), executing orders cleanly and attaching SL/TP immediately after fill.
   - **Dynamic Point Deviation**: Converts dollar slippage (e.g. $0.50) into points dynamically according to symbol digits (2 or 3 decimals).
   - **Auto-Reconnect Loop**: Recovers automatically from terminal restarts or network hiccups.

4. **Active Trade Lifecycle**:
   - **Breakeven Protection**: Automatically moves SL to Entry + Spread buffer once price achieves **+1.0R profit**.
   - **Max Hold Duration Exit**: Closes stagnant positions that linger beyond max hold bars (default 30 bars on M5).

---

## 🛡️ Risk Management & Circuit Breakers

- **Risk per trade**: Default `0.25%` of account equity.
- **Daily loss limit**: `1.25%` max daily equity drawdown (hard shutoff).
- **Consecutive loss lock**: Pauses trading after 3 straight losses.
- **Max open positions**: Exactly 1 position at any time.
- **Max trades per day**: Capped at 8 trades.
- **Fail-closed Gate**: Requires matching account login/server whitelist, `LIVE_ARMED` file, and absence of `KILL_SWITCH`.
- **SQLite Persistence**: State store with SHA-256 deduplication prevents double executions on the same bar.

---

## 🚀 Quickstart & Operational Commands

### 1. Environment & Tests
Activate virtual environment:
```powershell
.\.venv\Scripts\Activate.ps1
```

Run test suite and smoke test:
```powershell
python -m unittest discover -s tests -p "test_*.py"
python smoke_test.py
```

### 2. Preflight Check (Verify MT5 Connection)
Ensure MetaTrader 5 is open, logged in, and Algo Trading is enabled:
```powershell
python preflight.py
```

### 3. Running Paper Mode (Simulated Execution)
```powershell
python main.py
```

### 4. Backtest Broker History
```powershell
python backtest.py XAUUSD_M1.csv
```

### 5. Emergency Stop
To immediately freeze new entries:
```powershell
python emergency_stop.py
```
*(Or simply create a file named `KILL_SWITCH` in the project root)*
