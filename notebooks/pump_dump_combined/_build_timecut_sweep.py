"""Time-cut early-exit sweep (user's idea).

Horizon is fixed at H=240 min. N is SUBTRACTED from the horizon: the checkpoint is
at minute (H - N) into the hold (bar = ex - N). If at that single checkpoint the
position is STILL underwater (close-based MTM < 0), exit early there instead of
waiting the last N minutes. N=0 => checkpoint at the horizon => no early exit =
the surviving strategy baseline (pump @30% catastrophe stop, dump @20%).

The catastrophe stop (pump 30% on intrabar high, dump 20% on intrabar low,
gap-through fill + 0.1% slip) is ALWAYS on and fires whenever it triggers,
regardless of the time-cut; the time-cut only adds an earlier exit.

Faithful to _build_faithful_realistic.py in entries / scale-in / horizon / costs.
Writes two parquets (features differ per leg):
    _out/pump_timecut.parquet  _out/dump_timecut.parquet
each with sym, entry, liq, <features>, and pnl{i}/exit{i} for every N in TIMECUTS.
"""
import sys; sys.path.insert(0, "notebooks/pump_dump_combined")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

WIN=15; H=240; COOLDOWN=10; DAY=1440; FEE=0.00075
THR_P=0.05; THR_D=0.07; VOL_MULT=3.0; DELTA=0.02
CSTOP_P=0.30; CSTOP_D=0.20          # catastrophe stops of the surviving strategy
SLIP=0.001                          # adverse slippage on any active (stop/time-cut) exit
TIMECUTS=[0,20,40,60,90,120,150,180]   # N minutes subtracted from the 240 horizon

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

def exit_timecut(c, h, l, avg, en, ex, side, cstop, N):
    """First of: catastrophe stop (any t) / time-cut at bar ex-N if underwater / horizon.
    Returns (exit_idx, pnl_pre_cost). side>0 short(pump), side<0 long(dump)."""
    lo, hi = en+1, ex
    cp = hi - N                                  # N=0 -> cp=hi -> no early exit
    for t in range(lo, hi+1):
        if side>0:                               # short: catastrophe = price up through +cstop
            if h[t]/avg-1 >= cstop:
                return t, min(-cstop, -(c[t]/avg-1)) - SLIP
        else:                                    # long: catastrophe = price down through -cstop
            if l[t]/avg-1 <= -cstop:
                return t, min(-cstop, (c[t]/avg-1)) - SLIP
        if N>0 and t==cp:                        # time-cut checkpoint
            mtm = -(c[t]/avg-1) if side>0 else (c[t]/avg-1)
            if mtm < 0:
                return t, mtm - SLIP
    raw=c[hi]/avg-1
    return hi, (-raw if side>0 else raw)

def build_leg(side):
    thr=THR_P if side>0 else THR_D
    cstop=CSTOP_P if side>0 else CSTOP_D
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
            liq=np.median(dv[max(0,st-DAY):st])
            feats=pump_feats(c,v,h,l,st) if gate else dump_feats(c,v,h,l,st)
            names=PF if gate else DF
            d=dict(sym=sym, entry=ts[st+1], liq=liq, stream=("pump" if side>0 else "dump"))
            d.update({names[i]:feats[i] for i in range(len(names))})
            for i,N in enumerate(TIMECUTS):
                exi, pnl_raw = exit_timecut(c,h,l,avg,last,ex,side,cstop,N)
                d[f"pnl{i}"]=pnl_raw-2*FEE; d[f"exit{i}"]=ts[exi]
            rows.append(d)
        if loaded%40==0: print(f"  [{'pump' if side>0 else 'dump'}] loaded {loaded}", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)

if __name__=="__main__":
    print(f"building time-cut sweep N={TIMECUTS} (checkpoint at 240-N) ...", flush=True)
    for side,name in ((+1,"pump"),(-1,"dump")):
        X=build_leg(side)
        X.to_parquet(f"notebooks/pump_dump_combined/_out/{name}_timecut.parquet")
        print(f"\n{name}: {len(X)} raw events. raw per-trade by N:", flush=True)
        for i,N in enumerate(TIMECUTS):
            p=X[f"pnl{i}"]
            print(f"  N={N:>3} (chk@{H-N:>3}m): mean {p.mean()*100:+.2f}%  median {p.median()*100:+.2f}%  "
                  f"win {(p>0).mean()*100:.0f}%  std {p.std()*100:.1f}%  worst {p.min()*100:.0f}%", flush=True)
        print(f"cached -> _out/{name}_timecut.parquet", flush=True)
