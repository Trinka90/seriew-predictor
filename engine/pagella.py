"""Pagella: tiene traccia giorno per giorno dei pronostici consigliati dall'app e di come sono andati.

- Prima del fischio d'inizio salva il pronostico consigliato di ogni partita (stessa regola dell'app:
  il più probabile che paghi almeno 1,20), con probabilità, quota e semaforo.
- Al primo giro di ogni giorno congela la "schedina del giorno" (i 4 pronostici più sicuri, almeno 65%).
- Quando arriva il risultato, segna ✅ / ❌ e calcola i totali.
Stato in engine/cache/pagella.json, riepilogo per l'app in docs/data/pagella.json.
"""
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

import config as C

ROME = ZoneInfo("Europe/Rome")
PATH = C.BASE_DIR / "cache" / "pagella.json"
QMIN, PMIN, N_SLIP = 1.2, 0.65, 4
KEEP_DAYS = 120
CALCIO_KEYS = ["1", "X", "2", "1X", "X2", "12", "Over 1.5", "Over 2.5", "Over 3.5", "Under 1.5", "Under 2.5", "Under 3.5",
               "GG", "NG", "Multigol 1-3", "Multigol 2-4", "Multigol 2-3", "1 + Over 1.5", "2 + Over 1.5", "1 + GG",
               "2 + GG", "GG + Over 2.5"]


# ------------------------------------------------------------------ scelta del pronostico (come l'app)
def _picks(m):
    keys = CALCIO_KEYS if m.get("sport", "calcio") == "calcio" else list(m["mercati"])
    out = []
    for k in keys:
        p = m["mercati"].get(k)
        if p is None or p <= 0 or "attesi" in k:
            continue
        real = (m.get("quote") or {}).get(k)
        est = None if real else (m.get("quote_stimate") or {}).get(k)
        q = real or est
        out.append({"k": k, "p": p, "q": q, "est": bool(est), "odd": q or 1 / p,
                    "bk": (m.get("bookmaker") or "Bet365") if q else None})
    return sorted(out, key=lambda x: -x["p"])


def best_pick(m, qmin=QMIN):
    al = _picks(m)
    if not al:
        return None
    return next((x for x in al if x["odd"] >= qmin), al[-1])


def key_of(m):
    return f"{m.get('lega')}|{m['data']}|{m['casa']}|{m['ospite']}"


def _kickoff(m):
    try:
        return dt.datetime.fromisoformat(f"{m['data']}T{m.get('ora') or '23:59'}").replace(tzinfo=ROME)
    except Exception:
        return None


# ------------------------------------------------------------------ esito dei pronostici
def _calcio_ok(k, h, a):
    """True/False per un mercato calcio dato il risultato finale h-a."""
    if " + " in k:
        return all(_calcio_ok(x, h, a) for x in k.split(" + "))
    t = h + a
    r = re.match(r"^(Over|Under) (\d)\.5$", k)
    if r:
        return t > int(r[2]) if r[1] == "Over" else t <= int(r[2])
    r = re.match(r"^Multigol (\d)-(\d)$", k)
    if r:
        return int(r[1]) <= t <= int(r[2])
    return {"1": h > a, "X": h == a, "2": h < a, "1X": h >= a, "X2": h <= a, "12": h != a,
            "GG": h > 0 and a > 0, "NG": h == 0 or a == 0}.get(k)


def _tennis_ok(k, w, sets):
    s1, s2 = sets
    if k == "1":
        return w == 0
    if k == "2":
        return w == 1
    if k == "1 vince un set":
        return s1 >= 1 or w == 0
    if k == "2 vince un set":
        return s2 >= 1 or w == 1
    r = re.match(r"^Set Over (\d)\.5$", k)
    if r:
        return s1 + s2 > int(r[1])
    r = re.match(r"^Set (\d)-(\d)$", k)
    if r:
        return [s1, s2] == [int(r[1]), int(r[2])]
    return None


def _nba_ok(k, h, a):
    if k == "1":
        return h > a
    if k == "2":
        return a > h
    r = re.match(r"^(Over|Under) ([\d.]+) punti$", k)
    if r:
        v = float(r[2])
        if h + a == v:
            return None
        return h + a > v if r[1] == "Over" else h + a < v
    r = re.match(r"^Handicap (casa|ospite) ([+-][\d.]+)$", k)
    if r:
        d = (h - a if r[1] == "casa" else a - h) + float(r[2])
        return None if d == 0 else d > 0
    r = re.match(r"^(Casa|Ospite) vince di (\d+)\+$", k)
    if r:
        return (h - a if r[1] == "Casa" else a - h) >= int(r[2])
    return None


def _results(con):
    """Risultati disponibili: calcio dal database, tennis/NBA dalla cache ESPN."""
    import sports
    res = {"calcio": {}, "espn": {}}
    lo = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    try:
        for lg, d, h, a, gh, ga in con.execute(
                "SELECT league, date, home, away, fthg, ftag FROM matches WHERE date >= ? AND fthg IS NOT NULL", (lo,)):
            res["calcio"].setdefault((lg, h, a), []).append((str(d)[:10], int(gh), int(ga)))
    except Exception as e:
        print(f"  [!] pagella risultati calcio: {e}")
    try:   # ESPN: risultati in tempo reale, prima che arrivino su football-data
        for lg, d, h, a, gh, ga in con.execute(
                "SELECT league, date, home, away, hs, as_ FROM espn_soccer WHERE hs IS NOT NULL AND as_ IS NOT NULL"):
            res["calcio"].setdefault((lg, h, a), []).append((str(d)[:10], int(gh), int(ga)))
    except Exception as e:
        print(f"  [!] pagella risultati ESPN: {e}")
    for code in sports.SPORTS:
        for mid, m in sports._load(code)["matches"].items():
            if m.get("done"):
                res["espn"][mid] = m
    return res


def _settle(p, res):
    """Ritorna True/False (vinto/perso), None se annullato, 'attesa' se il risultato non c'è ancora."""
    sport = p.get("sport", "calcio")
    if sport == "calcio":
        for d, h, a in res["calcio"].get((p["lega"], p["casa"], p["ospite"]), []):
            if abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(p["data"])).days) <= 2:
                p["risultato"] = f"{h}-{a}"
                return _calcio_ok(p["k"], h, a)
        return "attesa"
    m = res["espn"].get(str(p.get("id")))
    if not m:
        return "attesa"
    if sport == "tennis":
        if m.get("wo") or m.get("w") is None:
            return None
        p["risultato"] = "-".join(str(x) for x in m.get("sets") or [])
        return _tennis_ok(p["k"], m["w"], m.get("sets") or [0, 0])
    h, a = int(m.get("hs") or 0), int(m.get("as") or 0)
    p["risultato"] = f"{h}-{a}"
    return _nba_ok(p["k"], h, a)


# ------------------------------------------------------------------ aggiornamento
def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"pronostici": {}, "schedine": {}, "dal": dt.date.today().isoformat()}


def _snap(m, x):
    return {"sport": m.get("sport", "calcio"), "lega": m.get("lega"), "id": m.get("id"),
            "data": m["data"], "ora": m.get("ora", ""), "casa": m["casa"], "ospite": m["ospite"],
            "k": x["k"], "p": round(x["p"], 4), "q": x["q"], "est": x["est"], "bk": x["bk"],
            "semaforo": (m.get("semaforo") or {}).get("livello", "verde")}


def update(matches, con, now=None):
    now = now or dt.datetime.now(ROME)
    today = now.date().isoformat()
    st = _load()
    P, S = st["pronostici"], st["schedine"]
    # 1) pronostico consigliato di ogni partita non ancora iniziata (l'ultimo prima del via resta)
    upcoming = []
    for m in matches:
        ko = _kickoff(m)
        if not ko or ko <= now:
            continue
        x = best_pick(m)
        if not x:
            continue
        k = key_of(m)
        if k in P and P[k].get("esito") not in (None, "attesa"):
            continue
        P[k] = {**_snap(m, x), "esito": "attesa"}
        upcoming.append((m, k))
    # 2) schedina del giorno: congelata al primo giro della giornata
    if today not in S:
        cands = []
        for m, k in upcoming:
            if m["data"] != today:
                continue
            ok = [x for x in _picks(m) if x["p"] >= PMIN and x["odd"] >= QMIN]
            if ok:
                cands.append((ok[0], m, k))
        cands.sort(key=lambda t: -t[0]["p"])
        if len(cands) >= 2:
            S[today] = {"creata": now.isoformat(timespec="minutes"),
                        "pronostici": [{**_snap(m, x), "chiave": k} for x, m, k in cands[:N_SLIP]]}
    # 3) esiti
    res = _results(con)
    for k, p in P.items():
        if p.get("esito") != "attesa":
            continue
        r = _settle(p, res)
        if r == "attesa":
            if (now.date() - dt.date.fromisoformat(p["data"])).days > 5:
                p["esito"] = "annullata"     # rinviata o risultato mai arrivato
            continue
        p["esito"] = "annullata" if r is None else ("vinto" if r else "perso")
    for d, s in S.items():
        for x in s["pronostici"]:
            if x.get("esito") in (None, "attesa"):
                r = _settle(x, res)
                if r == "attesa":
                    if (now.date() - dt.date.fromisoformat(x["data"])).days > 5:
                        x["esito"] = "annullata"
                    else:
                        x["esito"] = "attesa"
                else:
                    x["esito"] = "annullata" if r is None else ("vinto" if r else "perso")
    # pulizia
    lo = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    st["pronostici"] = {k: v for k, v in P.items() if v["data"] >= lo}
    st["schedine"] = {d: v for d, v in S.items() if d >= lo}
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return summary(st, now)


# ------------------------------------------------------------------ riepilogo per l'app
def _agg(rows):
    done = [r for r in rows if r["esito"] in ("vinto", "perso")]
    n = len(done)
    real = [r for r in done if r.get("q") and not r.get("est")]
    out = {"n": n, "vinti": sum(r["esito"] == "vinto" for r in done),
           "attesi": round(sum(r["p"] for r in done), 2)}
    if real:
        out["resa"] = round(sum((r["q"] if r["esito"] == "vinto" else 0) - 1 for r in real) / len(real), 4)
        out["n_quote"] = len(real)
    return out


def _slip_state(s):
    es = [x.get("esito") for x in s["pronostici"]]
    if "perso" in es:
        return "persa"
    if any(e in (None, "attesa") for e in es):
        return "in corso"
    if all(e == "annullata" for e in es):
        return "annullata"
    return "vinta"


def summary(st, now):
    P, S = st["pronostici"], st["schedine"]
    today = now.date()
    rows = list(P.values())

    def since(days):
        lo = (today - dt.timedelta(days=days)).isoformat()
        return [r for r in rows if r["data"] >= lo]
    periodi = {}
    for lab, days in (("7", 7), ("30", 30), ("tutto", 3650)):
        rr = since(days)
        fasce = {}
        for nm, lo_, hi in (("65-70%", .65, .70), ("70-80%", .70, .80), ("80%+", .80, 1.01)):
            fasce[nm] = _agg([r for r in rr if lo_ <= r["p"] < hi])
        sl = [s for d, s in S.items() if d >= (today - dt.timedelta(days=days)).isoformat()]
        st_sl = [_slip_state(s) for s in sl]
        periodi[lab] = {"tutti": _agg(rr),
                        "sport": {sp: _agg([r for r in rr if r["sport"] == sp]) for sp in ("calcio", "tennis", "basket")},
                        "semaforo": {lv: _agg([r for r in rr if r.get("semaforo") == lv]) for lv in ("verde", "giallo")},
                        "fasce": fasce,
                        "schedine": {"giocate": sum(x in ("vinta", "persa") for x in st_sl),
                                     "vinte": st_sl.count("vinta"),
                                     "attese": round(sum(_slip_p(s) for s, x in zip(sl, st_sl) if x in ("vinta", "persa")), 2)}}
    giorni = []
    for i in range(0, 30):
        d = (today - dt.timedelta(days=i)).isoformat()
        rr = [r for r in rows if r["data"] == d]
        s = S.get(d)
        if not rr and not s:
            continue
        giorni.append({"data": d, **_agg(rr), "in_attesa": sum(r["esito"] == "attesa" for r in rr),
                       "schedina": ({"stato": _slip_state(s), "prob": round(_slip_p(s), 4), "quota": _slip_q(s),
                                     "pronostici": s["pronostici"]} if s else None)})
    recenti = sorted([r for r in rows if r["esito"] in ("vinto", "perso") and r["data"] >= (today - dt.timedelta(days=3)).isoformat()],
                     key=lambda r: (r["data"], r.get("ora") or ""), reverse=True)[:60]
    return {"aggiornato": now.isoformat(timespec="minutes"), "dal": st.get("dal"), "periodi": periodi,
            "giorni": giorni, "recenti": recenti, "in_attesa": sum(r["esito"] == "attesa" for r in rows)}


def _slip_p(s):
    p = 1.0
    for x in s["pronostici"]:
        if x.get("esito") != "annullata":
            p *= x["p"]
    return p


def _slip_q(s):
    xs = [x for x in s["pronostici"] if x.get("esito") != "annullata"]
    if xs and all(x.get("q") for x in xs) and len({x.get("bk") for x in xs}) == 1:
        q = 1.0
        for x in xs:
            q *= x["q"]
        return {"quota": round(q, 2), "reale": not any(x.get("est") for x in xs), "bk": xs[0].get("bk")}
    q = 1.0
    for x in xs:
        q *= 1 / x["p"]
    return {"quota": round(q, 2), "reale": False, "bk": None}
