"""Verifica la struttura dei dati ESPN (tennis e NBA), eseguito in GitHub Actions."""
import json
from pathlib import Path

import requests

H = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
B = "https://site.api.espn.com/apis/site/v2/sports"
out = {}


def get(u):
    r = requests.get(u, headers=H, timeout=30)
    return r.status_code, (r.json() if "json" in r.headers.get("content-type", "") else r.text[:300])


def trim(o, depth=0):
    """Riduce liste lunghe per leggere la struttura."""
    if isinstance(o, dict):
        return {k: trim(v, depth + 1) for k, v in o.items() if k not in ("logos", "links", "calendar", "headshot", "flag")}
    if isinstance(o, list):
        return [trim(x, depth + 1) for x in o[:2]] + ([f"... +{len(o) - 2}"] if len(o) > 2 else [])
    return o


for name, url in {
    "atp_oggi": f"{B}/tennis/atp/scoreboard",
    "atp_passato": f"{B}/tennis/atp/scoreboard?dates=20260705",
    "atp_range": f"{B}/tennis/atp/scoreboard?dates=20260601-20260607",
    "wta_oggi": f"{B}/tennis/wta/scoreboard",
    "nba_oggi": f"{B}/basketball/nba/scoreboard",
    "nba_passato": f"{B}/basketball/nba/scoreboard?dates=20260310",
}.items():
    try:
        st, j = get(url)
        info = {"status": st}
        if isinstance(j, dict):
            ev = j.get("events", [])
            info["n_events"] = len(ev)
            ncomp = 0
            for e in ev:
                ncomp += len(e.get("competitions", []))
                for g in e.get("groupings", []):
                    ncomp += len(g.get("competitions", []))
            info["n_competitions"] = ncomp
            info["event0"] = trim(ev[0]) if ev else None
        else:
            info["body"] = j
        out[name] = info
    except Exception as e:
        out[name] = {"errore": str(e)[:300]}

p = Path(__file__).resolve().parent.parent / "experiments" / "probe2.json"
p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
print({k: (v.get("status"), v.get("n_events"), v.get("n_competitions")) for k, v in out.items()})
