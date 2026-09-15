from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from utils import local_day_bounds_utc

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


@dataclass
class RiskSnapshot:
    start_equity: float
    current_equity: float
    daily_drawdown_pct: float
    trades_today: int
    consecutive_losses: int
    open_symbol_positions: int


def risk_amount(equity: float, cfg: dict) -> float:
    return float(equity) * float(cfg["risk"]["risk_per_trade_pct"]) / 100.0


def _deal_net(d) -> float:
    return sum(float(getattr(d, x, 0.0) or 0.0) for x in ("profit", "commission", "swap", "fee"))


def _filtered_deals(date_from, date_to, symbol: str, magic: int):
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []
    return [d for d in deals if str(getattr(d, "symbol", "")) == symbol and int(getattr(d, "magic", 0)) == int(magic)]


def daily_history_stats(cfg: dict, symbol: str):
    start, now, day_key = local_day_bounds_utc(cfg["risk"]["risk_day_timezone"])
    deals = _filtered_deals(start, now, symbol, cfg["execution"]["magic"])
    entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
    entry_inout = getattr(mt5, "DEAL_ENTRY_INOUT", 2)
    trades = {
        int(getattr(d, "position_id", 0))
        for d in deals
        if int(getattr(d, "entry", -1)) in (entry_in, entry_inout) and int(getattr(d, "position_id", 0)) != 0
    }
    realized = sum(_deal_net(d) for d in deals)
    return len(trades), realized, day_key


def consecutive_losses(cfg: dict, symbol: str, lookback_days: int = 45) -> int:
    now = datetime.now(timezone.utc)
    deals = _filtered_deals(now - timedelta(days=lookback_days), now, symbol, cfg["execution"]["magic"])
    out_types = {
        getattr(mt5, "DEAL_ENTRY_OUT", 1),
        getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
        getattr(mt5, "DEAL_ENTRY_INOUT", 2),
    }
    by_pos = defaultdict(lambda: {"net": 0.0, "closed": False, "close_time": 0})
    for d in deals:
        pid = int(getattr(d, "position_id", 0))
        if not pid:
            continue
        by_pos[pid]["net"] += _deal_net(d)
        if int(getattr(d, "entry", -1)) in out_types:
            by_pos[pid]["closed"] = True
            by_pos[pid]["close_time"] = max(by_pos[pid]["close_time"], int(getattr(d, "time_msc", 0) or getattr(d, "time", 0) * 1000))
    closed = [v for v in by_pos.values() if v["closed"]]
    closed.sort(key=lambda x: x["close_time"], reverse=True)
    n = 0
    for x in closed:
        if x["net"] < 0:
            n += 1
        elif x["net"] > 0:
            break
    return n


def get_or_create_day_start_equity(store, cfg: dict, account, realized_today: float, day_key: str) -> float:
    key = f"risk:{day_key}:start_equity"
    existing = store.get_float(key)
    if existing is not None:
        return existing
    # Reconstruct a reasonable baseline if bot starts after some trades have already closed.
    reconstructed = float(account.balance) - float(realized_today)
    if reconstructed <= 0:
        reconstructed = float(account.equity)
    store.set(key, f"{reconstructed:.10f}")
    return reconstructed


def build_snapshot(store, cfg: dict, symbol: str, account, open_symbol_positions: int) -> RiskSnapshot:
    trades_today, realized_today, day_key = daily_history_stats(cfg, symbol)
    start_eq = get_or_create_day_start_equity(store, cfg, account, realized_today, day_key)
    current_eq = float(account.equity)
    dd = max(0.0, (start_eq - current_eq) / start_eq * 100.0) if start_eq > 0 else 100.0
    losses = consecutive_losses(cfg, symbol)
    return RiskSnapshot(start_eq, current_eq, dd, trades_today, losses, open_symbol_positions)


def allow_trade(snapshot: RiskSnapshot, cfg: dict):
    r = cfg["risk"]
    if snapshot.daily_drawdown_pct >= float(r["daily_loss_limit_pct"]):
        return False, f"daily_loss_limit:{snapshot.daily_drawdown_pct:.2f}%"
    if snapshot.consecutive_losses >= int(r["max_consecutive_losses"]):
        return False, f"consecutive_losses:{snapshot.consecutive_losses}"
    if snapshot.open_symbol_positions >= int(r["max_open_positions"]):
        return False, f"open_symbol_positions:{snapshot.open_symbol_positions}"
    if snapshot.trades_today >= int(r["max_trades_per_day"]):
        return False, f"max_trades_today:{snapshot.trades_today}"
    return True, "ok"
