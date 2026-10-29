"""Backtest migliorie tennis e NBA (NON modifica l'app).

Regola onesta: i parametri nuovi si tarano sul primo anno (fino al 28/10/2025)
e si giudicano sull'ultimo anno (29/10/2025 -> oggi), mai visto in taratura.

Parte A (dati ESPN già in cache):
  NBA    : correzione favoritissimi, stanchezza/riposo, peso Elo vs punti
  Tennis : stanchezza, peso del torneo, margine nei set
Parte B (solo se la rete lo permette, es. GitHub Actions):
  Tennis : confronto con quote Bet365 storiche di tennis-data.co.uk
  NBA    : quote storiche dal riepilogo partita ESPN

Uso: cd engine && python ../experiments/backtest_sport.py [--no-net]
"""
import datetime as dt
import io
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import sports as S  # noqa: E402

OUT = Path(__file__).resolve().parent
CUT = "2025-10-29"          # da qui in poi: periodo di giudizio
TUNE_FROM = "2025-02-01"    # prima l'Elo è ancora "giovane"
NET = "--no-net" not in sys.argv


def sig(x):
    return 1 / (1 + np.exp(-x))


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def metr(p, y):
    """p = prob. evento, y = 1 se accaduto."""
    p, y = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6), np.asarray(y, float)
    return {"n": int(len(p)), "acc": float(((p > .5) == (y == 1)).mean()),
            "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
            "brier": float(((p - y) ** 2).mean())}


def bands(p, y, edges=(.5, .6, .7, .8, .9, 1.01)):
    """Calibrazione dal punto di vista del favorito."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    pf = np.where(p >= .5, p, 1 - p)
    yf = np.where(p >= .5, y, 1 - y)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (pf >= lo) & (pf < hi)
        if m.sum() >= 15:
            rows.append((f"{lo*100:.0f}-{min(hi,1)*100:.0f}%", int(m.sum()), float(pf[m].mean()), float(yf[m].mean())))
    return rows


def fit_logistic(X, y, l2=1e-3):
    """Regressione logistica semplice (niente sklearn)."""
    X, y = np.asarray(X, float), np.asarray(y, float)

    def f(b):
        z = X @ b
        return np.sum(np.logaddexp(0, z) - y * z) / len(y) + l2 * np.sum(b[1:] ** 2)

    def g(b):
        r = sig(X @ b) - y
        gr = X.T @ r / len(y)
        gr[1:] += 2 * l2 * b[1:]
        return gr
    b0 = np.zeros(X.shape[1])
    b0[0] = 1.0
    return minimize(f, b0, jac=g, method="L-BFGS-B").x


# ===================================================================== NBA
def nba_preds(w_elo=0.5, sd=12.5, home=70.0, k=20.0, mov=True):
    db = S._load("NBA")
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    done = [m for m in ms if m["done"] and (m["hs"] or m["as"])]
    elo, last, recs = {}, {}, []
    rat, rat_day = None, None
    # ratings punti (come l'app): medie esponenziali ultime 40 partite, ricalcolo giornaliero
    tgames = {}

    def ratings(i):
        rec = done[max(0, i - 1400):i]
        if len(rec) < 100:
            return None
        L = np.mean([m["hs"] + m["as"] for m in rec]) / 2
        per = {}
        for m in rec:
            per.setdefault(m["home"], []).append((m["hs"], m["as"]))
            per.setdefault(m["away"], []).append((m["as"], m["hs"]))
        o, d = {}, {}
        for t, gs in per.items():
            gs = gs[-40:]
            w = 0.95 ** np.arange(len(gs) - 1, -1, -1)
            a = np.array(gs)
            o[t] = (a[:, 0] * w).sum() / w.sum() / L
            d[t] = (a[:, 1] * w).sum() / w.sum() / L
        return L, o, d

    for i, m in enumerate(done):
        if rat_day is None or m["date"] > rat_day:
            rat, rat_day = ratings(i), m["date"]
        eh, ea = elo.get(m["home"], 1500), elo.get(m["away"], 1500)
        p_elo = 1 / (1 + 10 ** ((ea - eh - home) / 400))
        if rat:
            L, o, d = rat
            xh = L * o.get(m["home"], 1) * d.get(m["away"], 1) + 1.5
            xa = L * o.get(m["away"], 1) * d.get(m["home"], 1) - 1.5
        else:
            xh, xa = 114.0, 112.0
        p_pts = 1 - norm.cdf(0, xh - xa, sd)
        p = w_elo * p_elo + (1 - w_elo) * p_pts
        dd = dt.date.fromisoformat(m["date"])
        rh = (dd - last[m["home"]]).days if m["home"] in last else 7
        ra = (dd - last[m["away"]]).days if m["away"] in last else 7
        # partite negli ultimi 7 giorni
        n7h = sum(1 for x in tgames.get(m["home"], []) if (dd - x).days <= 7)
        n7a = sum(1 for x in tgames.get(m["away"], []) if (dd - x).days <= 7)
        if rat:
            recs.append({"date": m["date"], "id": m["id"], "home": m["home"], "away": m["away"],
                         "p": p, "p_elo": p_elo, "p_pts": p_pts, "y": 1 if m["hs"] > m["as"] else 0,
                         "rh": min(rh, 5), "ra": min(ra, 5), "n7h": n7h, "n7a": n7a,
                         "diff": m["hs"] - m["as"], "odds": m.get("odds")})
        last[m["home"]] = last[m["away"]] = dd
        tgames.setdefault(m["home"], []).append(dd)
        tgames.setdefault(m["away"], []).append(dd)
        movv = abs(m["hs"] - m["as"])
        exp = p_elo
        res = 1.0 if m["hs"] > m["as"] else 0.0
        mult = ((movv + 3) ** 0.8) / (7.5 + 0.006 * (eh - ea if res else ea - eh)) if mov else 1.0
        elo[m["home"]] = eh + k * mult * (res - exp)
        elo[m["away"]] = ea - k * mult * (res - exp)
    return recs


def nba_features(r):
    b2b_h = 1.0 if r["rh"] == 1 else 0.0
    b2b_a = 1.0 if r["ra"] == 1 else 0.0
    return [logit(r["p"]), b2b_h - b2b_a, (min(r["rh"], 3) - min(r["ra"], 3)), r["n7h"] - r["n7a"]]


def nba_section(odds_extra=None):
    lines = ["## 🏀 NBA", ""]
    base = nba_preds()
    tune = [r for r in base if r["date"] < CUT]
    test = [r for r in base if r["date"] >= CUT]
    yt = np.array([r["y"] for r in test])
    res = {}
    res["App di oggi"] = np.array([r["p"] for r in test])

    # 1) correzione favoritissimi: p' = sig(a*logit p + c)
    X = np.array([[logit(r["p"]), 1.0] for r in tune])
    X[:, [0, 1]] = X[:, [0, 1]]
    b = fit_logistic(X, [r["y"] for r in tune], l2=0)
    a_cal, c_cal = b
    res["+ correzione favoriti"] = sig(a_cal * logit([r["p"] for r in test]) + c_cal)

    # 2a) + solo back-to-back
    Xb = np.array([[logit(r["p"]), nba_features(r)[1], 1.0] for r in tune])
    bb = fit_logistic(Xb, [r["y"] for r in tune], l2=1e-4)
    res["+ correzione + back-to-back"] = sig(np.array([[logit(r["p"]), nba_features(r)[1], 1.0] for r in test]) @ bb)

    # 2) + stanchezza/riposo
    Xf = np.array([nba_features(r) + [1.0] for r in tune])
    bf = fit_logistic(Xf, [r["y"] for r in tune], l2=1e-4)
    res["+ correzione + riposo/back-to-back"] = sig(np.array([nba_features(r) + [1.0] for r in test]) @ bf)

    # 3) peso Elo vs punti (scelto sulla taratura) + correzione + riposo
    best = None
    for w in (0.2, 0.35, 0.5, 0.65, 0.8, 1.0):
        rr = nba_preds(w_elo=w) if w != 0.5 else base
        tu = [r for r in rr if r["date"] < CUT]
        Xw = np.array([nba_features(r) + [1.0] for r in tu])
        bw = fit_logistic(Xw, [r["y"] for r in tu], l2=1e-4)
        ll = metr(sig(Xw @ bw), [r["y"] for r in tu])["logloss"]
        if best is None or ll < best[0]:
            best = (ll, w, rr, bw)
    _, w_best, rr, bw = best
    te = [r for r in rr if r["date"] >= CUT]
    if w_best != 0.5:
        res[f"+ peso Elo {w_best:.2f} (scelto in taratura)"] = sig(np.array([nba_features(r) + [1.0] for r in te]) @ bw)

    # 4) Elo senza margine di vittoria (per vedere quanto conta, l'app lo usa già)
    nm = nba_preds(mov=False)
    res["(controllo) Elo SENZA margine di vittoria"] = np.array([r["p"] for r in nm if r["date"] >= CUT])

    lines += [f"Taratura: {TUNE_FROM} → {CUT} ({len(tune)} partite). Giudizio: dal {CUT} ({len(test)} partite).", "",
              "| Variante | Vincente indovinato | LogLoss (più basso = meglio) | Brier |", "|---|---|---|---|"]
    for k, p in res.items():
        mm = metr(p, yt if len(p) == len(yt) else [r["y"] for r in nm if r["date"] >= CUT])
        lines.append(f"| {k} | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | {mm['brier']:.4f} |")
    lines += ["", f"Coefficienti correzione: a={a_cal:.3f}, c={c_cal:+.3f} (a<1 = l'app era troppo sicura).",
              f"Coefficienti riposo: back-to-back {bf[1]:+.3f}, giorni riposo {bf[2]:+.3f}, partite in 7gg {bf[3]:+.3f} "
              f"(in logit; 0,1 ≈ 2,5 punti percentuali vicino al 50%)", ""]
    best_key = min(res, key=lambda k: metr(res[k], yt)["logloss"] if len(res[k]) == len(yt) else 9)
    for k in ("App di oggi", best_key):
        lines += [f"**Calibrazione — {k}** (favorito)", "", "| Fascia | Partite | Detto | Uscito |", "|---|---|---|---|"]
        for b_, n, d, u in bands(res[k], yt):
            lines.append(f"| {b_} | {n} | {d*100:.0f}% | {u*100:.0f}% |")
        lines.append("")
    # back-to-back: quanto vincono davvero
    b2b = [r for r in test if r["rh"] == 1 and r["ra"] > 1]
    b2a = [r for r in test if r["ra"] == 1 and r["rh"] > 1]
    if b2b and b2a:
        lines += [f"Fatti grezzi (periodo di giudizio): casa in back-to-back vs ospite riposato → casa vince {np.mean([r['y'] for r in b2b])*100:.0f}% "
                  f"(modello diceva {np.mean([r['p'] for r in b2b])*100:.0f}%, {len(b2b)} partite); "
                  f"ospite in back-to-back vs casa riposata → casa vince {np.mean([r['y'] for r in b2a])*100:.0f}% "
                  f"(modello {np.mean([r['p'] for r in b2a])*100:.0f}%, {len(b2a)} partite).", ""]

    # schedina tipo app: favoriti sopra soglia
    lines += ["**Favoriti sopra soglia (giudizio)**", "", "| Soglia | Variante | Partite | Detto | Uscito |", "|---|---|---|---|---|"]
    for th in (0.7, 0.8):
        for k in ("App di oggi", best_key):
            p = res[k]
            pf = np.where(p >= .5, p, 1 - p)
            yf = np.where(p >= .5, yt, 1 - yt)
            m = pf >= th
            if m.sum():
                lines.append(f"| {th*100:.0f}% | {k} | {int(m.sum())} | {pf[m].mean()*100:.0f}% | {yf[m].mean()*100:.0f}% |")
    lines.append("")
    if odds_extra:
        lines += nba_vs_book(base, odds_extra, res, test)
    return lines, {"a": a_cal, "c": c_cal, "b2b": bb.tolist(), "rest": bf.tolist(), "w": w_best}


def nba_vs_book(base, odds, res, test):
    lines = ["**Contro il bookmaker (quote storiche ESPN)**", ""]
    idx = [i for i, r in enumerate(test) if odds.get(r["id"]) and odds[r["id"]].get("h") and odds[r["id"]].get("a")]
    if len(idx) < 100:
        return lines + [f"Quote trovate solo per {len(idx)} partite: troppo poche per un giudizio.", ""]
    y = np.array([test[i]["y"] for i in idx])
    qh = np.array([odds[test[i]["id"]]["h"] for i in idx])
    qa = np.array([odds[test[i]["id"]]["a"] for i in idx])
    pb = (1 / qh) / (1 / qh + 1 / qa)
    lines += [f"Partite con quote: {len(idx)} — fonte: {odds.get('_fonte', 'ESPN')}", "",
              "| Chi | Vincente | LogLoss | ROI puntando il nostro favorito | ROI solo dove vediamo valore (>5%) |", "|---|---|---|---|---|"]
    mb = metr(pb, y)
    lines.append(f"| Bookmaker | {mb['acc']*100:.1f}% | {mb['logloss']:.4f} | — | — |")
    for k, p in res.items():
        if len(p) != len(test):
            continue
        p = p[idx]
        mm = metr(p, y)
        fav_h = p >= .5
        win = np.where(fav_h, y == 1, y == 0)
        q = np.where(fav_h, qh, qa)
        roi = (np.where(win, q, 0) - 1).mean()
        ev_h, ev_a = p * qh - 1, (1 - p) * qa - 1
        bet = (ev_h > .05) | (ev_a > .05)
        sh = ev_h >= ev_a
        pay = np.where(sh, np.where(y == 1, qh, 0), np.where(y == 0, qa, 0)) - 1
        roiv = pay[bet].mean() if bet.sum() else float("nan")
        lines.append(f"| {k} | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | {roi*100:+.1f}% | {roiv*100:+.1f}% ({int(bet.sum())} giocate) |")
    for wm in (0.8,):
        p = res["+ correzione + riposo/back-to-back"][idx] if "+ correzione + riposo/back-to-back" in res else None
        if p is not None:
            pmix = (1 - wm) * p + wm * pb
            mm = metr(pmix, y)
            lines.append(f"| Fusione 80% quote + modello migliorato | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | — | — |")
    pmix = 0.2 * res["App di oggi"][idx] + 0.8 * pb
    mm = metr(pmix, y)
    lines.append(f"| Fusione 80% quote + modello di oggi (= app quando ci sono quote) | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | — | — |")
    return lines + [""]


def nba_fetch_odds(budget_s=900):
    """Quote storiche dal riepilogo ESPN (pickcenter). Cache in experiments/nba_odds.json."""
    import requests
    path = OUT / "nba_odds.json"
    try:
        cache = json.loads(path.read_text())
    except Exception:
        cache = {}
    db = S._load("NBA")
    ids = [m["id"] for m in db["matches"].values() if m["done"] and m["date"] >= CUT and m["id"] not in cache]
    stop = time.time() + budget_s
    ok = 0
    for gid in ids:
        if time.time() > stop:
            break
        try:
            r = requests.get(f"{S.ESPN}/basketball/nba/summary", params={"event": gid}, headers=S.H, timeout=20)
            d = r.json()
            rec = {}
            for pc in d.get("pickcenter") or []:
                hm = (pc.get("homeTeamOdds") or {}).get("moneyLine")
                am = (pc.get("awayTeamOdds") or {}).get("moneyLine")
                if hm and am:
                    rec = {"h": S._american(hm), "a": S._american(am),
                           "book": (pc.get("provider") or {}).get("name")}
                    break
            if not rec:
                for o in d.get("odds") or []:
                    hm = (o.get("homeTeamOdds") or {}).get("moneyLine")
                    am = (o.get("awayTeamOdds") or {}).get("moneyLine")
                    if hm and am:
                        rec = {"h": S._american(hm), "a": S._american(am), "book": (o.get("provider") or {}).get("name")}
                        break
            cache[gid] = rec
            ok += bool(rec)
        except Exception as e:
            print("  nba odds", gid, e)
        time.sleep(0.1)
    path.write_text(json.dumps(cache))
    print(f"NBA quote: {ok} nuove, {sum(1 for v in cache.values() if v)} totali con quote")
    return {k: v for k, v in cache.items() if v}


# ===================================================================== TENNIS
QUALI = re.compile(r"qualif", re.I)
SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")


def level(m):
    t = m["torneo"].lower()
    if QUALI.search(m.get("turno", "")):
        return "quali"
    if any(s in t for s in SLAMS):
        return "slam"
    return "tour"


class Elo2(S.TennisElo):
    def __init__(self, kmult=None, mov=0.0):
        super().__init__()
        self.kmult, self.mov = kmult or {}, mov

    def update2(self, w, l, sf, lev, straight):
        f = self.kmult.get(lev, 1.0) * (1 + self.mov if straight else 1 - self.mov)
        for d, n, kw, kl in ((self.all, self.n, w, l), (self.surf, self.ns, (w, sf), (l, sf))):
            ew, el = d.get(kw, 1500), d.get(kl, 1500)
            pw = 1 / (1 + 10 ** ((el - ew) / 400))
            d[kw] = ew + f * S._k(n.get(kw, 0)) * (1 - pw)
            d[kl] = el - f * S._k(n.get(kl, 0)) * (1 - pw)
            n[kw] = n.get(kw, 0) + 1
            n[kl] = n.get(kl, 0) + 1


def tennis_preds(code, kmult=None, mov=0.0):
    db = S._load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo = Elo2(kmult, mov)
    hist = {}   # giocatore -> lista (data, set giocati)
    recs = []
    for m in ms:
        if not m["done"] or m["w"] is None or m.get("wo"):
            continue
        a, b = m["p1"], m["p2"]
        dd = dt.date.fromisoformat(m["date"])

        def fat(p):
            h = hist.get(p, [])
            s3 = sum(s for d, s in h if 0 <= (dd - d).days <= 3)
            n14 = sum(1 for d, s in h if 0 <= (dd - d).days <= 14)
            rust = min(((dd - h[-1][0]).days if h else 60), 60)
            return s3, n14, rust
        if elo.n.get(a, 0) >= 10 and elo.n.get(b, 0) >= 10:
            fa, fb = fat(a), fat(b)
            recs.append({"date": m["date"], "id": m["id"], "p1": a, "p2": b, "torneo": m["torneo"],
                         "p": elo.prob(a, b, m["superficie"]), "y": 1 if m["w"] == 0 else 0,
                         "s3": fa[0] - fb[0], "n14": fa[1] - fb[1],
                         "rust": (1 if fa[2] > 21 else 0) - (1 if fb[2] > 21 else 0),
                         "bo5": m["bo5"], "lev": level(m)})
        w, l = (a, b) if m["w"] == 0 else (b, a)
        sets = m.get("sets") or [0, 0]
        straight = min(sets) == 0
        elo.update2(w, l, m["superficie"], level(m), straight)
        ns = sum(sets) or 2
        hist.setdefault(a, []).append((dd, ns))
        hist.setdefault(b, []).append((dd, ns))
    return recs


def rolling_cal(recs):
    """Calibrazione come l'app: ogni mese, fattore stimato sui 180 giorni prima."""
    pws = [(r["date"], r["p"] if r["y"] else 1 - r["p"]) for r in recs]
    out, cache = [], {}
    for r in recs:
        mo = r["date"][:7]
        if mo not in cache:
            cache[mo] = S.calib_at(pws, mo + "-01")
        out.append(S.calibrate(r["p"], cache[mo]))
    return np.array(out)


def tennis_section(code, book=None):
    lines = [f"## 🎾 {code}", ""]
    base = tennis_preds(code)
    test_i = [i for i, r in enumerate(base) if r["date"] >= CUT]
    tune_i = [i for i, r in enumerate(base) if TUNE_FROM <= r["date"] < CUT]
    y = np.array([base[i]["y"] for i in test_i])
    pc = rolling_cal(base)
    res = {"App di oggi (Elo calibrato)": pc[test_i]}

    # stanchezza: logistic su [logit p calibrata, set ultimi 3gg, partite 14gg, rientro dopo 3 settimane]
    def F(i, rr, pcc):
        r = rr[i]
        lg = logit(pcc[i])
        return [lg, lg * (r["lev"] == "slam"), lg * (r["lev"] == "quali"), r["s3"], r["n14"], r["rust"], 1.0]
    Xt = np.array([F(i, base, pc) for i in tune_i])
    bt = fit_logistic(Xt, [base[i]["y"] for i in tune_i], l2=1e-4)
    res["+ stanchezza / rientro / tipo torneo"] = sig(np.array([F(i, base, pc) for i in test_i]) @ bt)

    # peso torneo + margine nei set: griglia scelta sulla taratura
    best = None
    grid = [({"quali": q, "slam": s}, mv) for q in (0.4, 0.6, 0.8, 1.0) for s in (1.0, 1.25, 1.5) for mv in (0.0, 0.15, 0.3)]
    for km, mv in grid:
        rr = base if (km == {"quali": 1.0, "slam": 1.0} and mv == 0) else tennis_preds(code, km, mv)
        pcc = rolling_cal(rr)
        ti = [i for i, r in enumerate(rr) if TUNE_FROM <= r["date"] < CUT]
        ll = metr(pcc[ti], [rr[i]["y"] for i in ti])["logloss"]
        if best is None or ll < best[0]:
            best = (ll, km, mv, rr, pcc)
    _, km, mv, rr, pcc = best
    tei = [i for i, r in enumerate(rr) if r["date"] >= CUT]
    lab = f"+ peso torneo (quali×{km['quali']}, slam×{km['slam']}) e margine set {mv:+.2f}"
    p_tm = pcc[tei]
    ids_tm = [rr[i]["id"] for i in tei]
    tui = [i for i, r in enumerate(rr) if TUNE_FROM <= r["date"] < CUT]
    Xt2 = np.array([F(i, rr, pcc) for i in tui])
    bt2 = fit_logistic(Xt2, [rr[i]["y"] for i in tui], l2=1e-4)
    p_all = sig(np.array([F(i, rr, pcc) for i in tei]) @ bt2)
    y2 = np.array([rr[i]["y"] for i in tei])

    lines += [f"Taratura: {TUNE_FROM} → {CUT} ({len(tune_i)} partite). Giudizio: dal {CUT} ({len(test_i)} partite).", "",
              "| Variante | Partite | Vincente indovinato | LogLoss | Brier |", "|---|---|---|---|---|"]
    for k, p in res.items():
        mm = metr(p, y)
        lines.append(f"| {k} | {mm['n']} | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | {mm['brier']:.4f} |")
    for k, p in ((lab, p_tm), (lab + " + stanchezza", p_all)):
        mm = metr(p, y2)
        lines.append(f"| {k} | {mm['n']} | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | {mm['brier']:.4f} |")
    lines += ["", f"Coefficienti (logit): fiducia ×{bt[0]:.2f}, extra Slam ×{bt[1]:+.2f}, extra qualificazioni ×{bt[2]:+.2f}; "
              f"set giocati negli ultimi 3 giorni {bt[3]:+.3f} per set di differenza, "
              f"partite in 14 giorni {bt[4]:+.3f}, rientro dopo >3 settimane {bt[5]:+.3f}.", ""]
    # quali partite: livello
    lv = {}
    for i in test_i:
        lv.setdefault(base[i]["lev"], []).append(i)
    lines += ["**Affidabilità per tipo di torneo (app di oggi)**", "", "| Tipo | Partite | Vincente | Detto (fav.) | Uscito (fav.) |", "|---|---|---|---|---|"]
    pos = {i: j for j, i in enumerate(test_i)}
    for k_, ii in sorted(lv.items()):
        p = np.array([pc[i] for i in ii])
        yy = np.array([base[i]["y"] for i in ii])
        pf = np.where(p >= .5, p, 1 - p)
        yf = np.where(p >= .5, yy, 1 - yy)
        nm = {"quali": "Qualificazioni", "slam": "Slam", "tour": "Tornei del circuito"}[k_]
        lines.append(f"| {nm} | {len(ii)} | {metr(p, yy)['acc']*100:.1f}% | {pf.mean()*100:.0f}% | {yf.mean()*100:.0f}% |")
    lines.append("")
    if book:
        best_p = {rr[i]["id"]: p_all[j] for j, i in enumerate(tei)}
        lines += tennis_vs_book(code, base, test_i, pc, book, best_p, lab + " + stanchezza")
    return lines


# ------------------------------------------------------------- tennis-data.co.uk
def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s.replace("-", " ")).split()


def td_key(name):
    """'Auger-Aliassime F.' -> ('augeraliassime','f')"""
    t = norm_name(name)
    if len(t) < 2:
        return None
    ini = [x for x in t if len(x) == 1]
    sur = [x for x in t if len(x) > 1]
    return ("".join(sur), (ini[0] if ini else t[-1][0]))


def espn_keys(name):
    t = norm_name(name)
    if len(t) < 2:
        return set()
    return {("".join(t[i:]), t[0][0]) for i in range(1, len(t))}


DIAG = []


def tennis_fetch_book(code):
    import pandas as pd
    import requests
    frames = []
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    for y in (2025, 2026):
        sub = f"{y}" if code == "ATP" else f"{y}w"
        urls = [f"{pr}://www.tennis-data.co.uk/{sub}/{y}.{ext}" for pr in ("http", "https") for ext in ("xlsx", "xls")]
        for u in urls:
            try:
                r = requests.get(u, headers=ua, timeout=60, allow_redirects=True)
                DIAG.append(f"{u} -> {r.status_code}, {len(r.content)} byte, {r.headers.get('content-type')}")
                if r.status_code == 200 and len(r.content) > 5000:
                    frames.append(pd.read_excel(io.BytesIO(r.content)))
                    DIAG.append(f"  letto: {len(frames[-1])} righe, colonne {list(frames[-1].columns)[:30]}")
                    break
            except Exception as e:
                DIAG.append(f"{u} -> ERRORE {str(e)[:200]}")
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df[df["Date"] >= pd.Timestamp(CUT) - pd.Timedelta(days=3)]
    out = []
    for _, r in df.iterrows():
        kw, kl = td_key(r.get("Winner")), td_key(r.get("Loser"))
        if not kw or not kl:
            continue
        def g(c):
            try:
                v = float(r.get(c))
                return v if v > 1 else None
            except Exception:
                return None
        out.append({"date": r["Date"].date(), "kw": kw, "kl": kl, "b365w": g("B365W"), "b365l": g("B365L"),
                    "psw": g("PSW"), "psl": g("PSL"), "avgw": g("AvgW"), "avgl": g("AvgL"),
                    "torneo": r.get("Tournament"), "serie": r.get("Series") or r.get("Tier")})
    return out


def tennis_vs_book(code, base, test_i, pc, book, best_p, best_lab):
    lines = [f"**Contro Bet365 (quote storiche tennis-data.co.uk)**", ""]
    idx = {}
    for b in book:
        idx.setdefault(frozenset([b["kw"], b["kl"]]), []).append(b)
    rows = []
    for j, i in enumerate(test_i):
        r = base[i]
        dd = dt.date.fromisoformat(r["date"])
        found = None
        for ka in espn_keys(r["p1"]):
            for kb in espn_keys(r["p2"]):
                for b in idx.get(frozenset([ka, kb]), []):
                    if abs((b["date"] - dd).days) <= 10:
                        found = (b, ka == b["kw"])
                        break
                if found:
                    break
            if found:
                break
        if not found:
            continue
        b, p1_is_w = found
        q1 = b["b365w"] if p1_is_w else b["b365l"]
        q2 = b["b365l"] if p1_is_w else b["b365w"]
        if not q1 or not q2:
            continue
        # controllo coerenza esito
        if (r["y"] == 1) != p1_is_w:
            continue
        rows.append((pc[i], best_p.get(r["id"], pc[i]), q1, q2, r["y"], r["lev"]))
    n_test = len(test_i)
    lines.append(f"Partite del periodo di giudizio abbinate alle quote: {len(rows)} su {n_test} "
                 f"(tennis-data copre solo il tabellone principale ATP/WTA, non Challenger/ITF né qualificazioni).")
    if len(rows) < 100:
        return lines + ["Troppo poche per un giudizio.", ""]
    a = np.array([[x[0], x[1], x[2], x[3], x[4]] for x in rows], float)
    p_app, p_new, q1, q2, y = a.T
    pb = (1 / q1) / (1 / q1 + 1 / q2)
    lines += ["", "| Chi | Vincente | LogLoss | ROI puntando sempre il favorito | ROI solo dove vediamo valore >5% |", "|---|---|---|---|---|"]
    mb = metr(pb, y)
    fav = pb >= .5
    roi_b = (np.where(fav, np.where(y == 1, q1, 0), np.where(y == 0, q2, 0)) - 1).mean()
    lines.append(f"| Bet365 (probabilità implicita) | {mb['acc']*100:.1f}% | {mb['logloss']:.4f} | {roi_b*100:+.1f}% (favorito Bet365) | — |")
    for lab, p in (("App di oggi", p_app), (best_lab, p_new),
                   ("Fusione 80% Bet365 + 20% app", 0.2 * p_app + 0.8 * pb),
                   ("Fusione 50% Bet365 + 50% app", 0.5 * p_app + 0.5 * pb)):
        mm = metr(p, y)
        fav = p >= .5
        roi = (np.where(fav, np.where(y == 1, q1, 0), np.where(y == 0, q2, 0)) - 1).mean()
        e1, e2 = p * q1 - 1, (1 - p) * q2 - 1
        bet = (e1 > .05) | (e2 > .05)
        pay = np.where(e1 >= e2, np.where(y == 1, q1, 0), np.where(y == 0, q2, 0)) - 1
        rv = f"{pay[bet].mean()*100:+.1f}% ({int(bet.sum())} giocate)" if bet.sum() else "—"
        lines.append(f"| {lab} | {mm['acc']*100:.1f}% | {mm['logloss']:.4f} | {roi*100:+.1f}% | {rv} |")
    lines += ["", "**Favoriti sopra soglia a quota Bet365**", "", "| Soglia (prob. app) | Partite | Detto | Uscito | Quota media | ROI |", "|---|---|---|---|---|---|"]
    for th in (0.65, 0.75, 0.85):
        pf = np.where(p_app >= .5, p_app, 1 - p_app)
        win = np.where(p_app >= .5, y == 1, y == 0)
        q = np.where(p_app >= .5, q1, q2)
        m = (pf >= th) & (q >= 1.2)
        if m.sum():
            lines.append(f"| ≥{th*100:.0f}% e quota ≥1,20 | {int(m.sum())} | {pf[m].mean()*100:.0f}% | {win[m].mean()*100:.0f}% | "
                         f"{q[m].mean():.2f} | {(np.where(win[m], q[m], 0).mean()-1)*100:+.1f}% |")
    return lines + [""]


# ===================================================================== main
def main():
    t0 = time.time()
    rep = ["# Backtest tennis e NBA — migliorie proposte", "",
           f"Generato il {dt.date.today()}. Nessuna modifica all'app: solo simulazione sul passato.", "",
           "LogLoss: misura quanto sono giuste le percentuali (più basso = meglio; differenze di 0,005 sono già concrete).", ""]
    nba_odds = None
    books = {}
    if NET:
        try:
            nba_odds = nba_fetch_odds()
        except Exception as e:
            print("NBA quote:", e)
        for c in ("ATP", "WTA"):
            try:
                books[c] = tennis_fetch_book(c)
            except Exception as e:
                print("tennis-data", c, e)
    sec, coef = nba_section(nba_odds)
    rep += sec
    for c in ("ATP", "WTA"):
        rep += tennis_section(c, books.get(c))
        print(c, "fatto", round(time.time() - t0))
    if NET and not any(books.values()):
        rep += ["_tennis-data.co.uk non raggiungibile in questo giro: confronto con Bet365 non fatto._", ""]
    if DIAG:
        rep += ["<details><summary>Diagnostica download</summary>", "", "```"] + DIAG + ["```", "</details>", ""]
    rep += [f"_Tempo di calcolo: {time.time()-t0:.0f} s_"]
    (OUT / "backtest_sport.md").write_text("\n".join(rep), encoding="utf-8")
    (OUT / "backtest_sport.json").write_text(json.dumps(coef, indent=1))
    print("\n".join(rep))


if __name__ == "__main__":
    main()
