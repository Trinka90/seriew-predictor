"""Diagnostica calendario/quote (eseguito in GitHub Actions)."""
import json
import traceback
from pathlib import Path

import config as C
import data_sources as D
from db import connect

out = {}
con = connect(":memory:")
try:
    df = D._csv(C.FIXTURES_URL)
    out["colonne"] = list(df.columns)[:12]
    out["div"] = df["Div"].astype(str).str.strip().value_counts().to_dict() if "Div" in df else "manca Div"
    out["date"] = sorted(df["Date"].astype(str).unique())[:10] if "Date" in df else None
except Exception:
    out["csv_errore"] = traceback.format_exc()[-1500:]
try:
    n = D.update_fixtures(con)
    out["n"] = n
    rows = con.execute("SELECT league, COUNT(*), SUM(bk_h IS NOT NULL), SUM(odd_h IS NOT NULL), MIN(date), MAX(date) "
                       "FROM fixtures GROUP BY league").fetchall()
    out["per_lega"] = rows
except Exception:
    out["fix_errore"] = traceback.format_exc()[-1500:]
p = Path(__file__).resolve().parent.parent / "experiments" / "probe4.json"
p.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
