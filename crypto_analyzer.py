#!/usr/bin/env python3
"""
Kripto Grafik Analiz Botu (sadece analiz - emir GONDERMEZ)

Ne yapar:
  1) 15m / 1s / 4s / 1g mumlarini ceker (Binance public API)
  2) Kritik saat dilimleri: seans (Asya/Londra/NY), funding, gunluk kapanis, ABD acilisi,
     saat bazli hacim/oynaklik profili, ince likidite uyarisi
  3) FOMO skoru (RSI asiriligi, EMA'dan uzaklasma, hacim patlamasi, ardisik mum, funding)
  4) Sahte hacim / wash-trading skoru (hacim var fiyat yok, islem boyutu anomalisi,
     hacim-oynaklik korelasyonu, Benford, taker orani)
  5) Manipulasyon skoru (pump&dump, stop-avi igneleri, hacimsiz sicrama, emir defteri duvari)
  6) Destek/direnc kumeleri + trend yonu -> giris bolgesi, stop, hedef, R/R, karar

Kurulum:  pip install pandas numpy requests
Kullanim:
  python crypto_analyzer.py BTCUSDT
  python crypto_analyzer.py ETHUSDT --tz Europe/Istanbul --book
  python crypto_analyzer.py SOLUSDT --watch 300      # 5 dk'da bir tekrar
  python crypto_analyzer.py BTCUSDT --demo           # internetsiz test verisi
  python crypto_analyzer.py BTCUSDT --json
Binance erisilemiyorsa:  --base https://api.binance.us   (ABD)

UYARI: Egitim/analiz amaclidir, yatirim tavsiyesi degildir. Skorlar olasiliksal
sezgilerdir; kesin manipulasyon kaniti degildir. Her zaman stop kullanin.
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

TF_LIMITS = {"15m": 500, "1h": 1000, "4h": 500, "1d": 300}
TF_MIN = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_vol", "trades", "taker_buy", "taker_buy_quote", "ignore"]


# --------------------------------------------------------------------------- veri
def fetch(symbol, interval, limit, base):
    r = requests.get(f"{base}/api/v3/klines",
                     params=dict(symbol=symbol, interval=interval, limit=limit), timeout=15)
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=COLS)
    for c in ["open", "high", "low", "close", "volume", "trades", "taker_buy"]:
        df[c] = pd.to_numeric(df[c])
    df.index = pd.to_datetime(df["open_time"], unit="ms")
    return df[["open", "high", "low", "close", "volume", "trades", "taker_buy"]]


def funding_rate(symbol):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                         params={"symbol": symbol}, timeout=8)
        return float(r.json()["lastFundingRate"])
    except Exception:
        return None


def synth(interval, n, seed=7):
    """Test verisi: sonda pump + sahte hacim + stop-avi igneleri enjekte edilir."""
    mins = TF_MIN[interval]
    rng = np.random.default_rng(seed + mins)
    end = pd.Timestamp.now("UTC").tz_localize(None).floor(f"{mins}min")
    idx = pd.date_range(end=end, periods=n, freq=f"{mins}min")
    ret = rng.normal(0.0001, 0.004 * math.sqrt(mins / 60), n)
    vol = rng.lognormal(5, 0.4, n) * (1 + 0.5 * np.sin(2 * np.pi * np.asarray(idx.hour) / 24))
    avg_sz = rng.lognormal(0, 0.2, n)
    taker = np.clip(rng.normal(0.5, 0.06, n), 0.2, 0.8)
    ret[-8:] += 0.011
    vol[-8:] *= 4
    taker[-8:] = 0.68
    for i in rng.choice(np.arange(n - 60, n - 10), 6, replace=False):
        vol[i] *= 9
        ret[i] = rng.normal(0, 1e-4)
        avg_sz[i] *= 6
        taker[i] = 0.5
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0015, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0015, n)))
    lo[-15] = close[-15] * 0.965   # stop avi igne
    hi[-20] = close[-20] * 1.04
    return pd.DataFrame(dict(open=open_, high=hi, low=lo, close=close, volume=vol,
                             trades=vol / avg_sz, taker_buy=vol * taker), index=idx)


# --------------------------------------------------------------------- gostergeler
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    ru = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rd = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + ru / rd.replace(0, np.nan))).fillna(100)


def atr(d, n=14):
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def zs(s, win=50):
    return (s - s.rolling(win).mean()) / s.rolling(win).std()


# --------------------------------------------------------------- kritik saatler
SESSIONS = {"Asya": (0, 8), "Londra": (7, 16), "New York": (13, 22)}
CRITICAL = [("Funding + gunluk mum kapanisi", 0, 0), ("Londra acilisi", 7, 0),
            ("Funding", 8, 0), ("ABD veri/vadeli acilis", 13, 30),
            ("Funding", 16, 0), ("ABD borsa kapanisi", 20, 0)]


def time_report(d1h, tzname):
    tz = ZoneInfo(tzname)
    now = datetime.now(timezone.utc)
    off = now.astimezone(tz).utcoffset().total_seconds() / 3600
    loc = lambda h: f"{int((h + off) % 24):02d}:00"
    d = d1h.copy()
    d["hour"] = d.index.hour
    d["rng"] = (d.high - d.low) / d.close
    prof = d.groupby("hour").agg(vol=("volume", "mean"), rng=("rng", "mean"))
    prof["share"] = prof.vol / prof.vol.sum() * 100
    top = prof.sort_values("rng", ascending=False).head(3)
    cur_sessions = [n for n, (a, b) in SESSIONS.items() if a <= now.hour < b] or ["Sakin saat"]
    # su anki saatin hacmi, ayni saatin ortalamasina gore
    same = d[d.hour == now.hour].volume
    ratio = float(d.volume.iloc[-1] / same.iloc[:-1].mean()) if len(same) > 3 else float("nan")
    nxt = []
    for name, h, m in CRITICAL:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t = t + pd.Timedelta(days=1)
        mins = (t - now).total_seconds() / 60
        nxt.append((mins, name, (t.astimezone(tz)).strftime("%H:%M")))
    nxt.sort()
    warns = []
    if nxt[0][0] <= 45:
        warns.append(f"{nxt[0][1]} {int(nxt[0][0])} dk sonra ({nxt[0][2]}) - volatilite artabilir, "
                     "yeni giris icin mum kapanisini bekle")
    if now.weekday() >= 5:
        warns.append("Hafta sonu: likidite ince, igne/manipulasyon riski yuksek")
    if not np.isnan(ratio) and ratio < 0.5:
        warns.append(f"Bu saatin hacmi normalin %{ratio * 100:.0f}'i - ince likidite")
    return dict(sessions=cur_sessions, vol_ratio=ratio, next=nxt[:3], warns=warns,
                hot=[(loc(h), float(r.rng * 100), float(r.share)) for h, r in top.iterrows()],
                local_now=now.astimezone(tz).strftime("%H:%M"))


# ----------------------------------------------------------------------- FOMO
def fomo_score(d1h, d4h, fr):
    s, why = 0, []
    up = d1h.close.iloc[-1] >= d1h.close.iloc[-7]
    sign = 1 if up else -1
    for nm, d in (("1s", d1h), ("4s", d4h)):
        r = rsi(d.close).iloc[-1]
        r = r if up else 100 - r
        if r >= 80:
            s += 15; why.append(f"RSI {nm} asiri ({r:.0f})")
        elif r >= 70:
            s += 8; why.append(f"RSI {nm} yuksek ({r:.0f})")
    ext = sign * (d1h.close.iloc[-1] - ema(d1h.close, 50).iloc[-1]) / atr(d1h).iloc[-1]
    if ext > 4:
        s += 22; why.append(f"Fiyat EMA50'den {ext:.1f} ATR uzak")
    elif ext > 2.5:
        s += 11; why.append(f"Fiyat EMA50'den {ext:.1f} ATR uzak")
    vz = zs(d1h.volume).tail(3).max()
    if vz > 2.5:
        s += 20; why.append(f"Hacim patlamasi (z={vz:.1f})")
    streak = 0
    for o, c in zip(d1h.open.iloc[::-1], d1h.close.iloc[::-1]):
        if (c > o) == up:
            streak += 1
        else:
            break
    if streak >= 5:
        s += 12; why.append(f"{streak} ardisik {'yesil' if up else 'kirmizi'} mum")
    if fr is not None and sign * fr > 0.0002:
        s += 15 if sign * fr > 0.0005 else 7
        why.append(f"Funding asiri ({fr * 100:.3f}%) - kalabalik ayni yonde")
    last = d1h.tail(3)
    wick = ((last.high - last[["open", "close"]].max(axis=1)) if up
            else (last[["open", "close"]].min(axis=1) - last.low)) / (last.high - last.low + 1e-12)
    if wick.mean() > 0.4 and ext > 2:
        s += 8; why.append("Zirvede uzun igneler (tukenme)")
    return min(100, s), ("YUKARI (FOMO)" if up else "ASAGI (panik)"), why


# ------------------------------------------------------------------ sahte hacim
def benford_chi2(v):
    v = v[v > 0]
    if len(v) < 200:
        return None
    first = np.array([int(str(f"{x:.10e}")[0]) for x in v])
    obs = np.array([(first == k).sum() for k in range(1, 10)])
    exp = len(v) * np.log10(1 + 1 / np.arange(1, 10))
    return float(((obs - exp) ** 2 / exp).sum())   # >20.1 ~ p<0.01


def fake_volume_score(d):
    d = d.tail(500)
    s, why = 0, []
    a = atr(d)
    eff = (d.close - d.open).abs() / a
    vz = zs(d.volume)
    sus = int(((vz > 2.5) & (eff < 0.35)).tail(48).sum())
    if sus >= 3:
        s += 30; why.append(f"Son 48 mumda {sus} kez 'hacim var, fiyat yok'")
    elif sus >= 1:
        s += 12; why.append(f"Son 48 mumda {sus} kez 'hacim var, fiyat yok'")
    sz = d.volume / d.trades.replace(0, np.nan)
    z = ((sz.tail(24) - sz.iloc[-224:-24].mean()) / sz.iloc[-224:-24].std()).max()
    if z > 3:
        s += 20; why.append(f"Ortalama islem boyutu anormal buyuk (z={z:.1f}) - tek elden hacim")
    c = d.volume.tail(200).corr((d.high - d.low).tail(200))
    if c < 0.25:
        s += 15; why.append(f"Hacim-oynaklik korelasyonu dusuk ({c:.2f})")
    bf = benford_chi2(d.volume.values)
    if bf and bf > 20.1:
        s += 10; why.append(f"Hacim dagilimi Benford'a uymuyor (chi2={bf:.0f})")
    tk = (d.taker_buy / d.volume)
    spike = d[(vz > 2.5)]
    if len(spike) >= 3 and (tk.loc[spike.index].sub(0.5).abs() < 0.02).mean() > 0.5:
        s += 15; why.append("Hacim patlamalarinda alici/satici orani tam %50 (karsilikli islem suphesi)")
    return min(100, s), why


# --------------------------------------------------------------- manipulasyon
def spoof_check(symbol, base, wait=4):
    def book():
        r = requests.get(f"{base}/api/v3/depth", params={"symbol": symbol, "limit": 200}, timeout=10).json()
        return ({float(p): float(q) for p, q in r["bids"]}, {float(p): float(q) for p, q in r["asks"]})
    b1, a1 = book(); time.sleep(wait); b2, a2 = book()
    mid = (max(b2) + min(a2)) / 2
    med = np.median(list(b1.values()) + list(a1.values()))
    walls = vanished = 0
    for s1, s2 in ((b1, b2), (a1, a2)):
        for p, q in s1.items():
            if q > 8 * med and abs(p - mid) / mid < 0.01:
                walls += 1
                if s2.get(p, 0) < 0.2 * q:
                    vanished += 1
    bq = sum(q for p, q in b2.items() if p > mid * 0.99)
    aq = sum(q for p, q in a2.items() if p < mid * 1.01)
    return dict(walls=walls, vanished=vanished, imbalance=bq / aq if aq else None)


def manipulation_score(d1h, ob=None):
    s, why = 0, []
    x = d1h.tail(30).reset_index(drop=True)
    a = atr(d1h).iloc[-1]
    p = int(x.high.iloc[-24:].idxmax())
    lo_i = max(0, p - 6)
    trough = x.low.iloc[lo_i:p + 1].min()
    peak = x.high.iloc[p]
    rise = (peak - trough) / a
    if rise > 6:
        after = x.close.iloc[p:].min()
        drop = (peak - after) / (peak - trough)
        if drop > 0.5:
            s += 40; why.append(f"Pump & dump izi: {rise:.1f} ATR cikis, %{drop * 100:.0f} geri verildi")
        elif p >= len(x) - 4:
            s += 20; why.append(f"Sert pump ({rise:.1f} ATR) tepede - dagitim/dump riski")
    bod = (x.close - x.open).abs()
    uw = x.high - x[["open", "close"]].max(axis=1)
    lw = x[["open", "close"]].min(axis=1) - x.low
    hunts = int((((uw > 2 * bod) & (uw > 1.5 * a)) | ((lw > 2 * bod) & (lw > 1.5 * a))).tail(24).sum())
    if hunts:
        s += min(30, 10 * hunts); why.append(f"{hunts} adet stop-avi tarzi uzun igne (son 24 saat)")
    mv = (d1h.close.diff().abs() / a).tail(6)
    vzr = zs(d1h.volume).tail(6)
    if ((mv > 3) & (vzr < 0)).any():
        s += 20; why.append("Dusuk hacimle sert fiyat sicramasi (ince emir defteri oyunu)")
    if ob:
        if ob["vanished"] >= 2:
            s += 25; why.append(f"{ob['vanished']} buyuk emir duvari islem gormeden kayboldu (olasi spoofing)")
        if ob["imbalance"] and (ob["imbalance"] > 3 or ob["imbalance"] < 0.33):
            s += 8; why.append(f"Emir defteri dengesiz (alis/satis={ob['imbalance']:.1f})")
    return min(100, s), why


# ------------------------------------------------------------ seviye + plan
def pivots(d, w):
    hi, lo = d.high, d.low
    ph = hi[hi == hi.rolling(2 * w + 1, center=True).max()].values
    pl = lo[lo == lo.rolling(2 * w + 1, center=True).min()].values
    return list(ph) + list(pl)


def cluster(levels, tol):
    out = []
    for l in sorted(levels):
        if out and abs(np.mean(out[-1]) - l) <= tol:
            out[-1].append(l)
        else:
            out.append([l])
    return [(float(np.mean(g)), len(g)) for g in out]


def build_plan(d15, d1h, d4h, d1d, fomo, fake, manip):
    price = float(d1h.close.iloc[-1])
    a = float(atr(d1h).iloc[-1])
    a4 = float(atr(d4h).iloc[-1])
    e50, e200 = ema(d4h.close, 50).iloc[-1], ema(d4h.close, 200).iloc[-1]
    e50h = ema(d1h.close, 50).iloc[-1]
    if price > e200 and e50 > e200 and price > e50h * 0.99:
        bias = "LONG"
    elif price < e200 and e50 < e200 and price < e50h * 1.01:
        bias = "SHORT"
    else:
        bias = "YATAY"
    lv = cluster(pivots(d4h, 4) + pivots(d1d, 3) + pivots(d1h.tail(300), 6), 0.4 * a4)
    sup = [(l, n) for l, n in lv if l < price]
    res = [(l, n) for l, n in lv if l > price]
    plan = dict(bias=bias, price=price, atr=a, supports=sorted(sup, reverse=True)[:3],
                resistances=sorted(res)[:3])
    long_ = bias != "SHORT"
    pool = [(l, n) for l, n in (sup if long_ else res) if abs(price - l) <= 4 * a]
    if pool:
        lvl = max(pool, key=lambda t: t[1] / (1 + abs(price - t[0]) / a))[0]
    else:
        lvl = float(e50h)
    if long_:
        zone = (lvl - 0.2 * a, lvl + 0.4 * a)
        stop = lvl - 0.8 * a
        entry = sum(zone) / 2
        tg = [l for l, _ in sorted(res)][:2]
        tg = (tg + [entry + 2 * (entry - stop), entry + 3 * (entry - stop)])[:2] if len(tg) < 2 else tg
        in_zone = stop < price <= zone[1]
        r15 = rsi(d15.close).iloc[-1]
        trig = d15.close.iloc[-1] > ema(d15.close, 21).iloc[-1] and r15 > 45 and d15.close.iloc[-1] > d15.open.iloc[-1]
    else:
        zone = (lvl - 0.4 * a, lvl + 0.2 * a)
        stop = lvl + 0.8 * a
        entry = sum(zone) / 2
        tg = [l for l, _ in sorted(sup, reverse=True)][:2]
        tg = (tg + [entry - 2 * (stop - entry), entry - 3 * (stop - entry)])[:2] if len(tg) < 2 else tg
        in_zone = zone[0] <= price < stop
        r15 = rsi(d15.close).iloc[-1]
        trig = d15.close.iloc[-1] < ema(d15.close, 21).iloc[-1] and r15 < 55 and d15.close.iloc[-1] < d15.open.iloc[-1]
    risk = abs(entry - stop)
    rr = abs(tg[0] - entry) / risk if risk else 0
    plan.update(zone=zone, stop=stop, targets=tg, rr=rr, in_zone=bool(in_zone), trigger=bool(trig),
                dist_pct=(entry / price - 1) * 100)
    # karar
    if manip >= 60 or fake >= 60:
        v = "GIRME - manipulasyon / sahte hacim suphesi yuksek"
    elif fomo >= 60:
        v = "BEKLE - FOMO yuksek, geri cekilme bolgesini bekle (kovalama)"
    elif bias == "YATAY":
        v = "BEKLE - net trend yok, destek/direnc kenarlarinda islem ara"
    elif rr < 1.5:
        v = f"BEKLE - risk/odul yetersiz ({rr:.1f})"
    elif in_zone and trig:
        v = f"{bias} GIRIS ADAYI - bolgede ve 15dk tetik onayli"
    elif in_zone:
        v = f"{bias} bolgesinde - 15dk onay mumunu bekle"
    else:
        v = f"{bias} PLAN - fiyat bolgeye gelince alarm kur ({plan['dist_pct']:+.1f}%)"
    plan["verdict"] = v
    return plan


# ------------------------------------------------------------------------ main
def bar(x):
    return "#" * int(x / 5) + "." * (20 - int(x / 5)) + f" {x:.0f}/100"


def run(args):
    sym = args.symbol.upper()
    data = {tf: (synth(tf, n) if args.demo else fetch(sym, tf, n, args.base)) for tf, n in TF_LIMITS.items()}
    fr = None if args.demo else funding_rate(sym)
    ob = None
    if args.book and not args.demo:
        try:
            ob = spoof_check(sym, args.base)
        except Exception as e:
            print("Emir defteri alinamadi:", e, file=sys.stderr)
    tr = time_report(data["1h"], args.tz)
    f, fdir, fw = fomo_score(data["1h"], data["4h"], fr)
    k, kw = fake_volume_score(data["1h"])
    m, mw = manipulation_score(data["1h"], ob)
    p = build_plan(data["15m"], data["1h"], data["4h"], data["1d"], f, k, m)
    if args.json:
        print(json.dumps(dict(symbol=sym, fomo=f, fake_volume=k, manipulation=m, time=tr, plan=p),
                         default=str, ensure_ascii=False, indent=2))
        return
    print(f"\n=== {sym} | {tr['local_now']} ({args.tz}) | fiyat {p['price']:.4f} ===")
    print(f"Seans: {', '.join(tr['sessions'])} | hacim/normal: "
          f"{'-' if np.isnan(tr['vol_ratio']) else f'{tr['vol_ratio']:.2f}x'}")
    print("En oynak saatler:", ", ".join(f"{h} (%{r:.2f})" for h, r, _ in tr["hot"]))
    print("Siradaki kritik:", "; ".join(f"{n} {t} ({int(m_)}dk)" for m_, n, t in tr["next"]))
    for w in tr["warns"]:
        print("  ! " + w)
    for title, sc, ws, extra in (("FOMO", f, fw, f" yon: {fdir}"), ("SAHTE HACIM", k, kw, ""),
                                 ("MANIPULASYON", m, mw, "")):
        print(f"\n{title:<13}{bar(sc)}{extra}")
        for w in ws or ["belirgin sinyal yok"]:
            print("  - " + w)
    print(f"\nTREND: {p['bias']} | ATR(1s): {p['atr']:.4f}")
    print("Destek :", ", ".join(f"{l:.4f}(x{n})" for l, n in p["supports"]) or "-")
    print("Direnc :", ", ".join(f"{l:.4f}(x{n})" for l, n in p["resistances"]) or "-")
    print(f"Giris bolgesi: {p['zone'][0]:.4f} - {p['zone'][1]:.4f} | Stop: {p['stop']:.4f}")
    print(f"Hedefler: {p['targets'][0]:.4f} / {p['targets'][1]:.4f} | R/R(T1): {p['rr']:.1f}")
    print(f"\n>>> KARAR: {p['verdict']}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tz", default="Europe/Istanbul")
    ap.add_argument("--base", default="https://api.binance.com")
    ap.add_argument("--book", action="store_true", help="emir defteri spoofing kontrolu (+4 sn)")
    ap.add_argument("--watch", type=int, default=0, help="N saniyede bir tekrarla")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    while True:
        try:
            run(args)
        except Exception as e:
            print("Hata:", e, file=sys.stderr)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
