"""Sonda ESPN: struttura quote calcio (tutti i campionati) e quote NBA nel riepilogo partita. Solo diagnostica."""
import datetime as dt
import json
from pathlib import Path

import requests

E = "https://site.api.espn.com/apis/site/v2/sports"
H = {"User-Agent": "Mozilla/5.0"}
SLUGS = ["ita.1", "ita.2", "eng.1", "eng.2", "esp.1", "esp.2", "ger.1", "ger.2", "fra.1", "fra.2", "ned.1", "por.1",
         "bel.1", "tur.1", "sco.1", "eng.3", "eng.4", "eng.5", "sco.2", "sco.3", "sco.4", "gre.1"]
out = {"leghe": {}, "esempi": {}}
today = dt.date.today()
rng = f"{(today - dt.timedelta(days=3)):%Y%m%d}-{(today + dt.timedelta(days=4)):%Y%m%d}"
out["varianti"] = {}
for nm, par in (("range", {"dates": rng}), ("giorno", {"dates": f"{today:%Y%m%d}"}), ("niente", {}),
                ("range_limit", {"dates": rng, "limit": 100})):
    try:
        r = requests.get(f"{E}/soccer/ita.1/scoreboard", params=par, headers=H, timeout=30)
        out["varianti"][nm] = [r.status_code, len(r.json().get("events", [])) if r.ok else r.text[:200]]
    except Exception as e:
        out["varianti"][nm] = str(e)[:200]
for s in SLUGS:
    try:
        r = requests.get(f"{E}/soccer/{s}/scoreboard", params={"dates": rng}, headers=H, timeout=30)
        evs = r.json().get("events", [])
        n_odds = 0
        for ev in evs:
            c = ev["competitions"][0]
            if c.get("odds"):
                n_odds += 1
                if s not in out["esempi"] and c["status"]["type"]["state"] == "pre":
                    out["esempi"][s] = {"date": c.get("date"), "competitors": [
                        {k: x.get(k) for k in ("homeAway", "score")} | {"team": x["team"].get("displayName"),
                                                                         "short": x["team"].get("shortDisplayName")}
                        for x in c["competitors"]], "odds": c["odds"]}
        done = [ev for ev in evs if ev["competitions"][0]["status"]["type"].get("completed")]
        out["leghe"][s] = {"status": r.status_code, "eventi": len(evs), "con_quote": n_odds, "finite": len(done),
                           "es_finita": ({x["homeAway"]: [x["team"]["displayName"], x.get("score")]
                                          for x in done[0]["competitions"][0]["competitors"]} if done else None),
                           "es_data": evs[0]["date"] if evs else None}
    except Exception as e:
        out["leghe"][s] = {"errore": str(e)[:200]}
# NBA: quote nel tabellone e nel riepilogo, oggi e prossimi giorni
out["nba"] = {}
for dd in range(0, 4):
    day = today + dt.timedelta(days=dd)
    try:
        r = requests.get(f"{E}/basketball/nba/scoreboard", params={"dates": f"{day:%Y%m%d}"}, headers=H, timeout=30)
        evs = r.json().get("events", [])
        rows = []
        for ev in evs[:4]:
            s = requests.get(f"{E}/basketball/nba/summary", params={"event": ev["id"]}, headers=H, timeout=30).json()
            rows.append({"id": ev["id"], "date": ev["date"], "sb_odds": bool(ev["competitions"][0].get("odds")),
                         "pickcenter": (s.get("pickcenter") or [None])[0], "n_odds": len(s.get("odds") or [])})
        out["nba"][str(day)] = {"eventi": len(evs), "con_quote_tabellone": sum(bool(e["competitions"][0].get("odds")) for e in evs),
                                "campione": rows}
    except Exception as e:
        out["nba"][str(day)] = {"errore": str(e)[:300]}
# tennis: quote nel riepilogo?
try:
    r = requests.get(f"{E}/tennis/atp/scoreboard", headers=H, timeout=30).json()
    ev = r["events"][0]
    c = next(c for g in ev.get("groupings", []) for c in g.get("competitions", []))
    s = requests.get(f"{E}/tennis/atp/summary", params={"event": ev["id"], "competition": c["id"]}, headers=H, timeout=30)
    out["tennis"] = {"status": s.status_code, "keys": list(s.json().keys()) if s.ok else None,
                     "odds_comp": c.get("odds"), "pickcenter": s.json().get("pickcenter") if s.ok else None}
except Exception as e:
    out["tennis"] = {"errore": str(e)[:300]}
Path(__file__).with_name("probe_espn.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps(out["leghe"], indent=1))
