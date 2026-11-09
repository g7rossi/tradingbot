exec(open("backtest/sso_synthetic.py").read().split("rows=[]")[0])
s=pd.read_csv('data/sso_us_d.csv',parse_dates=['Date']).set_index('Date').reindex(idx)
sO,sC=s.Open,s.Close
def ibs_real(cost):
    r=pd.Series(0.0,index=idx)
    r[enter]=(sC/sO-1)[enter]-cost
    r[exit_]=(sO/sC.shift(1)-1)[exit_]-cost
    r[hold]=sC.pct_change()[hold]
    r[flat]=cash_d[flat]
    return r
ST="2006-07-01"
real=ibs_real(0.0005).loc[ST:]; syn=ibs_lev(2).loc[ST:]
blend_real=0.5*trend.loc[ST:]+0.5*real; blend_syn=0.5*trend.loc[ST:]+0.5*syn
rows={}
for k,r in {"IBS on SSO - synthetic":syn,"IBS on SSO - REAL":real,"Blend w/ SSO - synthetic":blend_syn,"Blend w/ SSO - REAL":blend_real,
            "Current bot (1x)":(0.5*trend+0.5*S["IBS on SPY (1x)"]).loc[ST:],"SPY buy-and-hold":spyr.loc[ST:]}.items():
    a=sstats(r); y=(1+r).groupby(r.index.year).prod()-1
    rows[k]=dict(cagr=round(a["cagr"],1),maxdd=round(a["dd"],1),sharpe=round(a["sharpe"],2),worst_yr=round(y.min()*100,1),lose_yrs=int((y<0).sum()),
                 c06_15=round(sstats(r.loc[:"2015"])["cagr"],1),c16_26=round(sstats(r.loc["2016":])["cagr"],1),k10=round(10000*(1+r).prod()))
pd.set_option("display.width",250); print(pd.DataFrame(rows).T.to_string())
for c in [0.0002,0.001,0.002]:
    print("cost",c,"real IBS SSO",round(sstats(ibs_real(c).loc[ST:])["cagr"],1),"real blend",round(sstats(0.5*trend.loc[ST:]+0.5*ibs_real(c).loc[ST:])["cagr"],1))
# opening-auction gap: SSO open vs 2x SPY overnight (does SSO open price track?)
gap=(sO/sC.shift(1)-1)-2*(O/C.shift(1)-1); print("SSO overnight tracking error, median abs %", round(gap.abs().median()*100,3), "mean %", round(gap.mean()*100,4))
y=pd.DataFrame({"real_blend":blend_real,"syn_blend":blend_syn,"real_ibs":real}); print(((1+y).groupby(y.index.year).prod()-1).mul(100).round(1).T.to_string())
