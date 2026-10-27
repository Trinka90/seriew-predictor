"""Verifica fonti per serie minori (eseguito in GitHub Actions)."""
import io
import json
from pathlib import Path

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
out = {}


def get(u):
    r = requests.get(u, headers=H, timeout=30)
    return r


# 1) football-data: serie minori nel formato principale + colonne bookmaker nel calendario
for code in ["E2", "E3", "EC", "SC1", "SC2", "SC3", "G1"]:
    u = f"https://www.football-data.co.uk/mmz4281/2627/{code}.csv"
    try:
        r = get(u)
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", "replace")), on_bad_lines="skip")
        out[f"fd_{code}"] = {"status": r.status_code, "righe": len(df), "B365": "B365H" in df.columns,
                             "squadre": sorted(set(df.get("HomeTeam", pd.Series(dtype=str)).dropna()))[:6]}
    except Exception as e:
        out[f"fd_{code}"] = {"errore": str(e)[:200]}
try:
    r = get("https://www.football-data.co.uk/fixtures.csv")
    df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", "replace")), on_bad_lines="skip")
    out["fixtures_cols"] = [c for c in df.columns if c.startswith(("B365", "Avg", "PS", "Max"))]
    out["fixtures_div"] = df["Div"].value_counts().to_dict()
except Exception as e:
    out["fixtures"] = {"errore": str(e)[:200]}
# extra leagues (formato "new")
for c in ["AUT", "SWZ", "DNK", "POL"]:
    try:
        r = get(f"https://www.football-data.co.uk/new/{c}.csv")
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", "replace")), on_bad_lines="skip")
        out[f"new_{c}"] = {"status": r.status_code, "righe": len(df), "cols": list(df.columns)[:30],
                           "stagioni": sorted(df["Season"].astype(str).unique())[-3:] if "Season" in df else None}
    except Exception as e:
        out[f"new_{c}"] = {"errore": str(e)[:200]}
try:
    r = get("https://www.football-data.co.uk/new_league_fixtures.csv")
    out["new_fixtures"] = {"status": r.status_code, "inizio": r.text[:300]}
except Exception as e:
    out["new_fixtures"] = {"errore": str(e)[:200]}

# 2) Serie C: ESPN e fixturedownload
B = "https://site.api.espn.com/apis/site/v2/sports/soccer"
for slug in ["ita.3", "ita.c", "ita.serie_c", "ita.2", "ger.3", "esp.3", "fra.3", "eng.3", "eng.4"]:
    try:
        r = get(f"{B}/{slug}/scoreboard")
        j = r.json() if "json" in r.headers.get("content-type", "") else {}
        ev = j.get("events", [])
        out[f"espn_{slug}"] = {"status": r.status_code, "lega": (j.get("leagues") or [{}])[0].get("name"),
                               "eventi": len(ev), "esempio": ev[0]["name"] if ev else None,
                               "odds": bool(ev and ev[0].get("competitions", [{}])[0].get("odds"))}
    except Exception as e:
        out[f"espn_{slug}"] = {"errore": str(e)[:200]}
try:
    r = get(f"{B}/ita.3/scoreboard?dates=20260301")
    j = r.json()
    out["espn_ita3_storico"] = {"eventi": len(j.get("events", [])),
                                "esempio": [e["name"] for e in j.get("events", [])[:3]]}
except Exception as e:
    out["espn_ita3_storico"] = {"errore": str(e)[:200]}
for slug in ["serie-c-2026", "serie-c-girone-a-2026", "lega-pro-2026"]:
    try:
        r = get(f"https://fixturedownload.com/feed/json/{slug}")
        out[f"fxd_{slug}"] = {"status": r.status_code, "n": len(r.json()) if r.status_code == 200 else 0}
    except Exception as e:
        out[f"fxd_{slug}"] = {"errore": str(e)[:200]}
# elenco leghe calcio ESPN (cerca Italia)
try:
    r = get("https://site.api.espn.com/apis/site/v2/leagues/dropdown?sport=soccer&limit=500")
    j = r.json()
    lg = j.get("leagues", [])
    out["espn_leghe_ita"] = [(x.get("slug"), x.get("name")) for x in lg if "ita" in (x.get("slug") or "")]
    out["espn_leghe_n"] = len(lg)
except Exception as e:
    out["espn_leghe"] = {"errore": str(e)[:200]}

p = Path(__file__).resolve().parent.parent / "experiments" / "probe3.json"
p.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
print(json.dumps(out, indent=1, ensure_ascii=False, default=str)[:6000])
