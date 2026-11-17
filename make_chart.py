"""Draws docs/equity_curve.png: growth of $10,000, strategy vs. SPY buy-and-hold.
Run from the repo root: python backtest/make_chart.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mt

exec(open("backtest/sso_real.py").read().split("rows={}")[0])

strat = blend_real.dropna()
spy = spyr.loc[strat.index[0]:].dropna()
eq_s = 10000 * (1 + strat).cumprod()
eq_b = 10000 * (1 + spy).cumprod()
dd_s = (eq_s / eq_s.cummax() - 1) * 100
dd_b = (eq_b / eq_b.cummax() - 1) * 100

GREEN, GRAY = "#1f7a4d", "#9ca3af"
fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6.2), dpi=150, sharex=True,
                              gridspec_kw={"height_ratios": [3, 1.2]})
ax.plot(eq_b.index, eq_b, color=GRAY, lw=1.6, label=f"S&P 500 buy-and-hold (SPY): ${eq_b.iloc[-1]:,.0f}")
ax.plot(eq_s.index, eq_s, color=GREEN, lw=2.0, label=f"This strategy: ${eq_s.iloc[-1]:,.0f}")
ax.set_title("Growth of $10,000, May 2007 – Oct 2026 (backtest, after trading costs)",
             loc="left", fontsize=12, fontweight="bold")
ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
ax.legend(frameon=False, loc="upper left")
ax2.fill_between(dd_b.index, dd_b, 0, color=GRAY, alpha=0.35, lw=0)
ax2.plot(dd_s.index, dd_s, color=GREEN, lw=1.2)
ax2.set_ylabel("Drawdown")
ax2.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
for a in (ax, ax2):
    a.grid(axis="y", color="#e5e7eb")
    a.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig("docs/equity_curve.png")
print("saved docs/equity_curve.png")
