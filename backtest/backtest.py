import sys, json
import numpy as np, pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"
COST = 0.0005  # 0.05% per side, slippage + spread

def load(t):
    d = pd.read_csv(f"{DATA}/{t}_us_d.csv", parse_dates=["Date"]).set_index("Date")
    return d

raw = {t: load(t) for t in ["spy", "veu", "agg", "bil"]}

# Stooq stopped adding back distributions for VEU, AGG, BIL around 2022-23.
# Patch with an approximate daily yield accrual (annual %, by period).
PATCH = {
    "bil": [("2022-07-01", "2022-12-31", 2.0), ("2023-01-01", "2023-12-31", 5.0),
            ("2024-01-01", "2024-12-31", 5.1), ("2025-01-01", "2025-12-31", 4.2),
            ("2026-01-01", "2026-12-31", 3.9)],
    "agg": [("2023-01-01", "2023-12-31", 3.3), ("2024-01-01", "2024-12-31", 3.6),
            ("2025-01-01", "2025-12-31", 3.8), ("2026-01-01", "2026-12-31", 3.9)],
    "veu": [("2023-01-01", "2026-12-31", 3.2)],
}

def closes(patch=True):
    px = pd.DataFrame({t: raw[t]["Close"] for t in raw}).dropna()
    if patch:
        r = px.pct_change()
        for t, spans in PATCH.items():
            for a, b, y in spans:
                m = (r.index >= a) & (r.index <= b)
                r.loc[m, t] += y / 100 / 252
        px = (1 + r.fillna(0)).cumprod() * px.iloc[0]
    return px

def rsi(s, n=2):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)

def gem(px, look=252):
    """Monthly dual momentum. Signal at month-end close, trade next day's close."""
    ret = px.pct_change().fillna(0)
    mom = px / px.shift(look) - 1
    month_end = px.index.to_series().groupby([px.index.year, px.index.month]).max()
    hold, held, trades, log = pd.Series(index=px.index, dtype=object), None, 0, []
    signal_days = set(month_end.values)
    pending = None
    for d in px.index:
        if pending is not None:
            if pending != held:
                trades += 1
                log.append((d, pending))
            held, pending = pending, None
        hold[d] = held
        if d in signal_days and not np.isnan(mom.loc[d, "bil"]) and not np.isnan(mom.loc[d, "veu"]):
            m = mom.loc[d]
            pending = ("spy" if m.spy >= m.veu else "veu") if m.spy > m.bil else "agg"
    held_prev = hold.shift(1)
    r = pd.Series(0.0, index=px.index)
    for d in px.index:
        h = held_prev[d]
        if isinstance(h, str):
            r[d] = ret.loc[d, h]
    # cost on switch days
    sw = (hold != hold.shift(1)) & hold.notna() & hold.shift(1).notna()
    r[sw] -= 2 * COST
    start = hold.first_valid_index()
    return r.loc[start:], trades, log

def rsi2(px, op, buy_lvl=10, cash="bil"):
    """RSI(2) on SPY: signal at close, enter/exit at next open. Idle cash in BIL."""
    c, o = px["spy"], op
    r2, sma200, sma5 = rsi(c), c.rolling(200).mean(), c.rolling(5).mean()
    cash_ret = px[cash].pct_change().fillna(0)
    r = pd.Series(0.0, index=px.index)
    inpos, entry_px, trades, n_days = False, None, 0, 0
    idx = px.index
    for i in range(1, len(idx)):
        d, p = idx[i], idx[i - 1]
        if inpos:
            # exit signal at prior close -> exit at today's open
            if c[p] > sma5[p]:
                r[d] = o[d] / c[p] - 1 - COST
                inpos = False
                r[d] += 0  # rest of day in cash, ignore
            else:
                r[d] = c[d] / c[p] - 1
                n_days += 1
        else:
            if c[p] > sma200[p] and r2[p] < buy_lvl:
                r[d] = c[d] / o[d] - 1 - COST
                inpos = True
                trades += 1
                n_days += 1
            else:
                r[d] = cash_ret[d]
    return r, trades, n_days

def stats(r, rf=None):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    yr = (1 + r).groupby(r.index.year).prod() - 1
    return dict(cagr=round(cagr * 100, 2), vol=round(vol * 100, 1), maxdd=round(dd * 100, 1),
                worst_year=round(yr.min() * 100, 1), sharpe=round((r.mean() * 252) / vol, 2),
                growth_of_10k=round(10000 * eq.iloc[-1]), years=round(yrs, 1))

def run(patch=True, look=252, buy_lvl=10, w_core=0.8):
    px = closes(patch)
    op = raw["spy"]["Open"].reindex(px.index)
    g, gt, glog = gem(px, look)
    s, st, sd = rsi2(px, op, buy_lvl)
    s = s.loc[g.index]
    combo = w_core * g + (1 - w_core) * s  # monthly-ish rebalanced approx (daily weights)
    spy = px["spy"].pct_change().loc[g.index].fillna(0)
    sixty = 0.6 * spy + 0.4 * px["agg"].pct_change().loc[g.index].fillna(0)
    return px, dict(gem=g, rsi=s, combo=combo, spy=spy, sixty40=sixty), dict(
        gem_trades=gt, rsi_trades=st, rsi_days=sd, glog=glog)

if __name__ == "__main__":
    px, R, info = run()
    out = {k: stats(v) for k, v in R.items()}
    print("start", R["gem"].index[0].date(), "end", R["gem"].index[-1].date())
    for k, v in out.items():
        print(k, v)
    print("GEM switches:", info["gem_trades"], "RSI trades:", info["rsi_trades"], "RSI days in market:", info["rsi_days"])
    print("GEM log:")
    for d, a in info["glog"]:
        print(" ", d.date(), a)
    # robustness
    print("\nROBUSTNESS")
    for patch in [True, False]:
        _, Rx, _ = run(patch=patch)
        print("patch", patch, "combo", stats(Rx["combo"])["cagr"], "gem", stats(Rx["gem"])["cagr"])
    for look in [126, 189, 252]:
        _, Rx, ix = run(look=look)
        print("lookback", look, stats(Rx["gem"]), "switches", ix["gem_trades"])
    for lvl in [5, 10, 15]:
        _, Rx, ix = run(buy_lvl=lvl)
        print("rsi<", lvl, stats(Rx["rsi"]), "trades", ix["rsi_trades"])
    print("\nSUBPERIODS")
    for a, b in [("2008", "2016"), ("2017", "2026"), ("2020", "2026")]:
        print(a, b, {k: stats(v.loc[a:b])["cagr"] for k, v in R.items()},
              {k: stats(v.loc[a:b])["maxdd"] for k, v in R.items()})
    yr = pd.DataFrame({k: (1 + v).groupby(v.index.year).prod() - 1 for k, v in R.items()})
    print("\nYEARLY %\n", (yr * 100).round(1).to_string())
    eq = pd.DataFrame({k: 10000 * (1 + v).cumprod() for k, v in R.items()}).resample("QE").last().round(0)
    eq.to_csv(sys.argv[2] if len(sys.argv) > 2 else "equity_quarterly.csv")
