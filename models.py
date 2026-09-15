from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Signal:
    side: str
    score: int
    bar_time: Optional[str] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    atr: Optional[float] = None
    reason: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class PreparedTrade:
    symbol: str
    side: str
    signal_id: str
    requested_price: float
    stop_loss: float
    take_profit: float
    lot: float
    risk_money_target: float
    risk_money_actual: float
    spread: float
    filling_name: str
    filling_mode: int
    use_two_step: bool = False
