"""One pass over the universe: for each PUMP event compute the per-trade pnl AND
exit timestamp under EVERY stop level in STOPS (realistic intrabar-high trigger +
gap-through fill + 0.1% slip), plus no-stop. Lets the notebook sweep stop levels
without re-reading raw data. Faithful to _build_pump_nostop / _build_faithful_realistic
in entries/scale-in/horizon; the ONLY thing that varies is the stop.
"""
import sys; sys.path.insert(0, "notebooks/pump_dump_combined")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

WIN=15; H=240; COOLDOWN=10; DAY=1440; FEE=0.00075
THR_P=0.05; VOL_MULT=3.0; SLIP=0.001
STOPS=[0.07,0.10,0.15,0.20,0.25,0.30,0.40,0.50,0.70,1.00,np.inf]   # last = no-stop

def clusters(c, thr):
    n=len(c)
    if n<WIN+5: return []
    r=np.full(n,np.nan); r[WIN:]=c[WIN:]/c[:-WIN]-1
    cond=(r>=thr)
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

def build():
    rows=[]; loaded=0
    for sym in list_symbols():
        try: df=ohlcv(sym,"2024-01-01","2026-07-01","1min")
        except Exception: continue
        c=df["close"].to_numpy("float64"); v=df["volume"].to_numpy("float64")
        h=df["high"].to_numpy("float64"); l=df["low"].to_numpy("float64")
        ts=df.index; n=len(c); dv=c*v; loaded+=1
        for (st,last,cond) in clusters(c,THR_P):
            ex=last+1+H
            if st<DAY or st+1>=n or ex>=n: continue
            base=np.median(v[max(0,st-DAY):st]) if st>60 else np.nan
            surge15=v[st-14:st+1].sum()/(base*15) if base and base>0 else np.nan
            if not (surge15>=VOL_MULT): continue
            pos=fills_time(c,st,last,cond,n)
            if not pos: continue
            pr=np.array([c[p+1] for p in pos]); ws=np.arange(1,len(pr)+1,dtype=float); ws/=ws.sum()
            avg=(ws*pr).sum()
            lo=last+1; hi=ex
            hexc=h[lo:hi+1]/avg-1                      # short adverse excursion (up)
            cwin=c[lo:hi+1]
            timeout_pnl=-(c[hi]/avg-1)-2*FEE
            d=dict(sym=sym, entry=ts[st+1], liq=np.median(dv[max(0,st-DAY):st]))
            feats=pump_feats(c,v,h,l,st); d.update({PF[i]:feats[i] for i in range(len(PF))})
            for i,S in enumerate(STOPS):
                if np.isinf(S):
                    d[f"pnl{i}"]=timeout_pnl; d[f"exit{i}"]=ts[hi]; continue
                trig=np.nonzero(hexc>=S)[0]
                if len(trig):
                    t0=int(trig[0]); realized=-(cwin[t0]/avg-1)
                    d[f"pnl{i}"]=min(-S, realized)-SLIP-2*FEE; d[f"exit{i}"]=ts[lo+t0]
                else:
                    d[f"pnl{i}"]=timeout_pnl; d[f"exit{i}"]=ts[hi]
            rows.append(d)
        if loaded%40==0: print(f"  loaded {loaded}", flush=True)
    return pd.DataFrame(rows).dropna(subset=PF).sort_values("entry").reset_index(drop=True)

if __name__=="__main__":
    print(f"building pump stop-sweep over {STOPS} ...", flush=True)
    X=build()
    X.to_parquet("notebooks/pump_dump_combined/_out/pump_stopsweep.parquet")
    print(f"\n{len(X)} raw pump events. raw mean per-trade by stop:", flush=True)
    for i,S in enumerate(STOPS):
        p=X[f"pnl{i}"]; lab=f"{S:.0%}" if np.isfinite(S) else "no-stop"
        print(f"  {lab:>8}: mean {p.mean()*100:+.2f}%  median {p.median()*100:+.2f}%  "
              f"win {(p>0).mean()*100:.0f}%  std {p.std()*100:.1f}%  worst {p.min()*100:.0f}%", flush=True)
    print("cached -> _out/pump_stopsweep.parquet", flush=True)
