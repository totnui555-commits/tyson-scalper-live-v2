# Live deployment checklist

Do not treat engineering readiness as evidence of profitability.

- [ ] Run `python smoke_test.py`
- [ ] Export broker XAUUSD M1 history and run cost-aware backtest
- [ ] Backtest sample covers multiple regimes, not only a few days
- [ ] Review win rate, expectancy, profit factor, drawdown, losing streak
- [ ] Run demo forward test with the exact broker/server/symbol
- [ ] Confirm broker symbol name, point, digits, volume step and stop level
- [ ] Confirm spread assumption is realistic for the hours you trade
- [ ] Populate `news_blackout.csv` with high-impact USD events
- [ ] Run `python setup_account.py --write-whitelist`
- [ ] Run `python preflight.py` and get `READY`
- [ ] Change `mode: live`
- [ ] Change `allow_live_trading: true`
- [ ] Create `LIVE_ARMED`
- [ ] Re-run `python preflight.py`
- [ ] Start with small risk; default is 0.25% per trade
- [ ] Know how to run `python emergency_stop.py`

## Live gate

A real order can only pass when all of these are true:

`mode=live` + `allow_live_trading=true` + account whitelist + server whitelist + `LIVE_ARMED` exists + no `KILL_SWITCH` + session filter + news filter + spread/ATR + risk limits + broker order check.
