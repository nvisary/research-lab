"""Rebuild the PUMP leg with NO stop (like the dump leg): scale-in short, exit at
the fixed time horizon (last+1+240) on close, no stop-loss at all. Everything
else identical to _build_faithful_realistic.py. Tests the user's hypothesis that
the fade edge is there but the stop was killing it (mirror of why dump uses no stop).

Outputs _out/pump_nostop.parquet (kept = WF-filtered) + prints raw vs kept.
"""
import sys; sys.path.insert(0, "notebooks/pump_dump_combined")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

WIN=15; H=240; COOLDOWN=10; DAY=1440; FEE=0.00075
THR_P=0.05; VOL_MULT=3.0

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

PF=["r1","r3","r5","r15","r30","accel5","surge15","surge1","rvol60","rng5","dist_hi240","dist_lo60"]

def build_pump_nostop():
    rows=[]; loaded=0
    for sym in list_symbols():
        try: df=ohlcv(sym,"2024-01-01","2026-07-01","1min")
        except Exception: continue
        c=df["close"].to_numpy("float64"); v=df["volume"].to_numpy("float64")
        h=df["high"].to_numpy("float64"); l=df["low"].to_numpy("float64")
        ts=df.index; n=len(c); dv=c*v; loaded+=1
        for (st,last,cond) in clusters(c,THR_P,+1):
            ex=last+1+H
            if st<DAY or st+1>=n or ex>=n: continue
            base=np.median(v[max(0,st-DAY):st]) if st>60 else np.nan
            surge15=v[st-14:st+1].sum()/(base*15) if base and base>0 else np.nan
            if not (surge15>=VOL_MULT): continue
            pos=fills_time(c,st,last,cond,n)
            if not pos: continue
            pr=np.array([c[p+1] for p in pos]); ws=np.arange(1,len(pr)+1,dtype=float); ws/=ws.sum()
            avg=(ws*pr).sum()
            # NO STOP: short held to the fixed horizon, exit on close[ex]
            pnl = -(c[ex]/avg-1) - 2*FEE
            liq=np.median(dv[max(0,st-DAY):st])
            feats=pump_feats(c,v,h,l,st)
            d=dict(sym=sym, entry=ts[st+1], exit_ts=ts[ex], pnl=pnl, liq=liq, ntr=len(pos), stream="pump")
            d.update({PF[i]:feats[i] for i in range(len(PF))})
            rows.append(d)
        if loaded%40==0: print(f"  [pump-nostop] loaded {loaded}", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)

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
    print("building PUMP leg (NO stop) ...", flush=True)
    P=build_pump_nostop()
    print(f"  raw events {len(P)}  mean {P.pnl.mean()*100:+.2f}%  median {P.pnl.median()*100:+.2f}%  "
          f"win {(P.pnl>0).mean()*100:.0f}%  std {P.pnl.std()*100:.1f}%  worst {P.pnl.min()*100:.0f}%", flush=True)
    Pk=wf_filter(P, PF)
    print(f"  kept {len(Pk)}  mean {Pk.pnl.mean()*100:+.2f}%  median {Pk.pnl.median()*100:+.2f}%  "
          f"win {(Pk.pnl>0).mean()*100:.0f}%  std {Pk.pnl.std()*100:.1f}%  worst {Pk.pnl.min()*100:.0f}%", flush=True)
    # save BOTH raw and kept so the notebook can show the filter effect
    P.to_parquet("notebooks/pump_dump_combined/_out/pump_nostop_raw.parquet")
    Pk[["sym","entry","exit_ts","pnl","liq","stream"]].to_parquet("notebooks/pump_dump_combined/_out/pump_nostop.parquet")
    print("cached -> _out/pump_nostop{,_raw}.parquet", flush=True)
