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

import numpy as np
import requests
from scipy.stats import norm

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
H = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
CACHE = Path(__file__).resolve().parent / "cache"
BUDGET_MIN = 8            # tempo massimo per scaricare (il resto al prossimo giro)
HISTORY_DAYS = 730
MARKET_W = 0.8            # peso quote NBA nella fusione

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
                        db["matches"][m["id"]] = m
            db["fetched"][d.isoformat()] = 1
            n += 1
        except Exception as e:
            print(f"  [!] ESPN {code} {d}: {e}")
        time.sleep(0.15)
    _save(code, db)
    return {"richieste": n, "partite": len(db["matches"]), "da_scaricare": max(0, len(todo) - n)}


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


def tennis_markets(pm, bo5):
    s = set_prob(pm, bo5)
    q = 1 - s
    if bo5:
        r = {"3-0": s ** 3, "3-1": 3 * s ** 3 * q, "3-2": 6 * s ** 3 * q * q,
             "0-3": q ** 3, "1-3": 3 * q ** 3 * s, "2-3": 6 * q ** 3 * s * s}
        over = {"Set Over 3.5": r["3-1"] + r["1-3"] + r["3-2"] + r["2-3"], "Set Over 4.5": r["3-2"] + r["2-3"]}
    else:
        r = {"2-0": s * s, "2-1": 2 * s * s * q, "0-2": q * q, "1-2": 2 * q * q * s}
        over = {"Set Over 2.5": r["2-1"] + r["1-2"]}
    lose0_1 = r["0-3"] if bo5 else r["0-2"]
    lose0_2 = r["3-0"] if bo5 else r["2-0"]
    mk = {"1": pm, "2": 1 - pm, "1 vince un set": 1 - lose0_1, "2 vince un set": 1 - lose0_2, **over}
    mk.update({f"Set {k}": v for k, v in r.items()})
    return mk


class TennisElo:
    def __init__(self):
        self.all, self.surf, self.n, self.ns = {}, {}, {}, {}
        self.last = {}

    def prob(self, a, b, sf):
        ea = 0.5 * self.all.get(a, 1500) + 0.5 * self.surf.get((a, sf), 1500)
        eb = 0.5 * self.all.get(b, 1500) + 0.5 * self.surf.get((b, sf), 1500)
        return 1 / (1 + 10 ** ((eb - ea) / 400))

    def update(self, w, l, sf):
        for d, n, key_w, key_l in ((self.all, self.n, w, l), (self.surf, self.ns, (w, sf), (l, sf))):
            ew, el = d.get(key_w, 1500), d.get(key_l, 1500)
            pw = 1 / (1 + 10 ** ((el - ew) / 400))
            d[key_w] = ew + _k(n.get(key_w, 0)) * (1 - pw)
            d[key_l] = el - _k(n.get(key_l, 0)) * (1 - pw)
            n[key_w] = n.get(key_w, 0) + 1
            n[key_l] = n.get(key_l, 0) + 1


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


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


def tennis(code):
    db = _load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo, hist = TennisElo(), {}
    bt_cut = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    pws = []   # (data, probabilità data al vincitore prima della partita)
    for m in ms:
        if not m["done"] or m["w"] is None or m.get("wo"):
            continue
        w, l = (m["p1"], m["p2"]) if m["w"] == 0 else (m["p2"], m["p1"])
        if elo.n.get(w, 0) >= 10 and elo.n.get(l, 0) >= 10:
            pws.append((m["date"], elo.prob(w, l, m["superficie"])))
        elo.update(w, l, m["superficie"])
        hist.setdefault(w, []).append("V")
        hist.setdefault(l, []).append("P")
    # calibrazione sugli ultimi 6 mesi (il tennis Elo tende a essere troppo sicuro)
    a_now = calib_at(pws, (dt.date.today() + dt.timedelta(days=1)).isoformat())
    # verifica: mese per mese, ogni mese calibrato solo con i 6 mesi precedenti
    bt, a_month = [], {}
    for d, p in pws:
        if d >= bt_cut:
            mo = d[:7]
            if mo not in a_month:
                a_month[mo] = calib_at(pws, mo + "-01")
            bt.append(calibrate(p, a_month[mo]))
    today = dt.date.today().isoformat()
    horizon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    out = []
    for m in ms:
        if m["done"] or m["state"] != "pre" or not (today <= m["date"] <= horizon):
            continue
        if "TBD" in (m["p1"] + m["p2"]) or "/" in m["p1"]:
            continue
        p = calibrate(elo.prob(m["p1"], m["p2"], m["superficie"]), a_now)
        conf = min(elo.n.get(m["p1"], 0), elo.n.get(m["p2"], 0))
        out.append({"sport": "tennis", "lega": code, "data": m["date"], "ora": m.get("ora", ""),
                    "casa": m["p1"], "ospite": m["p2"], "torneo": m["torneo"], "turno": m["turno"],
                    "superficie": m["superficie"], "bo5": m["bo5"],
                    "elo_casa": round(elo.all.get(m["p1"], 1500)), "elo_ospite": round(elo.all.get(m["p2"], 1500)),
                    "partite_casa": elo.n.get(m["p1"], 0), "partite_ospite": elo.n.get(m["p2"], 0),
                    "pochi_dati": conf < 15,
                    "forma_casa": _form(hist, m["p1"]), "forma_ospite": _form(hist, m["p2"]),
                    "mercati": tennis_markets(p, m["bo5"]), "quote": {}, "value": [], "risultati": [],
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
def nba(code="NBA"):
    db = _load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo, hist = {}, {}
    HOME, K = 70.0, 20.0
    pts = {}          # squadra -> lista (data, fatti, subiti)
    done = [m for m in ms if m["done"] and (m["hs"] or m["as"])]
    bt = []
    bt_cut = (dt.date.today() - dt.timedelta(days=365)).isoformat()

    def ratings(before):
        """Punti attesi: medie esponenziali di fatti/subiti rispetto alla media lega."""
        rec = [m for m in done if m["date"] < before][-1400:]
        if len(rec) < 100:
            return None
        L = np.mean([m["hs"] + m["as"] for m in rec]) / 2
        o, d = {}, {}
        for t in set(x["home"] for x in rec) | set(x["away"] for x in rec):
            f, s, w = [], [], []
            games = [m for m in rec if t in (m["home"], m["away"])][-40:]
            for i, m in enumerate(games):
                ww = 0.95 ** (len(games) - 1 - i)
                f.append((m["hs"] if m["home"] == t else m["as"]) * ww)
                s.append((m["as"] if m["home"] == t else m["hs"]) * ww)
                w.append(ww)
            if w:
                o[t] = sum(f) / sum(w) / L
                d[t] = sum(s) / sum(w) / L
        return L, o, d

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

    last_rat_date, rat = None, None
    for m in done:
        if m["date"] >= bt_cut:
            if last_rat_date is None or m["date"] > last_rat_date:   # ricalcolo giornaliero
                rat, last_rat_date = ratings(m["date"]), m["date"]
            p, _, _ = predict(m, rat)
            hw = m["hs"] > m["as"]
            o = m.get("odds") or {}
            pb = None
            if o.get("h") and o.get("a"):
                qh, qa = 1 / o["h"], 1 / o["a"]
                pb = qh / (qh + qa)
            bt.append((p if hw else 1 - p, (pb if hw else 1 - pb) if pb else None))
        eh, ea = elo.get(m["home"], 1500), elo.get(m["away"], 1500)
        mov = abs(m["hs"] - m["as"])
        exp = 1 / (1 + 10 ** ((ea - eh - HOME) / 400))
        res = 1.0 if m["hs"] > m["as"] else 0.0
        mult = ((mov + 3) ** 0.8) / (7.5 + 0.006 * (eh - ea if res else ea - eh))
        elo[m["home"]] = eh + K * mult * (res - exp)
        elo[m["away"]] = ea - K * mult * (res - exp)
        hist.setdefault(m["home"], []).append("V" if res else "P")
        hist.setdefault(m["away"], []).append("P" if res else "V")

    today = dt.date.today().isoformat()
    horizon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    rat = ratings("9999")
    out = []
    for m in ms:
        if m["done"] or m["state"] != "pre" or not (today <= m["date"] <= horizon):
            continue
        p, xh, xa = predict(m, rat)
        o = m.get("odds") or {}
        quote, con_m = {}, False
        if o.get("h") and o.get("a"):
            qh, qa = 1 / o["h"], 1 / o["a"]
            pb = qh / (qh + qa)
            p_model = p
            p = (1 - MARKET_W) * p + MARKET_W * pb
            quote = {"1": round(o["h"], 2), "2": round(o["a"], 2)}
            con_m = True
        else:
            p_model = p
        # punti: la linea del mercato se c'è, altrimenti la nostra
        diff = xh - xa
        tot = xh + xa
        line_t = o.get("ou") or round(tot) + 0.5
        spread = o.get("spread")
        mk = {"1": p, "2": 1 - p}
        if con_m:   # allinea la differenza attesa alla probabilità combinata
            diff = norm.ppf(p) * 12.5
        if o.get("ou"):
            tot = 0.5 * tot + 0.5 * o["ou"]
        mk[f"Over {line_t} punti"] = 1 - norm.cdf(line_t, tot, 18)
        mk[f"Under {line_t} punti"] = norm.cdf(line_t, tot, 18)
        if spread is not None:
            # spread negativo = casa favorita di |spread| punti
            mk[f"Handicap casa {spread:+g}"] = 1 - norm.cdf(-spread, diff, 12.5)
            mk[f"Handicap ospite {-spread:+g}"] = norm.cdf(-spread, diff, 12.5)
        for k in (5.5, 10.5):
            mk[f"Casa vince di {int(k + 0.5)}+"] = 1 - norm.cdf(k, diff, 12.5)
            mk[f"Ospite vince di {int(k + 0.5)}+"] = norm.cdf(-k, diff, 12.5)
        value = []
        for key, q in quote.items():
            pm = p_model if key == "1" else 1 - p_model
            if q and pm * q - 1 > 0.05:
                value.append({"mercato": key, "prob": pm, "quota": q, "edge": pm * q - 1})
        out.append({"sport": "basket", "lega": code, "data": m["date"], "ora": m.get("ora", ""),
                    "casa": m["home"], "ospite": m["away"], "punti_casa": round(diff / 2 + tot / 2, 1),
                    "punti_ospite": round(tot / 2 - diff / 2, 1),
                    "forma_casa": "".join(hist.get(m["home"], [])[-5:]), "forma_ospite": "".join(hist.get(m["away"], [])[-5:]),
                    "mercati": mk, "quote": quote, "bookmaker": "DraftKings (USA)" if quote else None,
                    "value": value, "risultati": [],
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
        pb = np.array([x[1] for x in withb]) if withb else None
        metrics = {"stagione": "ultimi 12 mesi", "partite": len(bt),
                   "acc_modello": float((pm > 0.5).mean()), "logloss_modello": float(-np.log(pm).mean()),
                   "acc_book": float((pb > 0.5).mean()) if pb is not None else None,
                   "logloss_book": float(-np.log(pb).mean()) if pb is not None else None, "roi": None}
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
