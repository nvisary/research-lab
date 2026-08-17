"""Rollover (trailing take-profit) exit sweep — the causal alternative to the
time-cut (nb13). Instead of "still red at minute 240-N -> cut" (which lost, because
being underwater early is normal for a reversion trade), this exits when the move
IN OUR FAVOR rolls over: track the best favorable MTM since entry; once the trade
has been green (peak > ARM), exit on the first bar where MTM falls back by `g` from
that running peak. g=inf => hold to the 240-min horizon = baseline (= nb12 / nb13 N=0).

The catastrophe stop (pump 30% on intrabar high, dump 20% on intrabar low, gap-fill
+ slip) is always on and can fire first. Rollover only TAKES PROFIT (armed after the
trade goes green); it never cuts a never-green loser early — that's deliberate, it
is a different mechanism from the time-cut.

Faithful to _build_timecut_sweep.py in entries / scale-in / horizon / costs. Also
records peak favorable MTM and the minute it occurs (tpeak) for the diagnostic
'does the reversion peak early then decay?'. Writes two parquets (features differ):
    _out/pump_rollover.parquet  _out/dump_rollover.parquet
"""
import sys; sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

WIN=15; H=240; COOLDOWN=10; DAY=1440; FEE=0.00075
THR_P=0.05; THR_D=0.07; VOL_MULT=3.0; DELTA=0.02
CSTOP_P=0.30; CSTOP_D=0.20
SLIP=0.001
ARM=0.0                                          # arm rollover once peak MTM > 0 (been green)
GIVEBACKS=[0.005,0.01,0.02,0.03,0.05,0.08,np.inf]   # last = no rollover (hold to horizon)

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

def exit_rollover(c, h, l, avg, en, ex, side, cstop, g):
    """First of: catastrophe stop / rollover (give back g from peak favorable MTM,
    armed once peak>ARM) / horizon. Returns (exit_idx, pnl_pre_cost)."""
    lo, hi = en+1, ex
    peak=-1e9
    for t in range(lo, hi+1):
        if side>0:
            if h[t]/avg-1 >= cstop: return t, min(-cstop, -(c[t]/avg-1)) - SLIP
        else:
            if l[t]/avg-1 <= -cstop: return t, min(-cstop, (c[t]/avg-1)) - SLIP
        mtm = -(c[t]/avg-1) if side>0 else (c[t]/avg-1)
        if mtm>peak: peak=mtm
        if np.isfinite(g) and peak>ARM and mtm <= peak-g:
            return t, mtm - SLIP
    raw=c[hi]/avg-1
    return hi, (-raw if side>0 else raw)

def peak_diag(c, avg, en, ex, side):
    """peak favorable MTM and the minute (from entry+1) at which it occurs."""
    lo,hi=en+1,ex
    mtm = (-(c[lo:hi+1]/avg-1)) if side>0 else (c[lo:hi+1]/avg-1)
    return float(mtm.max()), int(mtm.argmax())

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
            pk,tpk=peak_diag(c,avg,last,ex,side)
            d=dict(sym=sym, entry=ts[st+1], liq=liq, stream=("pump" if side>0 else "dump"),
                   peakmtm=pk, tpeak=tpk)
            d.update({names[i]:feats[i] for i in range(len(names))})
            for i,g in enumerate(GIVEBACKS):
                exi, pnl_raw = exit_rollover(c,h,l,avg,last,ex,side,cstop,g)
                d[f"pnl{i}"]=pnl_raw-2*FEE; d[f"exit{i}"]=ts[exi]
            rows.append(d)
        if loaded%40==0: print(f"  [{'pump' if side>0 else 'dump'}] loaded {loaded}", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)

if __name__=="__main__":
    print(f"building rollover sweep g={GIVEBACKS} (arm>{ARM}) ...", flush=True)
    for side,name in ((+1,"pump"),(-1,"dump")):
        X=build_leg(side)
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{name}_signals.parquet")
        print(f"\n{name}: {len(X)} raw events. peak MTM median {X.peakmtm.median()*100:+.2f}%  "
              f"tpeak median {X.tpeak.median():.0f}m (of 240).  raw per-trade by giveback:", flush=True)
        for i,g in enumerate(GIVEBACKS):
            p=X[f"pnl{i}"]; lab=("hold" if not np.isfinite(g) else f"{g:.1%}")
            print(f"  g={lab:>5}: mean {p.mean()*100:+.2f}%  median {p.median()*100:+.2f}%  "
                  f"win {(p>0).mean()*100:.0f}%  std {p.std()*100:.1f}%  worst {p.min()*100:.0f}%", flush=True)
        print(f"cached -> _out/{name}_signals.parquet", flush=True)
