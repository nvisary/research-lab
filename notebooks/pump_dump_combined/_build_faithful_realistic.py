"""Rebuild faithful combined (pump + dump) from raw OHLCV with a REALISTIC stop
model, faithful to nb04/05 in every other respect. The ONLY change vs
_build_faithful_liq.py is exit_realistic(): stops are detected on the intrabar
HIGH (short/pump) / LOW (long/dump) instead of the close, and filled at the WORSE
of {stop level, the bar's close} (gap-through) + a 0.1% adverse slippage. This
isolates the effect of the optimistic close-only / exact-stop-fill assumption
(see 08_audit_findings.ipynb finding #1).

Outputs _out/faithful_liq_realistic.parquet with the same schema as
_out/faithful_liq.parquet so the engine can run both side by side.
"""
import sys; sys.path.insert(0, "notebooks/pump_dump_combined")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

WIN=15; H=240; COOLDOWN=10; DAY=1440; FEE=0.00075
THR_P=0.05; THR_D=0.07; VOL_MULT=3.0; DELTA=0.02
STOP_P=0.03; STOP_D=0.20
SLIP=0.001   # 0.1% adverse slippage added on a stop fill

def clusters(c, thr, side):
    n=len(c)
    if n<WIN+5: return []
    r=np.full(n,np.nan); r[WIN:]=c[WIN:]/c[:-WIN]-1
    cond=(r<=-thr) if side<0 else (r>=thr)
    out=[]; i=WIN
    while i<n:
        if not cond[i] or (i>0 and cond[i-1]): i+=1; continue
        st=i; last=i; falses=0; j=i+1
        while j<n:
            if cond[j]: last=j; falses=0
            else:
                falses+=1
                if falses>=COOLDOWN: break
            j+=1
        out.append((st,last,cond)); i=last+COOLDOWN+1
    return out

def fills_time(c, st, last, cond, n):
    return [t for t in range(st, last+1) if cond[t] and t+1<n]

def fills_price(c, st, last, n):
    pos=[st]; lc=c[st]; t=st+1
    while t<=last:
        if c[t]<=lc*(1-DELTA): pos.append(t); lc=c[t]
        t+=1
    return [p for p in pos if p+1<n]

def pump_feats(c,v,h,l,st):
    def ret(k): return c[st]/c[st-k]-1 if st-k>=0 else np.nan
    base=np.median(v[max(0,st-DAY):st]) if st>60 else np.nan
    lr=np.diff(np.log(c[max(0,st-60):st+1]))
    r5=ret(5); prev5=(c[st-5]/c[st-10]-1) if st-10>=0 else np.nan
    return [ret(1),ret(3),r5,ret(15),ret(30), r5-prev5,
            v[st-14:st+1].sum()/(base*15) if base and base>0 else np.nan,
            v[st]/base if base and base>0 else np.nan,
            lr.std() if len(lr)>5 else np.nan,
            (h[st-4:st+1].max()-l[st-4:st+1].min())/c[st],
            c[st]/h[max(0,st-DAY):st+1].max()-1,
            c[st]/l[max(0,st-60):st+1].min()-1]

def dump_feats(c,v,h,l,st):
    def ret(k): return c[st]/c[st-k]-1 if st-k>=0 else np.nan
    base=np.median(v[max(0,st-DAY):st]) if st>60 else np.nan
    lr=np.diff(np.log(c[max(0,st-60):st+1]))
    r5=ret(5); prev10=(c[st-5]/c[st-15]-1) if st-15>=0 else np.nan
    ds=0;k=st
    while k>0 and c[k]<c[k-1]: ds+=1;k-=1
    return [ret(1),ret(3),r5,ret(15),ret(30),ret(60), r5-prev10,
            v[st-14:st+1].sum()/(base*15) if base and base>0 else np.nan,
            v[st]/base if base and base>0 else np.nan,
            lr.std() if len(lr)>5 else np.nan,
            (h[st-14:st+1].max()-l[st-14:st+1].min())/c[st],
            c[st]/h[max(0,st-DAY):st+1].max()-1, c[st]/l[max(0,st-DAY):st+1].min()-1, ds]

PF=["r1","r3","r5","r15","r30","accel5","surge15","surge1","rvol60","rng5","dist_hi240","dist_lo60"]
DF=["r1","r3","r5","r15","r30","r60","accel","surge15","surge1","volreg","rng15","hi_d","lo_d","dstreak"]

def exit_realistic(c, h, l, avg, en, ex, side, stop):
    """REALISTIC: detect the stop on the intrabar HIGH (short) / LOW (long); fill
    at the worse of {stop, that bar's close} (gap-through) + SLIP. Returns
    (exit_idx, pnl_pre_cost). Normal (no-stop) exit unchanged: close at ex."""
    lo, hi = en+1, ex
    for t in range(lo, hi+1):
        if side>0:  # short: adverse = up, trigger if HIGH pierces +stop
            if h[t]/avg-1 >= stop:
                realized = -(c[t]/avg-1)            # short pnl at this bar's close
                return t, min(-stop, realized) - SLIP
        else:       # long: adverse = down, trigger if LOW pierces -stop
            if l[t]/avg-1 <= -stop:
                realized = (c[t]/avg-1)             # long pnl at this bar's close
                return t, min(-stop, realized) - SLIP
    raw=c[hi]/avg-1
    return hi, (raw if side<0 else -raw)

def build_leg(side):
    thr=THR_P if side>0 else THR_D
    stop=STOP_P if side>0 else STOP_D
    gate = side>0
    rows=[]; loaded=0
    for sym in list_symbols():
        try: df=ohlcv(sym,"2024-01-01","2026-07-01","1min")
        except Exception: continue
        c=df["close"].to_numpy("float64"); v=df["volume"].to_numpy("float64")
        h=df["high"].to_numpy("float64"); l=df["low"].to_numpy("float64")
        ts=df.index; n=len(c); dv=c*v; loaded+=1
        for (st,last,cond) in clusters(c,thr,side):
            ex=last+1+H
            if st<DAY or st+1>=n or ex>=n: continue
            if gate:
                base=np.median(v[max(0,st-DAY):st]) if st>60 else np.nan
                surge15=v[st-14:st+1].sum()/(base*15) if base and base>0 else np.nan
                if not (surge15>=VOL_MULT): continue
                pos=fills_time(c,st,last,cond,n)
            else:
                pos=fills_price(c,st,last,n)
            if not pos: continue
            pr=np.array([c[p+1] for p in pos]); ws=np.arange(1,len(pr)+1,dtype=float); ws/=ws.sum()
            avg=(ws*pr).sum()
            exi, pnl_raw = exit_realistic(c, h, l, avg, last, ex, side, stop)
            pnl=pnl_raw-2*FEE
            liq=np.median(dv[max(0,st-DAY):st])
            feats=pump_feats(c,v,h,l,st) if gate else dump_feats(c,v,h,l,st)
            names=PF if gate else DF
            d=dict(sym=sym, entry=ts[st+1], exit_ts=ts[exi], pnl=pnl, liq=liq, ntr=len(pos),
                   stream=("pump" if side>0 else "dump"))
            d.update({names[i]:feats[i] for i in range(len(names))})
            rows.append(d)
        if loaded%40==0: print(f"  [{ 'pump' if side>0 else 'dump'}] loaded {loaded}", flush=True)
    X=pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)
    return X, (PF if gate else DF)

def wf_filter(X, names):
    pred=np.full(len(X),np.nan)
    cuts=[int(len(X)*q) for q in (0.40,0.55,0.70,0.85,1.0)]; prev=cuts[0]
    for cut in cuts[1:]:
        tr=X.iloc[:prev]
        m=HistGradientBoostingRegressor(max_depth=3,learning_rate=0.05,max_iter=300,
            l2_regularization=1.0,min_samples_leaf=50,random_state=0).fit(tr[names],tr.pnl)
        pred[prev:cut]=m.predict(X.iloc[prev:cut][names]); prev=cut
    X=X.copy(); X["pred"]=pred
    keep=(X.pred>0)|(X.pred.isna())
    return X[keep].reset_index(drop=True)

if __name__=="__main__":
    print("building PUMP leg (realistic stop) ...", flush=True)
    PUMP, pf = build_leg(+1)
    print(f"  PUMP raw events: {len(PUMP)}  avg pnl {PUMP.pnl.mean()*100:+.2f}%  (faithful close-stop: +0.34%)", flush=True)
    print("building DUMP leg (realistic stop) ...", flush=True)
    DUMP, df_ = build_leg(-1)
    print(f"  DUMP raw events: {len(DUMP)}  avg pnl {DUMP.pnl.mean()*100:+.2f}%", flush=True)

    PUMPk=wf_filter(PUMP, pf); DUMPk=wf_filter(DUMP, df_)
    print(f"\nPUMP kept {len(PUMPk)}  per-trade {PUMPk.pnl.mean()*100:+.2f}%  win {(PUMPk.pnl>0).mean()*100:.0f}%", flush=True)
    print(f"DUMP kept {len(DUMPk)}  per-trade {DUMPk.pnl.mean()*100:+.2f}%  win {(DUMPk.pnl>0).mean()*100:.0f}%", flush=True)

    both=pd.concat([PUMPk[["sym","entry","exit_ts","pnl","liq","stream"]],
                    DUMPk[["sym","entry","exit_ts","pnl","liq","stream"]]]).sort_values("entry").reset_index(drop=True)
    both.to_parquet("notebooks/pump_dump_combined/_out/faithful_liq_realistic.parquet")
    print(f"\nBOTH {len(both)} signals cached -> _out/faithful_liq_realistic.parquet", flush=True)
