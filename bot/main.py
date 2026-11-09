"""Trading bot entry point.

    python -m bot.main premarket   # ~8:40am ET: read yesterday's close, queue orders for the open, text the plan
    python -m bot.main recap       # ~4:30pm ET: text today's fills, P&L and what tomorrow looks like

Add --dry-run --csv path/to/spy.csv --asof YYYY-MM-DD to test without Alpaca.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import notify, strategy

NY = ZoneInfo("America/New_York")
MIN_TRADE_FRACTION = 0.01  # ignore rebalances smaller than 1% of the account


# --------------------------------------------------------------------------- broker

class AlpacaBroker:
    def __init__(self):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
        self.trading = TradingClient(key, secret, paper=paper)
        self.data = StockHistoricalDataClient(key, secret)

    def daily_bars(self, symbol: str, days: int = 450) -> pd.DataFrame:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        last_err = None
        # Full-market (SIP) bars give the true high/low that IBS needs. The free plan
        # serves SIP history with a 15-minute delay, which is fine for daily bars.
        for feed in (DataFeed.SIP, DataFeed.DELAYED_SIP):
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                    start=datetime.now(NY) - timedelta(days=int(days * 1.5)),
                    end=datetime.now(NY) - timedelta(minutes=16),
                    adjustment=Adjustment.ALL, feed=feed,
                )
                df = self.data.get_stock_bars(req).df
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            raise RuntimeError(f"Could not load bars for {symbol}: {last_err}")
        df = df.reset_index()
        df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(NY).dt.normalize().dt.tz_localize(None)
        daily = df.set_index("date")[["open", "high", "low", "close"]].astype(float)
        if symbol != "SPY":
            return daily
        # Alpaca's daily bars can include pre- and after-hours trades, which would
        # distort the day's high/low/close. IBS needs the regular session only, so
        # rebuild the most recent sessions from 5-minute bars (9:30am-4:00pm ET).
        regular = self._regular_session_bars(symbol, feed, sessions=30)
        daily = daily.copy()
        common = regular.index.intersection(daily.index)
        daily.loc[common, ["open", "high", "low", "close"]] = regular.loc[common].values
        return daily

    def _regular_session_bars(self, symbol, feed, sessions: int) -> pd.DataFrame:
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=datetime.now(NY) - timedelta(days=int(sessions * 1.6)),
            end=datetime.now(NY) - timedelta(minutes=16),
            adjustment=Adjustment.ALL, feed=feed,
        )
        m = self.data.get_stock_bars(req).df.reset_index()
        t = pd.to_datetime(m["timestamp"]).dt.tz_convert(NY)
        m = m[(t.dt.time >= pd.Timestamp("09:30").time()) & (t.dt.time < pd.Timestamp("16:00").time())]
        m["date"] = t[m.index].dt.normalize().dt.tz_localize(None)
        g = m.groupby("date")
        out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                            "low": g["low"].min(), "close": g["close"].last()})
        # A session still in progress (or missing its last bars) isn't complete; drop it.
        last_bar = g["timestamp"].max().map(lambda x: pd.Timestamp(x).tz_convert(NY).time())
        return out[last_bar >= pd.Timestamp("15:55").time()]

    def trading_days(self) -> set[pd.Timestamp]:
        from alpaca.trading.requests import GetCalendarRequest
        today = datetime.now(NY).date()
        cal = self.trading.get_calendar(GetCalendarRequest(start=today - timedelta(days=40), end=today + timedelta(days=40)))
        return {pd.Timestamp(c.date) for c in cal}

    def is_trading_day(self, day) -> bool:
        return pd.Timestamp(day) in self.trading_days()

    def equity(self) -> tuple[float, float]:
        a = self.trading.get_account()
        return float(a.equity), float(a.last_equity)

    def positions(self) -> dict[str, int]:
        return {p.symbol: int(float(p.qty)) for p in self.trading.get_all_positions()}

    def position_values(self) -> dict[str, float]:
        return {p.symbol: float(p.market_value) for p in self.trading.get_all_positions()}

    def cancel_open_orders(self) -> None:
        self.trading.cancel_orders()

    def submit_at_open(self, symbol: str, qty: int, side: str) -> None:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        self.trading.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.OPG,  # market-on-open order
        ))

    def todays_fills(self) -> list[str]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        start = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=start, limit=50))
        out = []
        for o in orders:
            if o.filled_qty and float(o.filled_qty) > 0:
                side = "Bought" if str(o.side).lower().endswith("buy") else "Sold"
                out.append(f"{side} {int(float(o.filled_qty))} {o.symbol} @ ${float(o.filled_avg_price):,.2f}")
        return out


class CsvBroker:
    """Fake broker for testing: prices from a CSV, a pretend $100k account."""

    def __init__(self, csv_path: str, asof: str):
        self.dir = os.path.dirname(csv_path) or "."
        self.bars = self._load(csv_path)
        self.asof = pd.Timestamp(asof)
        self.orders: list[str] = []

    @staticmethod
    def _load(path):
        df = pd.read_csv(path, parse_dates=["Date"]).rename(columns=str.lower).set_index("date")
        return df[["open", "high", "low", "close"]].astype(float)

    def daily_bars(self, symbol: str, days: int = 450) -> pd.DataFrame:
        if symbol == "SPY":
            return self.bars.loc[: self.asof]
        path = os.path.join(self.dir, f"{symbol.lower()}_us_d.csv")
        if os.path.exists(path):
            return self._load(path).loc[: self.asof]
        b = self.bars.loc[: self.asof].copy()  # unknown symbol: pretend flat $100
        b[:] = 100.0
        return b

    def trading_days(self):
        return set(self.bars.index)

    def is_trading_day(self, day) -> bool:
        return True

    def equity(self):
        return 100_000.0, 99_800.0

    def positions(self):
        return {"BIL": 1070}

    def position_values(self):
        return {"BIL": 97_905.0}

    def cancel_open_orders(self):
        pass

    def submit_at_open(self, symbol, qty, side):
        self.orders.append(f"{side.upper()} {qty} {symbol}")

    def todays_fills(self):
        return ["(dry run: no real fills)"]


# --------------------------------------------------------------------------- jobs

def _today(broker, dry_run: bool, next_day: bool):
    if dry_run:
        d = broker.asof + (pd.offsets.BDay(1) if next_day else pd.Timedelta(0))
        return d.date()
    return datetime.now(NY).date()


def premarket(broker, dry_run: bool) -> str:
    today = _today(broker, dry_run, next_day=True)
    if not dry_run and not broker.is_trading_day(today):
        return ""
    strategy.TRADING_DAYS = broker.trading_days()
    spy = broker.daily_bars("SPY")
    if not dry_run:
        spy = spy.loc[: pd.Timestamp(today) - pd.Timedelta(days=1)]  # only completed sessions
    sig = strategy.compute_signals(spy)
    prices = {"SPY": sig.close}
    for sym in strategy.SYMBOLS:
        if sym not in prices:
            prices[sym] = float(broker.daily_bars(sym, days=10)["close"].iloc[-1])

    equity, _ = broker.equity()
    target = strategy.target_shares(sig, equity, prices)
    held = broker.positions()

    broker.cancel_open_orders()  # makes re-runs safe: never double-submit
    planned = []
    # Sells first, then buys, all as market-on-open orders.
    for side in ("sell", "buy"):
        for sym in sorted(set(target) | set(held)):
            if sym not in prices:
                continue  # never touch anything the bot doesn't manage
            price = prices[sym]
            delta = target.get(sym, 0) - held.get(sym, 0)
            if abs(delta) * price < MIN_TRADE_FRACTION * equity:
                continue
            if (side == "sell" and delta < 0) or (side == "buy" and delta > 0):
                broker.submit_at_open(sym, abs(delta), side)
                planned.append(f"{side.upper()} {abs(delta)} {sym}")

    lines = [f"PREMARKET {today:%a %b %d}"] + strategy.describe(sig)
    tv = strategy.target_values(sig, equity)
    lines.append("Target: " + ", ".join(f"{s} {v / equity * 100:.0f}%" for s, v in tv.items() if v > 0))
    lines.append("Orders at the open: " + (", ".join(planned) if planned else "none, already on target"))
    lines.append(
        "Watching today: IBS half "
        + ("SELLS if SPY closes in the top 20% of today's range." if sig.ibs_in
           else "BUYS if SPY closes in the bottom 20% of today's range.")
    )
    return "\n".join(lines)


def recap(broker, dry_run: bool) -> str:
    today = _today(broker, dry_run, next_day=False)
    if not dry_run and not broker.is_trading_day(today):
        return ""
    strategy.TRADING_DAYS = broker.trading_days()
    spy = broker.daily_bars("SPY")
    sig = strategy.compute_signals(spy)
    prev_close = float(spy["close"].iloc[-2])
    day_chg = (sig.close / prev_close - 1) * 100

    equity, last_equity = broker.equity()
    start_equity = float(os.environ.get("START_EQUITY", "100000"))
    day_pl, total_pl = equity - last_equity, equity - start_equity
    vals = broker.position_values()

    lines = [f"CLOSE RECAP {today:%a %b %d}"]
    fills = broker.todays_fills()
    lines.append("Fills: " + ("; ".join(fills) if fills else "none"))
    lines.append(f"SPY {day_chg:+.2f}% today, IBS {sig.ibs:.2f}")
    lines.append(f"Account ${equity:,.0f} | today {day_pl:+,.0f} ({day_pl / last_equity * 100:+.2f}%) "
                 f"| since start {total_pl:+,.0f} ({total_pl / start_equity * 100:+.2f}%)")
    if vals:
        lines.append("Holding: " + ", ".join(f"{s} ${v:,.0f}" for s, v in sorted(vals.items())))
    # Preview tomorrow: today's bar is complete, so these are tomorrow's targets.
    lines.append("Tomorrow: " + _tomorrow(sig))
    return "\n".join(lines)


def _tomorrow(sig: strategy.Signals) -> str:
    ibs_part = (f"IBS half holds {strategy.IBS_SYMBOL}" if sig.ibs_in else "IBS half stays in T-bills")
    if sig.ibs < strategy.IBS_BUY:
        ibs_part += " (weak close today: buy signal)"
    elif sig.ibs > strategy.IBS_SELL:
        ibs_part += " (strong close today: sell signal)"
    trend_part = "trend half " + ("in SPY" if sig.trend_in else "in T-bills")
    return f"{ibs_part}; {trend_part}. Orders confirmed in the premarket text."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["premarket", "recap"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--asof")
    ap.add_argument("--force", action="store_true", help="ignore the time-of-day check")
    args = ap.parse_args(argv)

    if args.dry_run:
        broker = CsvBroker(args.csv, args.asof)
    else:
        # GitHub runs each job twice (summer and winter UTC times); only the run that
        # lands in the right New York window does anything.
        now = datetime.now(NY)
        windows = {"premarket": (8, 0, 9, 20), "recap": (16, 5, 17, 0)}
        h1, m1, h2, m2 = windows[args.job]
        in_window = (h1, m1) <= (now.hour, now.minute) <= (h2, m2)
        if not (in_window or args.force or os.environ.get("FORCE_RUN") == "true"):
            print(f"{now:%H:%M} ET is outside the {args.job} window; nothing to do.")
            return 0
        broker = AlpacaBroker()

    msg = premarket(broker, args.dry_run) if args.job == "premarket" else recap(broker, args.dry_run)
    if not msg:
        print("Market closed today; no message sent.")
        return 0
    if args.dry_run and isinstance(broker, CsvBroker) and broker.orders:
        print("(dry run) orders that would be sent:", broker.orders)
    notify.send(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
