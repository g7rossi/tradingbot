import sys
import numpy as np, pandas as pd
sys.argv = ["x", "data"]
exec(open("backtest/backtest.py").read().split('if __name__')[0])

COSTS = [0.0003, 0.001]   # per side: 0.03% realistic SPY, 0.10% pessimistic
px = closes(True)
spy = raw["spy"].reindex(px.index)
O, H, L, C = spy.Open, spy.High, spy.Low, px.spy  # C = adjusted close (patched set)
# scale O/H/L to match close adjustment
k = C / spy.Close
O, H, L = O * k, H * k, L * k
cash_d = px.bil.pct_change().fillna(0)
cash_d[px.index < "2007-06-01"] = 0.045 / 252

START = "2006-01-01"
IS, OOS = ("2006", "2015"), ("2016", "2026")

def from_positions(pos, cost):
    """pos[t] = fraction held over close(t-1)->close(t). Cash earns T-bill."""
    pos = pos.fillna(0)
    r = pos * C.pct_change().fillna(0) + (1 - pos) * cash_d
    turn = pos.diff().abs().fillna(0)
    return r - turn * cost

def overnight(cost):
    # hold close->open each day, cash rest of day (ignore intraday cash)
    r = O / C.shift(1) - 1
    return r.fillna(0) - 2 * cost

def intraday(cost):
    r = C / O - 1
    return r.fillna(0) - 2 * cost

ibs = (C - L) / (H - L).replace(0, np.nan)
sma200, sma20, sma5 = C.rolling(200).mean(), C.rolling(20).mean(), C.rolling(5).mean()
sd20 = C.rolling(20).std()
ret = C.pct_change()

def stateful(entry, exit_):
    """Enter at close when entry True, exit at close when exit_ True. Position applies next day."""
    p, out = 0, []
    for e, x in zip(entry.fillna(False), exit_.fillna(False)):
        if p == 0 and e: p = 1
        elif p == 1 and x: p = 0
        out.append(p)
    return pd.Series(out, index=entry.index).shift(1)

idx = C.index
# turn of month: hold from close of 2nd-to-last trading day thru close of 3rd trading day
g = idx.to_series().groupby([idx.year, idx.month])
rank_from_end = g.cumcount(ascending=False)
rank_from_start = g.cumcount()
tom_hold = ((rank_from_end <= 0) | (rank_from_start <= 2)).astype(float)  # days whose return we capture
# capture return of last trading day + first 3 days of month
vol20 = ret.rolling(20).std() * np.sqrt(252)

STRATS = {
    "Overnight (buy close, sell open)": ("direct", overnight),
    "Intraday (buy open, sell close)": ("direct", intraday),
    "IBS < 0.2, sell when IBS > 0.8": ("pos", stateful(ibs < 0.2, ibs > 0.8)),
    "IBS < 0.2 + above 200-day": ("pos", stateful((ibs < 0.2) & (C > sma200), ibs > 0.8)),
    "3 down days, sell first up day": ("pos", stateful((ret < 0) & (ret.shift(1) < 0) & (ret.shift(2) < 0), ret > 0)),
    "Buy after any down day (1 day)": ("pos", (ret < 0).astype(float).shift(1)),
    "Buy after any up day (1 day)": ("pos", (ret > 0).astype(float).shift(1)),
    "RSI(2) < 10 + above 200-day": ("pos", stateful((rsi(C) < 10) & (C > sma200), C > sma5)),
    "Bollinger lower band + above 200-day": ("pos", stateful((C < sma20 - 2 * sd20) & (C > sma200), C > sma20)),
    "10/50-day moving-average crossover": ("pos", (C.rolling(10).mean() > C.rolling(50).mean()).astype(float).shift(1)),
    "Daily 200-day filter": ("pos", (C > sma200).astype(float).shift(1)),
    "Turn of the month (4 days/month)": ("pos", tom_hold),
    "Volatility targeting (15% target)": ("pos", (0.15 / vol20).clip(upper=1).shift(1)),
}

def sstats(r):
    r = r.dropna()
    yrs = len(r) / 252
    eq = (1 + r).cumprod()
    vol = r.std() * np.sqrt(252)
    return dict(cagr=(eq.iloc[-1] ** (1 / yrs) - 1) * 100, dd=(eq / eq.cummax() - 1).min() * 100,
                sharpe=(r.mean() * 252) / vol if vol > 0 else np.nan)

def evaluate(cost):
    rows = []
    bh = C.pct_change().fillna(0)
    sm = (C > sma200).astype(float)
    me = idx.to_series().groupby([idx.year, idx.month]).transform("max") == idx.to_series()
    mpos = sm.where(me).ffill().shift(1)
    allr = {"SPY buy-and-hold": bh, "Monthly 200-day filter (current pick)": from_positions(mpos, cost)}
    for name, (kind, s) in STRATS.items():
        if kind == "direct":
            allr[name] = s(cost)
        else:
            allr[name] = from_positions(s, cost)
    for name, r in allr.items():
        r = r.loc[START:]
        full, a, b = sstats(r), sstats(r.loc[IS[0]:IS[1]]), sstats(r.loc[OOS[0]:OOS[1]])
        if name in STRATS and STRATS[name][0] == "pos":
            p = STRATS[name][1].loc[START:].fillna(0)
            tim, trades = p.mean() * 100, (p.diff().abs() > 0.5).sum() / 2 / (len(p) / 252)
        elif "Overnight" in name or "Intraday" in name:
            tim, trades = (30 if "Overnight" in name else 70), 252
        elif "Monthly" in name:
            p = mpos.loc[START:].fillna(0); tim, trades = p.mean() * 100, (p.diff().abs() > 0.5).sum() / 2 / (len(p) / 252)
        else:
            tim, trades = 100, 0
        rows.append(dict(strategy=name, cagr=full["cagr"], maxdd=full["dd"], sharpe=full["sharpe"],
                         sharpe_0615=a["sharpe"], sharpe_1626=b["sharpe"], cagr_0615=a["cagr"], cagr_1626=b["cagr"],
                         time_in_mkt=tim, round_trips_yr=trades))
    return pd.DataFrame(rows).set_index("strategy").round(2)

pd.set_option("display.width", 250)
for c in COSTS:
    print(f"\n=== cost {c*100:.2f}% per side ===")
    print(evaluate(c).sort_values("sharpe", ascending=False).to_string())
