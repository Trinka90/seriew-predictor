"""Sonda ESPN: infortuni NBA (endpoint dedicato e riepilogo partita) e leader di squadra. Solo diagnostica."""
import datetime as dt
import json
from pathlib import Path

import requests

E = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
H = {"User-Agent": "Mozilla/5.0"}
out = {}


def cut(x, n=3):
    if isinstance(x, dict):
        return {k: cut(v, n) for k, v in x.items() if k not in ("links", "logos", "headshot", "logo", "$ref")}
    if isinstance(x, list):
        return [cut(v, n) for v in x[:n]]
    return x


try:
    r = requests.get(f"{E}/injuries", headers=H, timeout=30)
    out["injuries_status"] = r.status_code
    out["injuries"] = cut(r.json(), 2) if r.ok else r.text[:300]
except Exception as e:
    out["injuries"] = str(e)
today = dt.date.today()
for dd in (0, 1):
    try:
        day = today + dt.timedelta(days=dd)
        ev = requests.get(f"{E}/scoreboard", params={"dates": f"{day:%Y%m%d}"}, headers=H, timeout=30).json()["events"][0]
        s = requests.get(f"{E}/summary", params={"event": ev["id"]}, headers=H, timeout=30).json()
        out[f"summary_{dd}"] = {"keys": list(s.keys()), "injuries": cut(s.get("injuries"), 2), "leaders": cut(s.get("leaders"), 2)}
    except Exception as e:
        out[f"summary_{dd}"] = str(e)
Path(__file__).with_name("probe_espn.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
