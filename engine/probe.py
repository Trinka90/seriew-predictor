"""Verifica quali fonti dati gratuite rispondono (eseguito in GitHub Actions)."""
import json
from pathlib import Path

import requests

H = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
URLS = {
    "td_atp_2026_xlsx": "http://www.tennis-data.co.uk/2026/2026.xlsx",
    "td_atp_2025_xlsx": "http://www.tennis-data.co.uk/2025/2025.xlsx",
    "td_wta_2026_xlsx": "http://www.tennis-data.co.uk/2026w/2026.xlsx",
    "td_wta_2025_xlsx": "http://www.tennis-data.co.uk/2025w/2025.xlsx",
    "sack_atp_2026": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2026.csv",
    "sack_atp_2025": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2025.csv",
    "sack_wta_2025": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_2025.csv",
    "espn_atp": "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
    "espn_wta": "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard",
    "espn_nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "espn_atp_rank": "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings",
}

out = {}
for k, u in URLS.items():
    try:
        r = requests.get(u, headers=H, timeout=30)
        info = {"status": r.status_code, "bytes": len(r.content), "type": r.headers.get("content-type", "")}
        if "json" in info["type"]:
            j = r.json()
            info["keys"] = list(j)[:12]
            info["sample"] = json.dumps(j)[:3000]
        elif u.endswith(".csv"):
            info["sample"] = r.text[:600] + "\n...\n" + r.text[-400:]
        elif u.endswith(".xlsx") and r.status_code == 200:
            import io
            import pandas as pd
            df = pd.read_excel(io.BytesIO(r.content))
            info["cols"] = list(df.columns)
            info["rows"] = len(df)
            info["last"] = df.tail(3).astype(str).to_dict("records")
        out[k] = info
    except Exception as e:
        out[k] = {"errore": str(e)[:300]}

p = Path(__file__).resolve().parent.parent / "experiments" / "probe.json"
p.parent.mkdir(exist_ok=True)
p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps({k: {kk: v for kk, v in i.items() if kk != "sample"} for k, i in out.items()}, indent=1)[:5000])
