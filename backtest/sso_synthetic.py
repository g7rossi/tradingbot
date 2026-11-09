exec(open("backtest/daily_strategies.py").read().split("pd.set_option")[0])
COSTL=0.0005  # wider spread assumed for leveraged ETF
ER={1:0.0009,2:0.0089,3:0.0091}
sig=pd.Series(stateful(ibs<0.2, ibs>0.8).shift(-1).values,index=idx)
hp=sig.shift(1).fillna(0); hp2=sig.shift(2).fillna(0)
enter=(hp==1)&(hp2==0); exit_=(hp==0)&(hp2==1); hold=(hp==1)&(hp2==1); flat=(hp==0)&(hp2==0)
r_oc=C/O-1; r_co=O/C.shift(1)-1; r_cc=C.pct_change()
up=(C>sma200).shift(1).fillna(False)  # SPY above 200-day as of prior close
def ibs_lev(L, gate=None):
    Lser=pd.Series(float(L),index=idx)
    if gate is not None: Lser=Lser.where(gate,1.0)
    fin=(Lser-1)*cash_d + Lser.map(ER)/252
    c=COSTL if L>1 else 0.0003
    r=pd.Series(0.0,index=idx)
    r[enter]=(Lser*r_oc-fin*0.3)[enter]-c
    r[exit_]=(Lser*r_co-fin*0.7)[exit_]-c
    r[hold]=(Lser*r_cc-fin)[hold]
    r[flat]=cash_d[flat]
    return r.loc[START:]
sm=(C>sma200).astype(float); me=idx.to_series().groupby([idx.year,idx.month]).transform("max")==idx.to_series()
trend=from_positions(sm.where(me).ffill().shift(1),0.0003).loc[START:]
spyr=C.pct_change().loc[START:]
sso_bh=(2*r_cc-cash_d-ER[2]/252).loc[START:]
S={
 "IBS on SPY (1x)":ibs_lev(1),
 "IBS on SSO (2x)":ibs_lev(2),
 "IBS on SSO, only when SPY above 200-day":ibs_lev(2,gate=up),
 "IBS on 3x (UPRO-style)":ibs_lev(3),
}
S["Blend: trend + IBS 1x (current bot)"]=0.5*trend+0.5*S["IBS on SPY (1x)"]
S["Blend: trend + IBS on SSO"]=0.5*trend+0.5*S["IBS on SSO (2x)"]
S["Blend: trend + IBS on SSO only above 200-day"]=0.5*trend+0.5*S["IBS on SSO, only when SPY above 200-day"]
S["SPY buy-and-hold"]=spyr
S["SSO buy-and-hold (2x)"]=sso_bh
rows=[]
for k,r in S.items():
    a=sstats(r); y=(1+r).groupby(r.index.year).prod()-1
    rows.append(dict(strategy=k,cagr=round(a["cagr"],1),maxdd=round(a["dd"],1),sharpe=round(a["sharpe"],2),
      worst_yr=round(y.min()*100,1),lose_yrs=int((y<0).sum()),c0615=round(sstats(r.loc["2006":"2015"])["cagr"],1),c1626=round(sstats(r.loc["2016":])["cagr"],1),
      y2008=round(y.get(2008)*100,1),y2018=round(y.get(2018)*100,1),y2020=round(y.get(2020)*100,1),y2022=round(y.get(2022)*100,1),
      k10=round(10000*(1+r).prod())))
pd.set_option("display.width",250); print(pd.DataFrame(rows).set_index("strategy").to_string())
# cost sensitivity
for c in [0.001,0.002]:
    COSTL=c; print("cost",c,"IBS SSO",round(sstats(ibs_lev(2))["cagr"],1),"gated",round(sstats(ibs_lev(2,gate=up))["cagr"],1))
# worst drawdown dates for IBS SSO
r=S["IBS on SSO (2x)"]; eq=(1+r).cumprod(); dd=eq/eq.cummax()-1; t=dd.idxmin(); p=eq.loc[:t].idxmax(); print("IBS SSO worst drop",p.date(),"->",t.date(),round(dd.min()*100,1))
r=S["Blend: trend + IBS on SSO"]; eq=(1+r).cumprod(); dd=eq/eq.cummax()-1; t=dd.idxmin(); p=eq.loc[:t].idxmax(); print("Blend SSO worst drop",p.date(),"->",t.date(),round(dd.min()*100,1))
