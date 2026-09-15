from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

import broker_mt5 as broker
from mt5_data import get_rates
from news_filter import check_news_blackout
from risk import build_snapshot, allow_trade, risk_amount
from state_store import StateStore
from strategy import build_signal
from utils import file_exists, in_session, load_config, make_signal_id


def live_gate(cfg: dict):
    if cfg.get("mode") != "live":
        return True, "paper_mode"
    if not cfg.get("allow_live_trading", False):
        return False, "allow_live_trading=false"
    if not cfg.get("account", {}).get("allowed_logins"):
        return False, "account.allowed_logins must be set for live mode"
    if not cfg.get("account", {}).get("allowed_servers"):
        return False, "account.allowed_servers must be set for live mode"
    if file_exists(cfg["kill_switch_file"]):
        return False, "KILL_SWITCH present"
    if cfg.get("require_live_arm_file", True) and not file_exists(cfg["live_arm_file"]):
        return False, "LIVE_ARMED file missing"
    return True, "live_armed"


def cooldown_clear(store: StateStore, cfg: dict):
    last = store.last_success_time()
    if last is None:
        return True, "no_previous_trade"
    elapsed = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 60.0
    needed = float(cfg["filters"]["cooldown_minutes"])
    return (elapsed >= needed), f"cooldown_elapsed={elapsed:.1f}m"


def manage_active_positions(symbol: str, cfg: dict, store: StateStore):
    """
    Active position lifecycle monitor:
    - Breakeven Guard: moves SL to entry + buffer once +1.0R profit is achieved
    - Max Hold Bars Guard: closes stagnant positions past the maximum hold duration
    """
    if cfg.get("mode") != "live":
        return

    try:
        positions = broker.positions_for_symbol(symbol)
        magic = int(cfg["execution"]["magic"])
        bot_positions = [p for p in positions if int(getattr(p, "magic", 0)) == magic]
        if not bot_positions:
            return

        tick = broker.get_tick(symbol)
        cur_bid = float(tick.bid)
        cur_ask = float(tick.ask)

        rcfg = cfg.get("risk", {})
        be_trigger_r = float(rcfg.get("breakeven_trigger_r", 1.0))
        be_buffer = float(rcfg.get("breakeven_buffer_price", 0.15))
        max_hold_bars = int(cfg.get("backtest", {}).get("max_hold_bars", 30))

        tf_str = str(cfg["strategy"]["execution_timeframe"]).upper()
        tf_minutes = 5 if tf_str == "M5" else (15 if tf_str == "M15" else 1)
        max_hold_seconds = max_hold_bars * tf_minutes * 60

        for pos in bot_positions:
            is_buy = (pos.type == getattr(broker.mt5, "ORDER_TYPE_BUY", 0))
            open_price = float(pos.price_open)
            cur_sl = float(pos.sl)
            cur_price = cur_bid if is_buy else cur_ask

            # 1. Breakeven & Dynamic Trailing Stop (Let Profits Run!)
            if cur_sl > 0:
                initial_risk = abs(open_price - cur_sl)
                if initial_risk > 0:
                    current_profit = (cur_price - open_price) if is_buy else (open_price - cur_price)
                    current_r = current_profit / initial_risk

                    target_sl = None
                    trailing_enabled = bool(rcfg.get("trailing_stop_enabled", True))
                    trail_start_r = float(rcfg.get("trail_start_r", 1.20))
                    trail_dist_r = float(rcfg.get("trail_distance_r", 0.75))

                    # A. Dynamic Trailing Stop (Rides the trend without closing early)
                    if trailing_enabled and current_r >= trail_start_r:
                        target_sl = (cur_price - trail_dist_r * initial_risk) if is_buy else (cur_price + trail_dist_r * initial_risk)
                    # B. Breakeven Guard (Protects entry once 0.8R reached)
                    elif current_r >= be_trigger_r:
                        target_sl = (open_price + be_buffer) if is_buy else (open_price - be_buffer)

                    if target_sl is not None:
                        should_modify = (target_sl > cur_sl) if is_buy else (target_sl < cur_sl)
                        if should_modify:
                            try:
                                broker.modify_position_sltp(symbol, pos.ticket, target_sl, pos.tp, cfg)
                                mode_desc = "TRAILING STOP" if (trailing_enabled and current_r >= trail_start_r) else "BREAKEVEN"
                                print(
                                    f"{mode_desc} TRIGGERED: Ticket {pos.ticket} SL moved to {target_sl:.3f} "
                                    f"(+{current_r:.2f}R achieved)"
                                )
                            except Exception as e:
                                print(f"SL modify failed for {pos.ticket}: {e}")

            # 2. Max Hold Time Exit
            open_time = getattr(pos, "time", 0)
            if open_time > 0:
                elapsed_sec = (datetime.now(timezone.utc).timestamp() - open_time)
                if elapsed_sec >= max_hold_seconds:
                    try:
                        broker.close_position(pos, cfg)
                        print(f"MAX HOLD EXCEEDED: Closed position {pos.ticket} after {elapsed_sec/60:.1f} mins")
                    except Exception as e:
                        print(f"Max hold close failed for {pos.ticket}: {e}")

    except Exception as e:
        print(f"Position management error: {e}")


def run():
    cfg = load_config()
    symbol = cfg["symbol"]
    store = StateStore(cfg["state"]["sqlite_path"])

    version = broker.connect()
    print(f"MT5 connected: {version}")
    try:
        broker.validate_account(cfg)
        broker.ensure_symbol(symbol)
        print(f"Tyson Scalper Live v2 | {symbol} | mode={cfg['mode']}")

        last_seen_open_bar = None
        while True:
            try:
                # Keep MT5 connection alive and healthy
                broker.ensure_connected()

                # Always monitor and manage active open trades
                manage_active_positions(symbol, cfg, store)

                gate_ok, gate_msg = live_gate(cfg)
                if cfg.get("mode") == "live" and not gate_ok:
                    print(f"LIVE BLOCK: {gate_msg}")
                    time.sleep(2)
                    continue

                if not in_session(cfg):
                    time.sleep(2)
                    continue

                exec_tf = cfg["strategy"]["execution_timeframe"]
                bias_tf = cfg["strategy"]["bias_timeframe"]
                m_exec = get_rates(symbol, exec_tf, 450)
                m_bias = get_rates(symbol, bias_tf, 300)

                open_bar_time = m_exec.iloc[-1]["time"]
                if last_seen_open_bar == open_bar_time:
                    time.sleep(0.5)
                    continue
                last_seen_open_bar = open_bar_time

                # Closed bars only: eliminates lookahead bias
                exec_c = m_exec.iloc[:-1].copy()
                bias_c = m_bias.iloc[:-1].copy()
                sig = build_signal(exec_c, bias_c, cfg)
                if sig.side == "none":
                    print(datetime.now().isoformat(timespec="seconds"), sig.reason)
                    continue

                if sig.atr is None or float(sig.atr) < float(cfg["filters"]["min_atr_price"]):
                    print(f"BLOCK low ATR: {sig.atr}")
                    continue

                blackout, news_msg = check_news_blackout(cfg)
                if blackout:
                    print(f"BLOCK {news_msg}")
                    continue

                cool, cool_msg = cooldown_clear(store, cfg)
                if not cool:
                    print(f"BLOCK {cool_msg}")
                    continue

                account, _ = broker.validate_account(cfg)
                positions = broker.positions_for_symbol(symbol)
                snapshot = build_snapshot(store, cfg, symbol, account, len(positions))
                ok, why = allow_trade(snapshot, cfg)
                if not ok:
                    print(f"RISK BLOCK: {why}")
                    continue

                signal_id = make_signal_id(symbol, sig.bar_time, sig.side)
                if not store.reserve_signal(signal_id, symbol, sig.side, sig.bar_time):
                    print(f"DUPLICATE BLOCK: {signal_id}")
                    continue

                try:
                    risk_money = risk_amount(float(account.equity), cfg)
                    trade = broker.prepare_trade(symbol, sig, cfg, risk_money)

                    print(
                        f"{sig.side.upper()} score={sig.score} lot={trade.lot} "
                        f"entry={trade.requested_price} sl={trade.stop_loss} tp={trade.take_profit} "
                        f"risk={trade.risk_money_actual:.2f}/{trade.risk_money_target:.2f} "
                        f"spread={trade.spread:.3f} fill={trade.filling_name} "
                        f"two_step={trade.use_two_step} | {sig.reason}"
                    )

                    if cfg["mode"] == "paper":
                        store.update_attempt(signal_id, "PAPER", message=sig.reason)
                        continue

                    # Recheck arming immediately before the irreversible action
                    gate_ok, gate_msg = live_gate(cfg)
                    if not gate_ok:
                        raise RuntimeError(f"Live gate changed before send: {gate_msg}")

                    result = broker.send_prepared_trade(trade, cfg)
                    partial_code = getattr(broker.mt5, "TRADE_RETCODE_DONE_PARTIAL", -999)
                    status = "LIVE_PARTIAL" if int(result.retcode) == int(partial_code) else "LIVE_DONE"
                    store.update_attempt(
                        signal_id, status,
                        order_ticket=int(getattr(result, "order", 0) or 0),
                        deal_ticket=int(getattr(result, "deal", 0) or 0),
                        message=str(getattr(result, "comment", "")),
                    )
                    print(f"ORDER {status}: order={result.order} deal={result.deal} retcode={result.retcode}")

                except Exception as e:
                    store.update_attempt(signal_id, "FAILED", message=repr(e))
                    print("ATTEMPT FAILED:", repr(e))

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print("LOOP ERROR:", repr(e))
                time.sleep(2)

    finally:
        broker.shutdown()


if __name__ == "__main__":
    run()
