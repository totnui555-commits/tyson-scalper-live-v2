from __future__ import annotations

from datetime import datetime, timezone
import math
import time

from models import PreparedTrade
from utils import make_signal_id

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


class BrokerError(RuntimeError):
    pass


def connect():
    if mt5 is None:
        raise BrokerError("MetaTrader5 package not installed")
    if not mt5.initialize():
        raise BrokerError(f"mt5.initialize failed: {mt5.last_error()}")
    return mt5.version()


def ensure_connected():
    """Checks MT5 connection and re-initializes if lost."""
    if mt5 is None:
        raise BrokerError("MetaTrader5 package not installed")
    term = mt5.terminal_info()
    if term is None or not getattr(term, "connected", False):
        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(1)
        if not mt5.initialize():
            raise BrokerError(f"mt5 reconnect failed: {mt5.last_error()}")


def shutdown():
    if mt5 is not None:
        try:
            mt5.shutdown()
        except Exception:
            pass


def validate_account(cfg: dict):
    ensure_connected()
    a = mt5.account_info()
    if a is None:
        raise BrokerError(f"Cannot read account_info: {mt5.last_error()}")
    login = int(a.login)
    server = str(a.server)

    allowed_logins = [int(x) for x in cfg["account"].get("allowed_logins", [])]
    allowed_servers = [str(x) for x in cfg["account"].get("allowed_servers", [])]

    if allowed_logins and login not in allowed_logins:
        raise BrokerError(f"Account login {login} not in allowed_logins: {allowed_logins}")
    if allowed_servers and server not in allowed_servers:
        raise BrokerError(f"Account server '{server}' not in allowed_servers: {allowed_servers}")
    if cfg["account"].get("require_trade_allowed", True) and not bool(a.trade_allowed):
        raise BrokerError("Account trading is disabled in terminal (trade_allowed=False)")

    return a, f"{login}@{server}"


def ensure_symbol(symbol: str):
    ensure_connected()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise BrokerError(f"Unknown symbol: {symbol}")
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            raise BrokerError(f"Cannot select symbol {symbol}: {mt5.last_error()}")
        info = mt5.symbol_info(symbol)
        if info is None:
            raise BrokerError(f"Symbol {symbol} disappeared after select: {mt5.last_error()}")
    return info


def account_info():
    ensure_connected()
    a = mt5.account_info()
    if a is None:
        raise BrokerError(f"Cannot read account_info: {mt5.last_error()}")
    return a


def get_tick(symbol: str):
    ensure_connected()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise BrokerError(f"No tick for {symbol}: {mt5.last_error()}")
    return tick


def tick_age_seconds(tick) -> float:
    ts = getattr(tick, "time_msc", None)
    if ts:
        tick_dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
    else:
        tick_dt = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - tick_dt).total_seconds())


def positions_for_symbol(symbol: str):
    ensure_connected()
    p = mt5.positions_get(symbol=symbol)
    return list(p) if p else []


def _floor_volume(raw: float, step: float, min_volume: float, max_volume: float):
    if raw < min_volume - 1e-12:
        return 0.0
    units = math.floor((raw + 1e-12) / step)
    vol = units * step
    vol = min(vol, max_volume)
    decimals = max(0, len((f"{step:.10f}").rstrip("0").split(".")[-1]))
    return round(vol, decimals)


def _normalize_price(price: float, digits: int) -> float:
    return round(float(price), int(digits))


def _calc_loss(symbol: str, side: str, volume: float, entry: float, sl: float):
    order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
    pnl = mt5.order_calc_profit(order_type, symbol, volume, entry, sl)
    if pnl is None:
        raise BrokerError(f"order_calc_profit failed: {mt5.last_error()}")
    return abs(float(pnl))


def _calc_margin(symbol: str, side: str, volume: float, entry: float):
    order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
    margin = mt5.order_calc_margin(order_type, symbol, volume, entry)
    if margin is None:
        raise BrokerError(f"order_calc_margin failed: {mt5.last_error()}")
    return float(margin)


def _filling_constant(name: str):
    mapping = {
        "FOK": mt5.ORDER_FILLING_FOK,
        "IOC": mt5.ORDER_FILLING_IOC,
        "RETURN": mt5.ORDER_FILLING_RETURN,
    }
    return mapping[name]


def _calc_deviation_points(symbol_info, cfg: dict) -> int:
    point = float(symbol_info.point)
    if "max_slippage_price" in cfg["execution"]:
        max_slip = float(cfg["execution"]["max_slippage_price"])
        return max(10, int(round(max_slip / point)))
    return int(cfg["execution"].get("deviation_points", 30))


def _base_request(symbol: str, side: str, lot: float, price: float, sl: float, tp: float, cfg: dict, filling_mode: int, deviation: int = 30):
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "magic": int(cfg["execution"]["magic"]),
        "comment": str(cfg["execution"]["comment"])[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }


def prepare_trade(symbol: str, signal, cfg: dict, risk_money: float) -> PreparedTrade:
    info = ensure_symbol(symbol)
    tick = get_tick(symbol)
    spread = float(tick.ask - tick.bid)
    actual_entry = float(tick.ask if signal.side == "buy" else tick.bid)

    max_tick_age = float(cfg["filters"]["max_tick_age_seconds"])
    age = tick_age_seconds(tick)
    if age > max_tick_age:
        raise BrokerError(f"Stale tick: {age:.1f}s > {max_tick_age:.1f}s")

    if spread > float(cfg["filters"]["max_spread_price"]):
        raise BrokerError(f"Spread too wide: {spread:.5f} > max {cfg['filters']['max_spread_price']}")

    if signal.atr and spread / float(signal.atr) > float(cfg["filters"]["max_spread_atr_ratio"]):
        raise BrokerError(f"Spread/ATR too high: {spread/float(signal.atr):.3f}")

    if abs(actual_entry - float(signal.entry)) > float(cfg["filters"]["max_entry_drift_price"]):
        raise BrokerError(
            f"Entry drift too large: market={actual_entry:.5f}, signal={signal.entry:.5f}"
        )

    digits = int(info.digits)
    point = float(info.point)
    deviation = _calc_deviation_points(info, cfg)
    min_stop_distance = (int(getattr(info, "trade_stops_level", 0)) + int(cfg["execution"]["stop_level_extra_points"])) * point

    sl = float(signal.stop_loss)
    if signal.side == "buy":
        if sl >= actual_entry - min_stop_distance:
            raise BrokerError("Buy stop is too close/invalid for broker stop level")
        risk_distance = actual_entry - sl
        tp = actual_entry + float(cfg["strategy"]["rr"]) * risk_distance
    else:
        if sl <= actual_entry + min_stop_distance:
            raise BrokerError("Sell stop is too close/invalid for broker stop level")
        risk_distance = sl - actual_entry
        tp = actual_entry - float(cfg["strategy"]["rr"]) * risk_distance

    # Spread-to-Risk Drag Guard (Crucial for Gold Scalping)
    max_spread_risk_ratio = float(cfg["filters"].get("max_spread_to_sl_ratio", 0.20))
    if risk_distance > 0 and (spread / risk_distance) > max_spread_risk_ratio:
        raise BrokerError(
            f"Spread drag too high: spread {spread:.3f} is {(spread/risk_distance)*100:.1f}% of SL distance {risk_distance:.3f} (max allowed: {max_spread_risk_ratio*100:.1f}%)"
        )

    sl = _normalize_price(sl, digits)
    tp = _normalize_price(tp, digits)
    actual_entry = _normalize_price(actual_entry, digits)

    one_lot_loss = _calc_loss(symbol, signal.side, 1.0, actual_entry, sl)
    if one_lot_loss <= 0:
        raise BrokerError("Calculated one-lot risk is zero")
    raw_lot = float(risk_money) / one_lot_loss
    lot = _floor_volume(raw_lot, float(info.volume_step), float(info.volume_min), float(info.volume_max))
    if lot <= 0:
        raise BrokerError(
            f"Risk budget too small for broker minimum volume {info.volume_min}; raw lot={raw_lot:.6f}"
        )

    actual_risk = _calc_loss(symbol, signal.side, lot, actual_entry, sl)
    allowed_overrun = float(cfg["risk"]["max_risk_overrun_pct"]) / 100.0
    if actual_risk > risk_money * (1 + allowed_overrun):
        raise BrokerError(f"Risk overrun: actual={actual_risk:.2f}, target={risk_money:.2f}")

    a = account_info()
    margin = _calc_margin(symbol, signal.side, lot, actual_entry)
    max_margin = float(a.margin_free) * float(cfg["risk"]["max_margin_usage_pct_of_free_margin"]) / 100.0
    if margin > max_margin:
        raise BrokerError(f"Margin guard: required={margin:.2f}, max_allowed={max_margin:.2f}")

    signal_id = make_signal_id(symbol, signal.bar_time, signal.side)

    # Detect Market Execution requirement
    is_market_exec = (getattr(info, "trade_execution", None) == getattr(mt5, "TRADE_EXECUTION_MARKET", 2))
    use_two_step = is_market_exec

    last_error = None
    for filling_name in cfg["execution"]["prefer_filling_modes"]:
        filling_mode = _filling_constant(filling_name)
        # Try direct with SL/TP first unless forced to two-step
        check_sl = 0.0 if use_two_step else sl
        check_tp = 0.0 if use_two_step else tp
        req = _base_request(symbol, signal.side, lot, actual_entry, check_sl, check_tp, cfg, filling_mode, deviation)
        check = mt5.order_check(req)

        # If direct failed with 10016 (invalid stops), fallback to two-step probing
        if check is not None and int(check.retcode) == 10016 and not use_two_step:
            use_two_step = True
            req_no_stops = _base_request(symbol, signal.side, lot, actual_entry, 0.0, 0.0, cfg, filling_mode, deviation)
            check = mt5.order_check(req_no_stops)

        if check is not None and int(check.retcode) == 0:
            return PreparedTrade(
                symbol=symbol, side=signal.side, signal_id=signal_id,
                requested_price=actual_entry, stop_loss=sl, take_profit=tp,
                lot=lot, risk_money_target=float(risk_money), risk_money_actual=actual_risk,
                spread=spread, filling_name=filling_name, filling_mode=filling_mode,
                use_two_step=use_two_step,
            )
        last_error = None if check is None else f"{check.retcode}:{check.comment}"

    raise BrokerError(f"No filling mode passed order_check; last={last_error}; mt5={mt5.last_error()}")


def send_prepared_trade(trade: PreparedTrade, cfg: dict):
    if cfg.get("mode") != "live" or not cfg.get("allow_live_trading", False):
        raise BrokerError("Live trading is not armed in config")

    info = ensure_symbol(trade.symbol)
    tick = get_tick(trade.symbol)
    age = tick_age_seconds(tick)
    if age > float(cfg["filters"]["max_tick_age_seconds"]):
        raise BrokerError(f"Stale final tick: {age:.1f}s")

    current_price = float(tick.ask if trade.side == "buy" else tick.bid)
    if abs(current_price - trade.requested_price) > float(cfg["filters"]["max_send_drift_price"]):
        raise BrokerError(
            f"Price moved before send: prepared={trade.requested_price:.5f}, now={current_price:.5f}"
        )

    digits = int(info.digits)
    point = float(info.point)
    deviation = _calc_deviation_points(info, cfg)
    min_stop_distance = (int(getattr(info, "trade_stops_level", 0)) + int(cfg["execution"]["stop_level_extra_points"])) * point

    if trade.side == "buy":
        if trade.stop_loss >= current_price - min_stop_distance:
            raise BrokerError("Final buy stop no longer valid")
        tp = current_price + float(cfg["strategy"]["rr"]) * (current_price - trade.stop_loss)
    else:
        if trade.stop_loss <= current_price + min_stop_distance:
            raise BrokerError("Final sell stop no longer valid")
        tp = current_price - float(cfg["strategy"]["rr"]) * (trade.stop_loss - current_price)

    current_price = _normalize_price(current_price, digits)
    tp = _normalize_price(tp, digits)
    current_risk = _calc_loss(trade.symbol, trade.side, trade.lot, current_price, trade.stop_loss)
    allowed_overrun = float(cfg["risk"]["max_risk_overrun_pct"]) / 100.0
    if current_risk > trade.risk_money_target * (1 + allowed_overrun):
        raise BrokerError(
            f"Final risk overrun: {current_risk:.2f} > target {trade.risk_money_target:.2f}"
        )

    # If Two-step execution is required, send deal without SL/TP first
    req_sl = 0.0 if trade.use_two_step else trade.stop_loss
    req_tp = 0.0 if trade.use_two_step else tp

    req = _base_request(
        trade.symbol, trade.side, trade.lot, current_price,
        req_sl, req_tp, cfg, trade.filling_mode, deviation,
    )

    if cfg["execution"].get("order_check_required", True):
        check = mt5.order_check(req)
        if check is None or int(check.retcode) != 0:
            raise BrokerError(f"Final order_check failed: {check} / {mt5.last_error()}")

    result = mt5.order_send(req)
    if result is None:
        raise BrokerError(f"order_send returned None: {mt5.last_error()}")

    # Handle retcode 10016 (Invalid Stops) dynamically if not caught during prepare
    if int(result.retcode) == 10016 and not trade.use_two_step:
        print("Market Execution detected (10016). Retrying with Two-Step Execution...")
        req["sl"] = 0.0
        req["tp"] = 0.0
        result = mt5.order_send(req)
        trade.use_two_step = True

    done = {int(mt5.TRADE_RETCODE_DONE)}
    if hasattr(mt5, "TRADE_RETCODE_DONE_PARTIAL"):
        done.add(int(mt5.TRADE_RETCODE_DONE_PARTIAL))

    if int(result.retcode) not in done:
        raise BrokerError(f"order_send rejected: retcode={result.retcode}, comment={result.comment}")

    # If Two-Step, immediately modify the position with SL and TP
    if trade.use_two_step:
        time.sleep(0.15)  # brief pause for position to register in terminal
        pos_ticket = int(result.order) if result.order else 0
        if not pos_ticket:
            positions = positions_for_symbol(trade.symbol)
            if positions:
                pos_ticket = positions[-1].ticket

        if pos_ticket:
            try:
                modify_position_sltp(trade.symbol, pos_ticket, trade.stop_loss, tp, cfg)
                print(f"Two-step SL/TP attached to ticket {pos_ticket}: SL={trade.stop_loss}, TP={tp}")
            except Exception as e:
                print(f"CRITICAL WARNING: Failed to attach SL/TP to ticket {pos_ticket}: {e}")

    return result


def modify_position_sltp(symbol: str, position_ticket: int, sl: float, tp: float, cfg: dict):
    """Sets or updates Stop Loss and Take Profit on an existing open position."""
    ensure_connected()
    info = ensure_symbol(symbol)
    digits = int(info.digits)
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": int(position_ticket),
        "sl": _normalize_price(sl, digits),
        "tp": _normalize_price(tp, digits),
        "magic": int(cfg["execution"]["magic"]),
    }
    res = mt5.order_send(req)
    if res is None:
        raise BrokerError(f"modify_position_sltp returned None: {mt5.last_error()}")
    if int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
        raise BrokerError(f"modify_position_sltp failed: retcode={res.retcode}, comment={res.comment}")
    return res


def close_position(position, cfg: dict):
    """Closes an open position at current market price."""
    ensure_connected()
    symbol = position.symbol
    info = ensure_symbol(symbol)
    tick = get_tick(symbol)
    is_buy = (position.type == mt5.ORDER_TYPE_BUY)
    price = float(tick.bid if is_buy else tick.ask)
    deviation = _calc_deviation_points(info, cfg)
    filling_mode = getattr(position, "type_filling", mt5.ORDER_FILLING_IOC)
    if filling_mode not in (0, 1, 2):
        filling_mode = mt5.ORDER_FILLING_IOC

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "position": position.ticket,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": _normalize_price(price, int(info.digits)),
        "deviation": deviation,
        "magic": int(cfg["execution"]["magic"]),
        "comment": "Bot Auto-Close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    res = mt5.order_send(req)
    if res is None or int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
        err = getattr(res, "comment", mt5.last_error())
        raise BrokerError(f"close_position failed: {err}")
    return res
