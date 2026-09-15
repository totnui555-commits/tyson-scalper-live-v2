from __future__ import annotations

from datetime import datetime, timezone
import sys

import broker_mt5 as broker
from indicators import atr
from models import Signal
from mt5_data import get_rates
from news_filter import check_news_blackout, load_manual_events
from utils import file_exists, load_config


def ok(msg): print(f"[PASS] {msg}")
def warn(msg): print(f"[WARN] {msg}")
def fail(msg): print(f"[FAIL] {msg}")


def main():
    cfg = load_config()
    failures = 0
    try:
        version = broker.connect()
        ok(f"MT5 connected: {version}")
        a, t = broker.validate_account(cfg)
        ok(f"Account login={a.login} server={a.server} currency={a.currency}")

        if cfg["mode"] == "live":
            if not cfg.get("allow_live_trading", False):
                fail("mode=live but allow_live_trading=false"); failures += 1
            if not cfg.get("account", {}).get("allowed_logins"):
                fail("Live requires account.allowed_logins whitelist"); failures += 1
            if not cfg.get("account", {}).get("allowed_servers"):
                fail("Live requires account.allowed_servers whitelist"); failures += 1
            if cfg.get("require_live_arm_file", True) and not file_exists(cfg["live_arm_file"]):
                fail(f"Live arm file missing: {cfg['live_arm_file']}"); failures += 1
            if file_exists(cfg["kill_switch_file"]):
                fail(f"Kill switch is active: {cfg['kill_switch_file']}"); failures += 1
        else:
            warn("mode=paper; no real orders will be sent")

        info = broker.ensure_symbol(cfg["symbol"])
        ok(
            f"Symbol {cfg['symbol']} digits={info.digits} point={info.point} "
            f"volume={info.volume_min}/{info.volume_step}/{info.volume_max} stops_level={info.trade_stops_level}"
        )

        tick = broker.get_tick(cfg["symbol"])
        age = broker.tick_age_seconds(tick)
        spread = float(tick.ask - tick.bid)
        if age <= float(cfg["filters"]["max_tick_age_seconds"]):
            ok(f"Tick age {age:.2f}s")
        else:
            fail(f"Stale tick {age:.2f}s"); failures += 1
        if spread <= float(cfg["filters"]["max_spread_price"]):
            ok(f"Spread {spread:.3f}")
        else:
            fail(f"Spread too wide {spread:.3f}"); failures += 1

        ncfg = cfg["filters"]["news_filter"]
        if ncfg.get("enabled", False):
            try:
                events = load_manual_events(ncfg["csv_path"])
                ok(f"News blackout CSV readable ({len(events)} events loaded)")
                if len(events) == 0:
                    warn("News CSV contains no events; keep it updated before live XAUUSD scalping")
            except Exception as e:
                if ncfg.get("fail_closed_if_unavailable", True):
                    fail(f"News filter unavailable: {e}"); failures += 1
                else:
                    warn(f"News filter unavailable: {e}")
            blackout, msg = check_news_blackout(cfg)
            if blackout:
                warn(f"Currently blocked by news filter: {msg}")
            else:
                ok(f"News state: {msg}")

        exec_tf = cfg["strategy"].get("execution_timeframe", "M5")
        bias_tf = cfg["strategy"].get("bias_timeframe", "M15")
        m_exec = get_rates(cfg["symbol"], exec_tf, 200)
        m_bias = get_rates(cfg["symbol"], bias_tf, 200)
        exec_c = m_exec.iloc[:-1].copy()
        atr_series = atr(exec_c, cfg["strategy"]["atr_period"])
        atr_now = float(atr_series.iloc[-1])
        if atr_now > 0:
            ok(f"{exec_tf}/{bias_tf} data + ATR available ({atr_now:.3f})")
        else:
            fail("ATR invalid"); failures += 1

        # Non-trading order_check probe using a synthetic BUY request
        entry = float(tick.ask)
        min_dist = (int(info.trade_stops_level) + int(cfg["execution"]["stop_level_extra_points"])) * float(info.point)
        distance = max(
            atr_now * 1.5,
            min_dist * 2.0,
            float(cfg["filters"].get("min_sl_distance_price", 2.50)),
            float(info.point) * 50
        )
        sig = Signal(
            side="buy", score=99, bar_time=datetime.now(timezone.utc).isoformat(),
            entry=entry, stop_loss=entry - distance,
            take_profit=entry + cfg["strategy"]["rr"] * distance,
            atr=atr_now, reason="preflight-only"
        )
        risk_money = float(a.equity) * float(cfg["risk"]["risk_per_trade_pct"]) / 100.0
        trade = broker.prepare_trade(cfg["symbol"], sig, cfg, risk_money)
        ok(
            f"order_check probe passed: lot={trade.lot}, fill={trade.filling_name}, "
            f"two_step={trade.use_two_step}, risk~{trade.risk_money_actual:.2f} {a.currency}"
        )

    except Exception as e:
        fail(repr(e))
        failures += 1
    finally:
        broker.shutdown()

    print("\nPreflight result:", "READY" if failures == 0 else f"NOT READY ({failures} failure(s))")
    raise SystemExit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
