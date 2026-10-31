"""Vale la pena aggiungere altri sport? Ultimo anno di risultati + quote ESPN (gratis) per ogni candidato.
Misura: partite, copertura quote, quanto vince il favorito, quante partite hanno un favorito >=70% / >=80%
(= pronostici 'sicuri' che porterebbe all'app), calibrazione delle quote, stagionalità. Solo analisi."""
import datetime as dt
import json
import random
import time
from pathlib import Path

import numpy as np
import requests

E = "https://site.api.espn.com/apis/site/v2/sports"
H = {"User-Agent": "Mozilla/5.0"}
CAND = {
    "NFL": ("football/nfl", False), "NHL": ("hockey/nhl", False), "MLB": ("baseball/mlb", False),
    "Champions League": ("soccer/uefa.champions", True), "Europa League": ("soccer/uefa.europa", True),
    "Conference League": ("soccer/uefa.europa.conf", True), "Coppa Italia": ("soccer/ita.coppa_italia", True),
    "FA Cup": ("soccer/eng.fa", True), "NBA (riferimento)": ("basketball/nba", False),
}
TODAY = dt.date.today()
START = TODAY - dt.timedelta(days=365)
BUDGET = time.time() + 40 * 60
random.seed(1)


def am(o):
    try:
        o = float(str(o).replace("+", ""))
        return 1 + (o / 100 if o > 0 else 100 / -o) if o else None
    except Exception:
        return None


def odds_from(o):
    """(casa, pari, ospite) decimali da un oggetto quote ESPN (tabellone o riepilogo)."""
    if not isinstance(o, dict):
        return None
    ml = o.get("moneyline") or {}
    g = lambda s: am(((ml.get(s) or {}).get("close") or (ml.get(s) or {}).get("open") or {}).get("odds"))
    h, a, d = g("home"), g("away"), g("draw")
    if not (h and a):
        h = am((o.get("homeTeamOdds") or {}).get("moneyLine"))
        a = am((o.get("awayTeamOdds") or {}).get("moneyLine"))
        d = am((o.get("drawOdds") or {}).get("moneyLine"))
    return (h, d, a) if h and a else None


def games(path):
    out, n_req = [], 0
    day = START
    while day <= TODAY and time.time() < BUDGET:
        try:
            r = requests.get(f"{E}/{path}/scoreboard", params={"dates": f"{day:%Y%m%d}"}, headers=H, timeout=20)
            n_req += 1
            for ev in r.json().get("events", []) if r.ok else []:
                c = ev["competitions"][0]
                if not (c.get("status") or {}).get("type", {}).get("completed"):
                    continue
                cs = {x.get("homeAway"): x for x in c.get("competitors", [])}
                try:
                    hs, as_ = float(cs["home"]["score"]), float(cs["away"]["score"])
                except Exception:
                    continue
                out.append({"id": ev["id"], "date": str(day), "hs": hs, "as": as_,
                            "odds": odds_from(next(iter(c.get("odds") or []), None))})
        except Exception as e:
            print(path, day, e)
        day += dt.timedelta(days=1)
        time.sleep(0.05)
    return out, n_req


def fill_odds(path, gs, k=250):
    miss = [g for g in gs if not g["odds"]]
    for g in random.sample(miss, min(k, len(miss))):
        if time.time() > BUDGET:
            break
        try:
            s = requests.get(f"{E}/{path}/summary", params={"event": g["id"]}, headers=H, timeout=20).json()
            for o in (s.get("pickcenter") or []) + (s.get("odds") or []):
                x = odds_from(o)
                if x:
                    g["odds"] = x
                    break
        except Exception:
            pass
        time.sleep(0.05)


def analyze(name, path, soccer):
    gs, nreq = games(path)
    fill_odds(path, gs)
    res = {"partite": len(gs), "richieste": nreq}
    if not gs:
        return res
    months = sorted({g["date"][:7] for g in gs})
    res["mesi_con_partite"] = len(months)
    res["ultima_partita"] = max(g["date"] for g in gs)
    res["prossimi_30gg"] = None
    hw = np.mean([g["hs"] > g["as"] for g in gs])
    res["vince_casa"] = round(float(hw), 3)
    if soccer:
        res["pareggi"] = round(float(np.mean([g["hs"] == g["as"] for g in gs])), 3)
    wo = [g for g in gs if g["odds"]]
    res["con_quote"] = len(wo)
    if len(wo) >= 40:
        P, Y = [], []
        for g in wo:
            h, d, a = g["odds"]
            q = np.array([1 / h, 1 / d if d else 0, 1 / a])
            p = q / q.sum()
            res.setdefault("_marg", []).append(q.sum() - 1)
            out = 0 if g["hs"] > g["as"] else (1 if g["hs"] == g["as"] else 2)
            f = int(np.argmax(p))
            P.append(p[f]); Y.append(out == f)
        P, Y = np.array(P), np.array(Y)
        res["margine_book"] = round(float(np.mean(res.pop("_marg"))), 3)
        res["fav_vince"] = round(float(Y.mean()), 3)
        res["fav_detto"] = round(float(P.mean()), 3)
        for th in (0.6, 0.7, 0.8):
            m = P >= th
            res[f"quota_fav_ge{int(th*100)}"] = round(float(m.mean()), 3)
            res[f"esito_fav_ge{int(th*100)}"] = (round(float(Y[m].mean()), 3), round(float(P[m].mean()), 3), int(m.sum())) if m.sum() else None
        # quante partite "sicure" al mese (stagione in corso, rapportate alla stagione)
        res["fav70_al_mese"] = round(float((P >= 0.7).sum()) / max(1, len({g["date"][:7] for g in wo})), 1)
    return res


def upcoming(path):
    n = 0
    for d in range(0, 30, 3):
        day = TODAY + dt.timedelta(days=d)
        try:
            r = requests.get(f"{E}/{path}/scoreboard", params={"dates": f"{day:%Y%m%d}"}, headers=H, timeout=20)
            n += len(r.json().get("events", [])) if r.ok else 0
        except Exception:
            pass
    return n


out = {"generato": str(TODAY)}
for name, (path, soccer) in CAND.items():
    t = time.time()
    try:
        out[name] = analyze(name, path, soccer)
        out[name]["prossimi_30gg_campione"] = upcoming(path)
    except Exception as e:
        out[name] = {"errore": str(e)[:200]}
    print(name, round(time.time() - t), out[name])
Path(__file__).with_name("analisi_sport.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
