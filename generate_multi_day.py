import numpy as np
import pandas as pd

def generate_multi_day_gold_data(filename="gold_multi_day.csv", n_bars=7500):
    np.random.seed(101)
    start_time = pd.Timestamp("2026-09-07 00:00:00", tz="UTC") # Monday start
    times = [start_time + pd.Timedelta(minutes=i) for i in range(n_bars)]

    # Gold price simulation with multi-day macro regimes (trending up, down, pullbacks, ranges)
    base_price = 2650.0
    prices = [base_price]
    regimes = [1.0, 1.0, -1.0, -1.0, 0.0, 1.0, -1.0, 1.0] # varying market cycles

    for i in range(1, n_bars):
        regime_idx = (i // 600) % len(regimes)
        regime = regimes[regime_idx]

        drift = regime * 0.07
        shock = np.random.normal(0, 0.40)
        # Volatility liquidity sweeps
        if np.random.rand() < 0.035:
            shock += np.random.choice([-1.8, 1.8])
        next_p = max(2400.0, prices[-1] + drift + shock)
        prices.append(next_p)

    records = []
    for i in range(n_bars):
        p = prices[i]
        bar_open = p + np.random.normal(0, 0.12)
        bar_close = prices[min(i + 1, n_bars - 1)]
        high_extra = abs(np.random.normal(0.45, 0.30))
        low_extra = abs(np.random.normal(0.45, 0.30))
        bar_high = max(bar_open, bar_close) + high_extra
        bar_low = min(bar_open, bar_close) - low_extra
        vol = int(np.random.uniform(200, 1500))
        records.append({
            "time": times[i].isoformat(),
            "open": round(bar_open, 3),
            "high": round(bar_high, 3),
            "low": round(bar_low, 3),
            "close": round(bar_close, 3),
            "tick_volume": vol,
        })

    df = pd.DataFrame(records)
    df.to_csv(filename, index=False)
    print(f"Generated {n_bars} multi-day bars to {filename}")

if __name__ == "__main__":
    generate_multi_day_gold_data()
