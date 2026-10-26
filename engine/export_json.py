"""Aggiorna i dati e scrive i JSON letti dall'app (docs/data/).

  python export_json.py                 # update completo + pronostici
  python export_json.py --backtest      # in più ricalcola l'affidabilità sulla stagione scorsa
"""
import argparse
import datetime as dt
import json
import math
import sys

import pandas as pd

import config as C
import context as X
import data_sources as D
from db import connect
from predict import build_models, predict_match

KEY_MARKETS = ["1", "X", "2", "Over 2.5", "Under 2.5"]


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 4)
    if hasattr(o, "item"):
        return _clean(o.item())
    return o


def _write(name, obj):
    C.DOCS_DATA.mkdir(parents=True, exist_ok=True)
    (C.DOCS_DATA / name).write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")


def _news(df):
    return [{"data": r.published, "tag": r.tags, "titolo": r.title, "fonte": r.source,
             "link": r.link.split("#")[0]}
            for r in df.head(8).itertuples()]


def update_all(con):
    status = {}
    status["storico"] = D.update_history(C.SEASONS, con)
    status["calendario"] = D.update_fixtures(con)
    if C.API_FOOTBALL_KEY:
        try:
            status["infortuni"] = D.update_injuries(con)
            if dt.date.today().weekday() == 0:          # allenatori: una volta a settimana
                status["allenatori"] = D.update_coaches(con)
        except Exception as e:
            status["api_errore"] = str(e)
    status["notizie"] = D.update_news(con)
    return status


def export(con, days=10, status=None):
    df = X.load_matches(con)
    today = pd.Timestamp(dt.date.today())
    fx = pd.read_sql("SELECT * FROM fixtures", con, parse_dates=["date"])
    fx = fx[(fx.date >= today) & (fx.date <= today + pd.Timedelta(days=days))].sort_values(["date", "time"])
    models = build_models(df, today)
    table = X.standings(df)
    manual = X.load_manual()

    def pos(t):
        if table.empty or t not in table.index:
            return None
        r = table.loc[t]
        return {"pos": int(r.pos), "pt": int(r.pt), "gf": int(r.gf), "gs": int(r.gs)}

    matches = []
    for f in fx.itertuples():
        info = predict_match(con, df, models, table, manual, f.home, f.away)
        mk = info["markets"]
        odds = {"1": f.odd_h, "X": f.odd_d, "2": f.odd_a, "Over 2.5": f.odd_o25, "Under 2.5": f.odd_u25}
        value = []
        for k, o in odds.items():
            if o and not pd.isna(o):
                edge = mk[k] * o - 1
                if edge > C.VALUE_EDGE:
                    value.append({"mercato": k, "prob": mk[k], "quota": o, "edge": edge})
        matches.append({
            "data": f.date.strftime("%Y-%m-%d"), "ora": f.time if f.time and f.time != "nan" else "",
            "casa": f.home, "ospite": f.away,
            "gol_casa": info["lam"], "gol_ospite": info["mu"],
            "gol_casa_base": info["lam_base"], "gol_ospite_base": info["mu_base"],
            "classifica_casa": pos(f.home), "classifica_ospite": pos(f.away),
            "forma_casa": info["form_h"], "forma_ospite": info["form_a"],
            "quote": odds, "value": value, "mercati": mk,
            "risultati": [{"r": s, "p": p} for p, s in info["scores"]],
            "assenti_casa": [{"nome": a["player"], "motivo": a["reason"]} for a in info["abs_h"]],
            "assenti_ospite": [{"nome": a["player"], "motivo": a["reason"]} for a in info["abs_a"]],
            "note_casa": [x for x in info["coach_h"] + [info["mot_h"]] if x],
            "note_ospite": [x for x in info["coach_a"] + [info["mot_a"]] if x],
            "news_casa": _news(info["news_h"]), "news_ospite": _news(info["news_a"]),
        })

    ratings = models[0].ratings()
    rows = []
    for t, r in (table.iterrows() if not table.empty else []):
        rows.append({"squadra": t, "pos": r.pos, "pg": r.pg, "pt": r.pt, "gf": r.gf, "gs": r.gs, "dr": r.dr,
                     "forma": X.form(df, t),
                     "attacco": ratings.attacco.get(t), "difesa": ratings.difesa.get(t),
                     "forza": ratings.forza.get(t)})

    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="minutes")
    _write("predictions.json", {"aggiornato": now, "partite": matches})
    _write("standings.json", {"aggiornato": now, "stagione": C.CURRENT_SEASON, "righe": rows})

    meta_path = C.DOCS_DATA / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update({"aggiornato": now, "partite_storico": int(len(df)),
                 "ultima_partita": df.date.max().strftime("%Y-%m-%d"),
                 "fonti": status or {}, "api_football": bool(C.API_FOOTBALL_KEY)})
    _write("meta.json", meta)
    print(f"JSON scritti: {len(matches)} partite in programma")
    return meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-update", action="store_true")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--giorni", type=int, default=10)
    a = p.parse_args()
    con = connect()
    status = None if a.no_update else update_all(con)
    if con.execute("SELECT COUNT(*) FROM matches").fetchone()[0] < 300:
        print("ERRORE: storico insufficiente (download fallito?) - non sovrascrivo i JSON")
        sys.exit(1)
    meta = export(con, a.giorni, status)
    if a.backtest:
        import backtest
        prev = str(int(C.CURRENT_SEASON[:2]) - 1).zfill(2) + C.CURRENT_SEASON[:2]
        meta["backtest"] = backtest.run(prev)
        _write("meta.json", meta)


if __name__ == "__main__":
    main()
