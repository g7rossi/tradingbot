"""Strategy logic: 50% monthly 200-day trend filter + 50% IBS mean reversion on SPY.

Everything here is a pure function of daily SPY bars, so the bot never needs to
remember anything between runs: it recomputes today's target from price history.

Rules
-----
Trend sleeve (50%): at each month-end close, hold SPY if SPY closed above its
    200-day simple moving average, otherwise hold T-bills (BIL). Stays put
    until the next month-end check.
IBS sleeve (50%): IBS = (close - low) / (high - low), where SPY closed within the
    day's range. Buy when SPY's IBS < 0.2 (weak close). Sell when IBS > 0.8
    (strong close). In between, keep whatever you had. Idle cash sits in BIL.
    The signal always comes from SPY, but this half trades SSO (a 2x S&P 500
    fund) by default. Set IBS_SYMBOL=SPY to trade it without leverage.
Both sleeves act at the NEXT market open after the signal, which is what the
backtest's realistic version assumed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

IBS_BUY = 0.2
IBS_SELL = 0.8
SMA_DAYS = 200
TREND_WEIGHT = 0.5
IBS_WEIGHT = 0.5
CASH_BUFFER = 0.02  # keep 2% in cash so whole-share rounding never overspends
TREND_SYMBOL = "SPY"
IBS_SYMBOL = os.environ.get("IBS_SYMBOL", "SSO").upper()
CASH_SYMBOL = "BIL"
SYMBOLS = list(dict.fromkeys([TREND_SYMBOL, IBS_SYMBOL, CASH_SYMBOL]))


@dataclass
class Signals:
    asof: pd.Timestamp          # date of the bar the signals are based on
    close: float
    high: float
    low: float
    sma200: float
    ibs: float
    ibs_in: bool                # IBS sleeve should hold SPY
    ibs_last_event: str         # e.g. "buy signal 2026-10-14"
    trend_in: bool              # trend sleeve should hold SPY
    trend_checked: pd.Timestamp  # month-end date the trend decision came from
    trend_check_close: float
    trend_check_sma: float

    @property
    def spy_weight(self) -> float:
        return TREND_WEIGHT * self.trend_in + IBS_WEIGHT * self.ibs_in

    @property
    def pct_above_sma(self) -> float:
        return (self.close / self.sma200 - 1) * 100


def ibs_series(bars: pd.DataFrame) -> pd.Series:
    rng = (bars["high"] - bars["low"]).where(lambda r: r > 0)
    return ((bars["close"] - bars["low"]) / rng).fillna(0.5)


def compute_signals(bars: pd.DataFrame, asof: pd.Timestamp | None = None) -> Signals:
    """bars: daily SPY bars indexed by date with open/high/low/close (dividend-adjusted)."""
    bars = bars.sort_index()
    if asof is not None:
        bars = bars.loc[:asof]
    if len(bars) < SMA_DAYS + 25:
        raise ValueError(f"Need at least {SMA_DAYS + 25} daily bars, got {len(bars)}")

    close = bars["close"]
    sma = close.rolling(SMA_DAYS).mean()
    ibs = ibs_series(bars)

    # IBS state = whichever extreme happened most recently.
    events = pd.Series(pd.NA, index=bars.index, dtype="object")
    events[ibs < IBS_BUY] = "buy"
    events[ibs > IBS_SELL] = "sell"
    events = events.dropna()
    if len(events):
        last_date, last_event = events.index[-1], events.iloc[-1]
        ibs_in = last_event == "buy"
        ibs_last = f"{last_event} signal {last_date:%b %d}"
    else:
        ibs_in, ibs_last = False, "no signal yet"

    # Trend: decision taken at the most recent month-end close.
    idx = bars.index
    month_end = idx.to_series().groupby([idx.year, idx.month]).transform("max") == idx.to_series()
    last = idx[-1]
    # The current month's last bar only counts once the month is actually over.
    # If the latest bar is the last trading day of its month, the caller can tell us
    # via is_month_end(); here we treat a bar as month-end only if a later month exists
    # in the data OR it is flagged explicitly.
    me_dates = idx[month_end.values]
    completed = [d for d in me_dates if (d.year, d.month) != (last.year, last.month)]
    if _is_last_trading_day_of_month(bars, last):
        completed.append(last)
    tdate = completed[-1]
    trend_in = bool(close[tdate] > sma[tdate])

    return Signals(
        asof=last, close=float(close.iloc[-1]), high=float(bars["high"].iloc[-1]),
        low=float(bars["low"].iloc[-1]), sma200=float(sma.iloc[-1]), ibs=float(ibs.iloc[-1]),
        ibs_in=bool(ibs_in), ibs_last_event=ibs_last, trend_in=trend_in, trend_checked=tdate,
        trend_check_close=float(close[tdate]), trend_check_sma=float(sma[tdate]),
    )


# Filled in by the caller with the exchange calendar (Alpaca) or left None for history.
TRADING_DAYS: set[pd.Timestamp] | None = None


def _is_last_trading_day_of_month(bars: pd.DataFrame, day: pd.Timestamp) -> bool:
    """True if `day` is the final trading day of its month."""
    if TRADING_DAYS:
        later = [d for d in TRADING_DAYS if d > day]
        nxt = min(later) if later else None
        return nxt is not None and nxt.month != day.month
    # Without a calendar: weekday-based guess (holidays at month end are rare).
    nxt = day + pd.offsets.BDay(1)
    return nxt.month != day.month


def target_values(sig: Signals, equity: float) -> dict[str, float]:
    investable = equity * (1 - CASH_BUFFER)
    vals = {sym: 0.0 for sym in SYMBOLS}
    vals[TREND_SYMBOL] += investable * TREND_WEIGHT * sig.trend_in
    vals[IBS_SYMBOL] += investable * IBS_WEIGHT * sig.ibs_in
    vals[CASH_SYMBOL] += investable - sum(v for k, v in vals.items() if k != CASH_SYMBOL)
    return vals


def target_shares(sig: Signals, equity: float, prices: dict[str, float]) -> dict[str, int]:
    return {sym: int(v // prices[sym]) for sym, v in target_values(sig, equity).items()}


def describe(sig: Signals) -> list[str]:
    """Plain-English lines about what the bot sees."""
    weak_strong = "weak" if sig.ibs < IBS_BUY else "strong" if sig.ibs > IBS_SELL else "middle"
    lines = [
        f"SPY close ${sig.close:,.2f} ({sig.asof:%b %d}), IBS {sig.ibs:.2f} ({weak_strong} close)",
        f"200-day avg ${sig.sma200:,.2f}; SPY is {sig.pct_above_sma:+.1f}% vs it",
        f"Trend half: {'IN SPY' if sig.trend_in else 'IN T-BILLS'} (month-end check {sig.trend_checked:%b %d})",
        f"IBS half: {'IN ' + IBS_SYMBOL if sig.ibs_in else 'IN T-BILLS'} (last {sig.ibs_last_event})",
    ]
    return lines
