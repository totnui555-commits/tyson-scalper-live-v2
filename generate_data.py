import numpy as np
import pandas as pd

def generate_realistic_gold_data(filename="sample_gold_m1.csv", n_bars=1200):
    np.random.seed(42)
    start_time = pd.Timestamp("2026-09-10 00:00:00", tz="UTC")
    times = [start_time + pd.Timedelta(minutes=i) for i in range(n_bars)]

    # Gold price simulation around 2650 with trending and mean-reverting regimes
    base_price = 2650.0
    prices = [base_price]
    regime = 1.0  # 1 for bull, -1 for bear

    for i in range(1, n_bars):
        if i % 180 == 0:  # switch trend regime every 3 hours
            regime *= -1.0
        drift = regime * 0.08
        shock = np.random.normal(0, 0.45)
        # Occasional liquidity spikes
        if np.random.rand() < 0.03:
            shock += np.random.choice([-1.5, 1.5])
        next_p = max(2500.0, prices[-1] + drift + shock)
        prices.append(next_p)

    records = []
    for i in range(n_bars):
        p = prices[i]
        bar_open = p + np.random.normal(0, 0.15)
        bar_close = prices[min(i + 1, n_bars - 1)]
        high_extra = abs(np.random.normal(0.4, 0.3))
        low_extra = abs(np.random.normal(0.4, 0.3))
        bar_high = max(bar_open, bar_close) + high_extra
        bar_low = min(bar_open, bar_close) - low_extra
        vol = int(np.random.uniform(150, 1200))
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
    print(f"Generated {n_bars} bars to {filename}")

if __name__ == "__main__":
    generate_realistic_gold_data()
