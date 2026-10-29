"""Altri sport da ESPN (gratis, senza chiave): tennis ATP/WTA e basket NBA.

Tennis: rating Elo per giocatore, generale + per superficie (K variabile, metodo FiveThirtyEight).
        Dalla probabilità di vittoria si ricava quella per set -> mercati sui set.
NBA:    Elo con vantaggio casa + modello punti fatti/subiti; fuso con le quote ESPN quando ci sono.
Lo storico è in cache (engine/cache/espn_*.json) e si aggiorna in modo incrementale.
"""
import datetime as dt
import json
import math
import re
import time
from pathlib import Path

from zoneinfo import ZoneInfo

import numpy as np
import requests
from scipy.optimize import minimize
from scipy.stats import norm

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
H = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
CACHE = Path(__file__).resolve().parent / "cache"
BUDGET_MIN = 8            # tempo massimo per scaricare (il resto al prossimo giro)
HISTORY_DAYS = 730
MARKET_W = 1.0            # NBA: con le quote si usano le quote (backtest 25/26: il mercato da solo è il più preciso)
ROME = ZoneInfo("Europe/Rome")


def it_time(date, ora):
    """ESPN dà data e ora in UTC: le converte in ora italiana (cambia anche il giorno, es. NBA all'01:00)."""
    if not ora:
        return date, ""
    try:
        t = dt.datetime.fromisoformat(f"{date}T{ora}").replace(tzinfo=dt.timezone.utc).astimezone(ROME)
        return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")
    except Exception:
        return date, ora

SPORTS = {
    "ATP": {"path": "tennis/atp", "sport": "tennis", "nome": "ATP", "flag": "🎾", "group": "mens-singles"},
    "WTA": {"path": "tennis/wta", "sport": "tennis", "nome": "WTA", "flag": "🎾", "group": "womens-singles"},
    "NBA": {"path": "basketball/nba", "sport": "basket", "nome": "NBA", "flag": "🏀"},
}

CLAY = ["roland garros", "french open", "monte", "madrid", "rome", "roma", "internazionali", "barcelona", "hamburg",
        "estoril", "munich", "geneva", "lyon", "bastad", "gstaad", "umag", "kitzbuhel", "buenos aires", "rio",
        "santiago", "houston", "marrakech", "bucharest", "cordoba", "palermo", "prague", "rabat", "strasbourg",
        "charleston", "bogota", "parma", "iasi", "cluj", "lausanne", "warsaw", "budapest", "bad homburg"]
GRASS = ["wimbledon", "halle", "queen", "hertogenbosch", "rosmalen", "eastbourne", "mallorca", "stuttgart open",
         "boss open", "nottingham", "berlin", "birmingham", "newport", "libema"]


def surface(name, date):
    n = name.lower()
    if any(k in n for k in GRASS):
        return "erba"
    if any(k in n for k in CLAY):
        return "terra"
    return "cemento"


# ----------------------------------------------------------------- download con cache
def _load(code):
    try:
        return json.loads((CACHE / f"espn_{code}.json").read_text(encoding="utf-8"))
    except Exception:
        return {"matches": {}, "fetched": {}}


def _save(code, db):
    CACHE.mkdir(exist_ok=True)
    limit = (dt.date.today() - dt.timedelta(days=HISTORY_DAYS + 30)).isoformat()
    db["matches"] = {k: v for k, v in db["matches"].items() if v["date"] >= limit}
    (CACHE / f"espn_{code}.json").write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _comps(event):
    for c in event.get("competitions", []) or []:
        yield c
    for g in event.get("groupings", []) or []:
        if isinstance(g, dict):
            slug = (g.get("grouping") or {}).get("slug", "")
            for c in g.get("competitions", []) or []:
                if isinstance(c, dict):
                    c["_group"] = slug
                    yield c


def _american(o):
    try:
        o = float(str(o).replace("+", ""))
        return 1 + (o / 100 if o > 0 else 100 / -o)
    except Exception:
        return None


def _parse_tennis(ev, c, cfg):
    if c.get("_group") != cfg["group"]:
        return None
    cs = [x for x in c.get("competitors", []) if isinstance(x, dict) and x.get("type") == "athlete"]
    if len(cs) != 2:
        return None
    st = (c.get("status") or {}).get("type") or {}
    p = [x.get("athlete", {}).get("displayName") for x in cs]
    if not all(p):
        return None
    sets = [[int(s.get("value", 0)) for s in (x.get("linescores") or []) if isinstance(s, dict)] for x in cs]
    rnd = (c.get("round") or {}).get("displayName", "")
    name = ev.get("name", "")
    bo5 = bool(ev.get("major")) and cfg["group"] == "mens-singles" and "qualif" not in rnd.lower()
    win = [bool(x.get("winner")) for x in cs]
    return {"id": c.get("id"), "date": (c.get("date") or ev.get("date", ""))[:10], "ora": (c.get("date") or "")[11:16],
            "torneo": name, "turno": rnd, "superficie": surface(name, None), "bo5": bo5,
            "p1": p[0], "p2": p[1], "state": st.get("state"), "done": bool(st.get("completed")),
            "w": 0 if win[0] else 1 if win[1] else None,
            "sets": [sum(1 for a, b in zip(sets[0], sets[1]) if a > b), sum(1 for a, b in zip(sets[0], sets[1]) if b > a)],
            "wo": bool(st.get("completed")) and not any(sets[0] + sets[1])}


def _parse_nba(ev, c):
    cs = [x for x in c.get("competitors", []) if isinstance(x, dict)]
    home = next((x for x in cs if x.get("homeAway") == "home"), None)
    away = next((x for x in cs if x.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    st = (c.get("status") or {}).get("type") or {}
    m = {"id": c.get("id"), "date": (c.get("date") or "")[:10], "ora": (c.get("date") or "")[11:16],
         "home": home["team"]["displayName"], "away": away["team"]["displayName"],
         "state": st.get("state"), "done": bool(st.get("completed")),
         "hs": float(home.get("score") or 0), "as": float(away.get("score") or 0)}
    o = next((x for x in (c.get("odds") or []) if isinstance(x, dict)), None)
    if o:
        ml = o.get("moneyline") or {}
        get = lambda side: _american(((ml.get(side) or {}).get("close") or (ml.get(side) or {}).get("open") or {}).get("odds"))
        m["odds"] = {"h": get("home"), "a": get("away"), "spread": o.get("spread"), "ou": o.get("overUnder")}
    return m


def _pickcenter(gid):
    r = requests.get(f"{ESPN}/basketball/nba/summary", params={"event": gid}, headers=H, timeout=20)
    r.raise_for_status()
    d = r.json()
    for pc in (d.get("pickcenter") or []) + (d.get("odds") or []):
        if not isinstance(pc, dict):
            continue
        hm = (pc.get("homeTeamOdds") or {}).get("moneyLine")
        am = (pc.get("awayTeamOdds") or {}).get("moneyLine")
        if hm and am:
            return {"h": _american(hm), "a": _american(am), "spread": pc.get("spread"), "ou": pc.get("overUnder"),
                    "book": (pc.get("provider") or {}).get("name"), "src": "summary"}
    return {}


def update_nba_odds(db, stop, max_old=80):
    """Quote per tutte le partite NBA dei prossimi giorni (il tabellone ESPN le dà solo per alcune) e,
    a piccoli passi, per quelle già giocate dell'ultimo anno (servono a misurare l'affidabilità)."""
    today = dt.date.today().isoformat()
    horizon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    lo = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    ms = db["matches"].values()
    nxt = [m for m in ms if not m["done"] and today <= it_time(m["date"], m.get("ora"))[0] <= horizon]
    old = [m for m in ms if m["done"] and m["date"] >= lo and not m.get("odds") and not m.get("no_odds")][:max_old]
    n = 0
    for m in nxt + old:
        if time.time() > stop:
            break
        try:
            o = _pickcenter(m["id"])
            if o:
                m["odds"] = o
            elif m["done"]:
                m["no_odds"] = 1
            n += 1
        except Exception as e:
            print(f"  [!] quote NBA {m['id']}: {e}")
        time.sleep(0.1)
    return n


def update(code, stop):
    """Aggiorna la cache di un campionato ESPN entro il tempo `stop`."""
    cfg = SPORTS[code]
    db = _load(code)
    today = dt.date.today()
    tennis = cfg["sport"] == "tennis"
    step = 7 if tennis else 1
    # date da scaricare: tutte quelle mai scaricate + le recenti/prossime (risultati e calendario cambiano)
    dates = [today - dt.timedelta(days=d) for d in range(0, HISTORY_DAYS, step)]
    if not tennis:
        dates = [d for d in dates if d.month not in (7, 8, 9)]   # NBA ferma in estate
    fresh = {(today + dt.timedelta(days=d)) for d in range(-3 if tennis else -2, 4)}
    todo = [d for d in sorted(fresh)] + [d for d in dates if d.isoformat() not in db["fetched"] and d not in fresh]
    n = 0
    for d in todo:
        if time.time() > stop:
            break
        try:
            r = requests.get(f"{ESPN}/{cfg['path']}/scoreboard", params={"dates": d.strftime("%Y%m%d")},
                             headers=H, timeout=30)
            r.raise_for_status()
            for ev in r.json().get("events", []) or []:
                for c in _comps(ev):
                    m = _parse_tennis(ev, c, cfg) if tennis else _parse_nba(ev, c)
                    if m and m["id"]:
                        old = db["matches"].get(m["id"]) or {}
                        if not m.get("odds") and old.get("odds"):
                            m["odds"] = old["odds"]          # tieni le quote del riepilogo
                        db["matches"][m["id"]] = m
            db["fetched"][d.isoformat()] = 1
            n += 1
        except Exception as e:
            print(f"  [!] ESPN {code} {d}: {e}")
        time.sleep(0.15)
    q = 0
    if code == "NBA":
        try:
            q = update_nba_odds(db, stop)
        except Exception as e:
            print(f"  [!] quote NBA: {e}")
    _save(code, db)
    return {"richieste": n, "partite": len(db["matches"]), "da_scaricare": max(0, len(todo) - n), "quote_lette": q}


# ----------------------------------------------------------------- comuni
def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + np.exp(-np.asarray(x, float)))


def fit_logistic(X, y, l2=1e-4, b0=None):
    """Regressione logistica (primo coefficiente = moltiplicatore del logit, partenza 1)."""
    X, y = np.asarray(X, float), np.asarray(y, float)

    def f(b):
        z = X @ b
        return np.sum(np.logaddexp(0, z) - y * z) / len(y) + l2 * np.sum(b[1:] ** 2)

    def g(b):
        gr = X.T @ (_sig(X @ b) - y) / len(y)
        gr[1:] += 2 * l2 * b[1:]
        return gr
    if b0 is None:
        b0 = np.zeros(X.shape[1])
        b0[0] = 1.0
    return minimize(f, b0, jac=g, method="L-BFGS-B").x


def fit_calibration(pw):
    """Fattore che corregge l'eccesso di fiducia: p' = sigmoide(a * logit(p)).
    pw = probabilità che il modello aveva dato al vincitore, prima di ogni partita."""
    from scipy.optimize import minimize_scalar
    lg = _logit(pw)
    if len(lg) < 200:
        return 1.0
    r = minimize_scalar(lambda a: np.sum(np.log1p(np.exp(-a * lg))), bounds=(0.2, 2.0), method="bounded")
    return float(r.x)


def calibrate(p, a):
    return float(1 / (1 + np.exp(-a * _logit(p))))


CALIB_DAYS = 180   # finestra della calibrazione (provata mese per mese sull'ultimo anno: la più precisa)


def calib_at(pws, date, days=CALIB_DAYS):
    """Fattore di calibrazione usando solo le partite dei `days` giorni prima di `date`."""
    lo = (dt.date.fromisoformat(date[:10]) - dt.timedelta(days=days)).isoformat()
    return fit_calibration([p for d, p in pws if lo <= d < date])


def _form(hist, who, k=5):
    return "".join(hist.get(who, [])[-k:])


def _window_fit(recs, X, y, date, days=365, min_n=300, b_def=None):
    """Coefficienti stimati solo sulle partite dei `days` giorni prima di `date` (mai sul futuro)."""
    lo = (dt.date.fromisoformat(date[:10]) - dt.timedelta(days=days)).isoformat()
    idx = [i for i, r in enumerate(recs) if lo <= r["date"] < date]
    if len(idx) < min_n:
        return b_def
    return fit_logistic(X[idx], y[idx])


# ----------------------------------------------------------------- tennis
def _k(n):
    return 250 / (n + 5) ** 0.4


def _bo3(p):
    return p * p * (3 - 2 * p)


def _bo5(p):
    return p ** 3 * (10 - 15 * p + 6 * p * p)


def set_prob(pm, bo5):
    """Probabilità di vincere un set che dà la probabilità di vincere la partita pm."""
    f = _bo5 if bo5 else _bo3
    lo, hi = 1e-4, 1 - 1e-4
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < pm else (lo, mid)
    return (lo + hi) / 2


# I set non sono indipendenti: chi è in giornata tende a vincere in due set secchi. Con set indipendenti il
# modello prevedeva il 3° set nel 48% delle partite, nella realtà è il 36%. Fattore stimato sullo storico
# ESPN 2024-25 e verificato sull'ultimo anno (3° set: previsto 35%, reale 36%).
DECIDER = 0.725


def tennis_markets(pm, bo5):
    s = set_prob(pm, bo5)
    q = 1 - s
    if bo5:
        r = {"3-1": 3 * s ** 3 * q * DECIDER, "3-2": 6 * s ** 3 * q * q * DECIDER,
             "1-3": 3 * q ** 3 * s * DECIDER, "2-3": 6 * q ** 3 * s * s * DECIDER}
        r["3-0"] = max(1e-6, pm - r["3-1"] - r["3-2"])
        r["0-3"] = max(1e-6, (1 - pm) - r["1-3"] - r["2-3"])
        over = {"Set Over 3.5": r["3-1"] + r["1-3"] + r["3-2"] + r["2-3"], "Set Over 4.5": r["3-2"] + r["2-3"]}
    else:
        r = {"2-1": 2 * s * s * q * DECIDER, "1-2": 2 * q * q * s * DECIDER}
        r["2-0"] = max(1e-6, pm - r["2-1"])
        r["0-2"] = max(1e-6, (1 - pm) - r["1-2"])
        over = {"Set Over 2.5": r["2-1"] + r["1-2"]}
    lose0_1 = r["0-3"] if bo5 else r["0-2"]
    lose0_2 = r["3-0"] if bo5 else r["2-0"]
    mk = {"1": pm, "2": 1 - pm, "1 vince un set": 1 - lose0_1, "2 vince un set": 1 - lose0_2, **over}
    mk.update({f"Set {k}": v for k, v in r.items()})
    return mk


# Migliorie verificate sul passato (experiments/backtest_sport.md, giudizio 29/10/2025 -> 28/10/2026):
# le qualificazioni pesano meno, gli Slam di più; vincere in set secchi vale più che al set decisivo.
LEV_K = {"quali": 0.4, "slam": 1.5, "tour": 1.0}
MOV = 0.15
SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")


def level(m):
    if re.search(r"qualif", m.get("turno", "") or "", re.I):
        return "quali"
    if any(s in (m.get("torneo") or "").lower() for s in SLAMS):
        return "slam"
    return "tour"


class TennisElo:
    def __init__(self):
        self.all, self.surf, self.n, self.ns = {}, {}, {}, {}
        self.last = {}

    def prob(self, a, b, sf):
        ea = 0.5 * self.all.get(a, 1500) + 0.5 * self.surf.get((a, sf), 1500)
        eb = 0.5 * self.all.get(b, 1500) + 0.5 * self.surf.get((b, sf), 1500)
        return 1 / (1 + 10 ** ((eb - ea) / 400))

    def update(self, w, l, sf, f=1.0):
        for d, n, key_w, key_l in ((self.all, self.n, w, l), (self.surf, self.ns, (w, sf), (l, sf))):
            ew, el = d.get(key_w, 1500), d.get(key_l, 1500)
            pw = 1 / (1 + 10 ** ((el - ew) / 400))
            d[key_w] = ew + f * _k(n.get(key_w, 0)) * (1 - pw)
            d[key_l] = el - f * _k(n.get(key_l, 0)) * (1 - pw)
            n[key_w] = n.get(key_w, 0) + 1
            n[key_l] = n.get(key_l, 0) + 1


def _fatigue(played, who, day):
    """Set giocati negli ultimi 3 giorni, partite negli ultimi 14, giorni dall'ultima partita."""
    h = played.get(who, [])
    s3 = sum(s for d, s in h if 0 <= (day - d).days <= 3)
    n14 = sum(1 for d, s in h if 0 <= (day - d).days <= 14)
    gap = (day - h[-1][0]).days if h else None
    return s3, n14, gap


def _tfeat(pc, lev, fa, fb):
    lg = float(_logit(pc))
    rust = lambda g: 1 if (g is not None and g > 21) else 0
    return [lg, lg * (lev == "slam"), lg * (lev == "quali"), fa[0] - fb[0], fa[1] - fb[1], rust(fa[2]) - rust(fb[2]), 1.0]


B_DEF = np.array([1.0, 0, 0, 0, 0, 0, 0])


def _tnotes(name, f):
    s3, n14, gap = f
    out = []
    if gap is not None and gap > 21:
        out.append(f"{name} rientra dopo {gap // 7} settimane senza partite (di solito rende meno)")
    if s3 >= 5:
        out.append(f"{name} ha giocato {s3} set negli ultimi 3 giorni")
    return out


def tennis(code):
    db = _load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo, hist, played = TennisElo(), {}, {}
    recs = []   # partite già giocate: probabilità e situazione PRIMA della partita
    for m in ms:
        if not m["done"] or m["w"] is None or m.get("wo"):
            continue
        a, b, lev = m["p1"], m["p2"], level(m)
        day = dt.date.fromisoformat(m["date"])
        if elo.n.get(a, 0) >= 10 and elo.n.get(b, 0) >= 10:
            recs.append({"date": m["date"], "p": elo.prob(a, b, m["superficie"]), "y": 1 if m["w"] == 0 else 0,
                         "lev": lev, "fa": _fatigue(played, a, day), "fb": _fatigue(played, b, day)})
        w, l = (a, b) if m["w"] == 0 else (b, a)
        sets = m.get("sets") or [0, 0]
        elo.update(w, l, m["superficie"], LEV_K[lev] * (1 + MOV if min(sets) == 0 else 1 - MOV))
        hist.setdefault(w, []).append("V")
        hist.setdefault(l, []).append("P")
        ns = sum(sets) or 2
        played.setdefault(a, []).append((day, ns))
        played.setdefault(b, []).append((day, ns))
    # 1) calibrazione Elo mese per mese sui 6 mesi precedenti (l'Elo del tennis è troppo sicuro)
    pws = [(r["date"], r["p"] if r["y"] else 1 - r["p"]) for r in recs]
    a_m = {}
    for r in recs:
        mo = r["date"][:7]
        if mo not in a_m:
            a_m[mo] = calib_at(pws, mo + "-01")
        r["pc"] = calibrate(r["p"], a_m[mo])
    # 2) correzione per tipo di torneo + stanchezza/rientro, stimata sull'anno precedente
    X = np.array([_tfeat(r["pc"], r["lev"], r["fa"], r["fb"]) for r in recs]) if recs else np.zeros((0, 7))
    y = np.array([r["y"] for r in recs], float)
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    a_now = calib_at(pws, tomorrow)
    b_now = _window_fit(recs, X, y, tomorrow, b_def=B_DEF)
    # verifica sull'ultimo anno: ogni mese usa solo coefficienti stimati prima
    bt_cut = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    bt, b_m = [], {}
    for i, r in enumerate(recs):
        if r["date"] >= bt_cut:
            mo = r["date"][:7]
            if mo not in b_m:
                b_m[mo] = _window_fit(recs, X, y, mo + "-01", b_def=B_DEF)
            p = float(_sig(X[i] @ b_m[mo]))
            bt.append(p if r["y"] else 1 - p)

    today = dt.date.today()
    horizon = (today + dt.timedelta(days=3)).isoformat()
    out = []
    for m in ms:
        if m["done"] or m["state"] != "pre":
            continue
        d_it, ora_it = it_time(m["date"], m.get("ora", ""))
        if not (today.isoformat() <= d_it <= horizon):
            continue
        if "TBD" in (m["p1"] + m["p2"]) or "/" in m["p1"]:
            continue
        day = dt.date.fromisoformat(m["date"])
        lev = level(m)
        fa, fb = _fatigue(played, m["p1"], day), _fatigue(played, m["p2"], day)
        pc = calibrate(elo.prob(m["p1"], m["p2"], m["superficie"]), a_now)
        p = float(_sig(np.array(_tfeat(pc, lev, fa, fb)) @ b_now))
        conf = min(elo.n.get(m["p1"], 0), elo.n.get(m["p2"], 0))
        motivi = _tnotes(m["p1"], fa) + _tnotes(m["p2"], fb)
        if conf < 15:
            motivi.append("pochi dati su uno dei due giocatori")
        if lev == "quali":
            motivi.append("qualificazioni: i pronostici escono meno spesso (58-61% contro 62-64%)")
        out.append({"sport": "tennis", "lega": code, "id": m["id"], "data": d_it, "ora": ora_it,
                    "casa": m["p1"], "ospite": m["p2"], "torneo": m["torneo"], "turno": m["turno"],
                    "superficie": m["superficie"], "bo5": m["bo5"], "livello": lev,
                    "elo_casa": round(elo.all.get(m["p1"], 1500)), "elo_ospite": round(elo.all.get(m["p2"], 1500)),
                    "partite_casa": elo.n.get(m["p1"], 0), "partite_ospite": elo.n.get(m["p2"], 0),
                    "pochi_dati": conf < 15,
                    "forma_casa": _form(hist, m["p1"]), "forma_ospite": _form(hist, m["p2"]),
                    "mercati": tennis_markets(p, m["bo5"]), "quote": {}, "value": [], "risultati": [],
                    "semaforo": {"livello": "giallo" if motivi else "verde", "motivi": motivi},
                    "assenti_casa": [], "assenti_ospite": [], "note_casa": [], "note_ospite": [],
                    "news_casa": [], "news_ospite": [], "eventi": {}, "effetti": [], "con_mercato": False})
    ranking = sorted(((p, e) for p, e in elo.all.items() if elo.n.get(p, 0) >= 20), key=lambda x: -x[1])[:40]
    rows = [{"squadra": p, "pos": i + 1, "pt": round(e), "pg": elo.n[p],
             "gf": hist.get(p, []).count("V"), "gs": hist.get(p, []).count("P"), "dr": 0,
             "forma": _form(hist, p), "forza": e} for i, (p, e) in enumerate(ranking)]
    bt = np.array(bt)
    metrics = None
    if len(bt) > 100:
        metrics = {"stagione": "ultimi 12 mesi", "partite": int(len(bt)),
                   "acc_modello": float((bt > 0.5).mean()), "logloss_modello": float(-np.log(bt).mean()),
                   "acc_book": None, "logloss_book": None, "roi": None, "calibrazione": round(a_now, 3)}
    return out, rows, metrics


# ----------------------------------------------------------------- NBA
def _nba_ratings(done, i):
    """Punti attesi: medie esponenziali delle ultime 40 partite di ogni squadra, rispetto alla media lega."""
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
        gs = np.array(gs[-40:])
        w = 0.95 ** np.arange(len(gs) - 1, -1, -1)
        o[t] = (gs[:, 0] * w).sum() / w.sum() / L
        d[t] = (gs[:, 1] * w).sum() / w.sum() / L
    return L, o, d


def _book_p(o):
    if o and o.get("h") and o.get("a"):
        qh, qa = 1 / o["h"], 1 / o["a"]
        return qh / (qh + qa)
    return None


def nba(code="NBA"):
    db = _load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo, hist, last = {}, {}, {}
    HOME, K = 70.0, 20.0
    done = [m for m in ms if m["done"] and (m["hs"] or m["as"])]

    def predict(m, rat):
        eh, ea = elo.get(m["home"], 1500), elo.get(m["away"], 1500)
        p_elo = 1 / (1 + 10 ** ((ea - eh - HOME) / 400))
        if rat:
            L, o, d = rat
            xh = L * o.get(m["home"], 1) * d.get(m["away"], 1) + 1.5
            xa = L * o.get(m["away"], 1) * d.get(m["home"], 1) - 1.5
        else:
            xh, xa = 114.0, 112.0
        p_pts = 1 - norm.cdf(0, xh - xa, 12.5)
        return 0.5 * p_elo + 0.5 * p_pts, xh, xa

    recs, rat, rat_day = [], None, None
    for i, m in enumerate(done):
        if rat_day is None or m["date"] > rat_day:   # ricalcolo giornaliero
            rat, rat_day = _nba_ratings(done, i), m["date"]
        if rat:
            p, _, _ = predict(m, rat)
            recs.append({"date": m["date"], "p": p, "y": 1 if m["hs"] > m["as"] else 0, "pb": _book_p(m.get("odds"))})
        eh, ea = elo.get(m["home"], 1500), elo.get(m["away"], 1500)
        mov = abs(m["hs"] - m["as"])
        exp = 1 / (1 + 10 ** ((ea - eh - HOME) / 400))
        res = 1.0 if m["hs"] > m["as"] else 0.0
        mult = ((mov + 3) ** 0.8) / (7.5 + 0.006 * (eh - ea if res else ea - eh))
        elo[m["home"]] = eh + K * mult * (res - exp)
        elo[m["away"]] = ea - K * mult * (res - exp)
        hist.setdefault(m["home"], []).append("V" if res else "P")
        hist.setdefault(m["away"], []).append("P" if res else "V")
        last[m["home"]] = last[m["away"]] = m["date"]

    # correzione dei favoritissimi e del fattore campo: p' = sig(a*logit p + c), stimata sull'anno precedente
    X = np.array([[float(_logit(r["p"])), 1.0] for r in recs]) if recs else np.zeros((0, 2))
    y = np.array([r["y"] for r in recs], float)
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    B0 = np.array([1.0, 0.0])
    b_now = _window_fit(recs, X, y, tomorrow, b_def=B0)
    bt_cut = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    bt, b_m = [], {}
    for i, r in enumerate(recs):
        if r["date"] >= bt_cut:
            mo = r["date"][:7]
            if mo not in b_m:
                b_m[mo] = _window_fit(recs, X, y, mo + "-01", b_def=B0)
            p = float(_sig(X[i] @ b_m[mo]))
            bt.append((p if r["y"] else 1 - p, (r["pb"] if r["y"] else 1 - r["pb"]) if r["pb"] else None))

    today = dt.date.today()
    horizon = (today + dt.timedelta(days=3)).isoformat()
    rat = _nba_ratings(done, len(done))
    out = []
    for m in ms:
        if m["done"] or m["state"] != "pre":
            continue
        d_it, ora_it = it_time(m["date"], m.get("ora", ""))
        if not (today.isoformat() <= d_it <= horizon):
            continue
        p0, xh, xa = predict(m, rat)
        p_model = float(_sig(np.array([float(_logit(p0)), 1.0]) @ b_now))
        o = m.get("odds") or {}
        pb = _book_p(o)
        quote, con_m = {}, False
        if pb:
            p = (1 - MARKET_W) * p_model + MARKET_W * pb
            quote = {"1": round(o["h"], 2), "2": round(o["a"], 2)}
            con_m = True
        else:
            p = p_model
        # punti: differenza allineata alla probabilità finale, totale fra modello e linea del mercato
        diff = float(norm.ppf(min(max(p, 0.01), 0.99))) * 12.5
        tot = xh + xa
        if o.get("ou"):
            tot = 0.5 * tot + 0.5 * o["ou"]
        line_t = o.get("ou") or round(tot) + 0.5
        spread = o.get("spread")
        mk = {"1": p, "2": 1 - p}
        mk[f"Over {line_t} punti"] = 1 - norm.cdf(line_t, tot, 18)
        mk[f"Under {line_t} punti"] = norm.cdf(line_t, tot, 18)
        if spread is not None:
            # spread negativo = casa favorita di |spread| punti
            mk[f"Handicap casa {spread:+g}"] = 1 - norm.cdf(-spread, diff, 12.5)
            mk[f"Handicap ospite {-spread:+g}"] = norm.cdf(-spread, diff, 12.5)
        for k in (5.5, 10.5):
            mk[f"Casa vince di {int(k + 0.5)}+"] = 1 - norm.cdf(k, diff, 12.5)
            mk[f"Ospite vince di {int(k + 0.5)}+"] = norm.cdf(-k, diff, 12.5)
        # semaforo (solo informativo)
        motivi = []
        day = dt.date.fromisoformat(m["date"])
        for t in (m["home"], m["away"]):
            if t in last and (day - dt.date.fromisoformat(last[t])).days == 1:
                motivi.append(f"{t} gioca la seconda partita in due sere (back-to-back)")
        if not con_m:
            motivi.append("quote non ancora disponibili: pronostico del solo modello, meno preciso")
        out.append({"sport": "basket", "lega": code, "id": m["id"], "data": d_it, "ora": ora_it,
                    "casa": m["home"], "ospite": m["away"], "punti_casa": round(diff / 2 + tot / 2, 1),
                    "punti_ospite": round(tot / 2 - diff / 2, 1),
                    "forma_casa": "".join(hist.get(m["home"], [])[-5:]), "forma_ospite": "".join(hist.get(m["away"], [])[-5:]),
                    "mercati": mk, "quote": quote, "bookmaker": f"{o.get('book') or 'DraftKings'} (USA)" if quote else None,
                    "value": [], "risultati": [], "prob_modello": p_model,
                    "semaforo": {"livello": "giallo" if motivi else "verde", "motivi": motivi},
                    "assenti_casa": [], "assenti_ospite": [], "note_casa": [], "note_ospite": [],
                    "news_casa": [], "news_ospite": [], "eventi": {}, "effetti": [], "con_mercato": con_m})
    rows = [{"squadra": t, "pos": i + 1, "pt": round(e), "pg": len(hist.get(t, [])),
             "gf": hist.get(t, []).count("V"), "gs": hist.get(t, []).count("P"), "dr": 0,
             "forma": "".join(hist.get(t, [])[-5:]), "forza": e}
            for i, (t, e) in enumerate(sorted(elo.items(), key=lambda x: -x[1]))]
    metrics = None
    if len(bt) > 100:
        pm = np.array([x[0] for x in bt])
        withb = [x for x in bt if x[1]]
        pb_ = np.array([x[1] for x in withb]) if len(withb) > 50 else None
        pmb = np.array([x[0] for x in withb]) if pb_ is not None else None
        metrics = {"stagione": "ultimi 12 mesi", "partite": len(bt),
                   "acc_modello": float((pm > 0.5).mean()), "logloss_modello": float(-np.log(pm).mean()),
                   "acc_book": float((pb_ > 0.5).mean()) if pb_ is not None else None,
                   "logloss_book": float(-np.log(pb_).mean()) if pb_ is not None else None,
                   "logloss_modello_su_quote": float(-np.log(pmb).mean()) if pmb is not None else None,
                   "partite_con_quote": len(withb), "roi": None,
                   "correzione": [round(float(x), 3) for x in b_now]}
    return out, rows, metrics


def run_all():
    """Scarica (entro il tempo massimo) e calcola. Ritorna partite, classifiche, metriche, stato."""
    stop = time.time() + BUDGET_MIN * 60
    status, matches, tables, metrics = {}, [], {}, {}
    for code, cfg in SPORTS.items():
        try:
            status[code] = update(code, stop)
            ms, rows, met = tennis(code) if cfg["sport"] == "tennis" else nba(code)
            matches += ms
            if rows:
                tables[code] = rows
            if met:
                metrics[code] = met
            status[code]["in_programma"] = len(ms)
        except Exception as e:
            status[code] = {"errore": str(e)[:200]}
            print(f"  [!] {code}: {e}")
    return matches, tables, metrics, status
