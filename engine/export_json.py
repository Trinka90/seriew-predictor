"""Aggiorna i dati (dati reali, API-Football attiva) e scrive i JSON letti dall'app (docs/data/).

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
            for r in df.head(5).itertuples()]


def _abs(a):
    return {"nome": a["player"], "motivo": a.get("motivo") or a.get("reason") or "", "fonte_dato": a.get("src"),
            "ruolo": a.get("ruolo"), "importanza": a.get("importanza"), "certezza": a.get("certezza"),
            "titolo": a.get("fonte"), "effetto": round(a["att"] + a["def"], 3)}


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
    try:
        import news_ai
        status["ia"] = news_ai.run(con)
    except Exception as e:
        status["ia"] = {"attivo": False, "errore": str(e)}
        print(f"  [!] IA notizie: {e}")
    return status


def export_league(con, lg, df_all, fx_all, manual, today):
    df = df_all[df_all.league == lg].reset_index(drop=True)
    if len(df) < 200:
        return [], [], []
    fx = fx_all[fx_all.league == lg]
    models = build_models(df, today)
    table = X.standings(df)
    rounds = C.LEAGUES[lg]["giornate"]

    def pos(t):
        if table.empty or t not in table.index:
            return None
        r = table.loc[t]
        return {"pos": int(r.pos), "pt": int(r.pt), "gf": int(r.gf), "gs": int(r.gs)}

    matches = []
    for f in fx.itertuples():
        info = predict_match(con, df, models, table, manual, f.home, f.away, rounds,
                             odds=(f.odd_h, f.odd_d, f.odd_a, f.odd_o25, f.odd_u25))
        mk = info["markets"]
        mm = info["model_markets"]
        odds = {"1": f.odd_h, "X": f.odd_d, "2": f.odd_a, "Over 2.5": f.odd_o25, "Under 2.5": f.odd_u25}
        value = []
        for k, o in odds.items():
            if o and not pd.isna(o):
                edge = mm[k] * o - 1
                if edge > C.VALUE_EDGE:
                    value.append({"mercato": k, "prob": mm[k], "quota": o, "edge": edge})
        matches.append({
            "lega": lg,
            "data": f.date.strftime("%Y-%m-%d"), "ora": f.time if f.time and f.time != "nan" else "",
            "casa": f.home, "ospite": f.away,
            "gol_casa": info["lam"], "gol_ospite": info["mu"],
            "gol_casa_base": info["lam_base"], "gol_ospite_base": info["mu_base"],
            "classifica_casa": pos(f.home), "classifica_ospite": pos(f.away),
            "forma_casa": info["form_h"], "forma_ospite": info["form_a"],
            "quote": odds, "value": value, "mercati": mk,
            "risultati": [{"r": s, "p": p} for p, s in info["scores"]],
            "assenti_casa": [_abs(a) for a in info["abs_h"]],
            "assenti_ospite": [_abs(a) for a in info["abs_a"]],
            "gol_casa_notizie": info["lam_news"], "gol_ospite_notizie": info["mu_news"],
            "eventi": info["eventi"], "effetti": info["effetti"], "con_mercato": info["mercato"],
            "note_casa": [x for x in info["coach_h"] + [info["mot_h"]] if x],
            "note_ospite": [x for x in info["coach_a"] + [info["mot_a"]] if x],
            "news_casa": _news(info["news_h"]), "news_ospite": _news(info["news_a"]),
        })

    known = set(df[df.season == C.CURRENT_SEASON].home)
    unknown = sorted({t for t in set(fx.home) | set(fx.away) if t not in known})
    if unknown:
        print(f"  [!] {lg} squadre non riconosciute: {unknown}")

    ratings = models[0].ratings()
    rows = []
    for t, r in (table.iterrows() if not table.empty else []):
        rows.append({"squadra": t, "pos": r.pos, "pg": r.pg, "pt": r.pt, "gf": r.gf, "gs": r.gs, "dr": r.dr,
                     "forma": X.form(df, t), "forza": ratings.forza.get(t)})
    return matches, rows, unknown


def export(con, days=10, status=None):
    df_all = X.load_matches(con)
    today = pd.Timestamp(dt.date.today())
    fx_all = pd.read_sql("SELECT * FROM fixtures", con, parse_dates=["date"])
    fx_all = fx_all[(fx_all.date >= today) & (fx_all.date <= today + pd.Timedelta(days=days))] \
        .sort_values(["date", "time"])
    manual = X.load_manual()

    matches, standings, unknown, per_lega = [], {}, {}, {}
    for lg in C.LEAGUES:
        m, rows, unk = export_league(con, lg, df_all, fx_all, manual, today)
        matches += m
        if rows:
            standings[lg] = rows
        if unk:
            unknown[lg] = unk
        sub = df_all[df_all.league == lg]
        per_lega[lg] = {"partite_storico": int(len(sub)), "in_programma": len(m),
                        "ultima_partita": sub.date.max().strftime("%Y-%m-%d") if len(sub) else None}
        print(f"  {C.LEAGUES[lg]['nome']}: {len(m)} partite in programma")
    matches.sort(key=lambda x: (x["data"], x["ora"] or "99", list(C.LEAGUES).index(x["lega"])))

    leghe = {k: {"nome": v["nome"], "flag": v["flag"]} for k, v in C.LEAGUES.items()
             if per_lega[k]["partite_storico"] >= 200}
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="minutes")
    _write("predictions.json", {"aggiornato": now, "leghe": leghe, "partite": matches})
    _write("standings.json", {"aggiornato": now, "stagione": C.CURRENT_SEASON, "leghe": standings})

    meta_path = C.DOCS_DATA / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if not isinstance(meta.get("backtest"), dict) or "stagione" in meta.get("backtest", {}):
        meta["backtest"] = {}   # vecchio formato (solo Serie A)
    meta.update({"aggiornato": now, "partite_storico": int(len(df_all)),
                 "ultima_partita": df_all.date.max().strftime("%Y-%m-%d"),
                 "per_lega": per_lega, "fonti": status or meta.get("fonti", {}), "api_football": bool(C.API_FOOTBALL_KEY),
                 "modello": {"xi": C.XI, "tiri_in_porta": C.SOT_WEIGHT, "peso_mercato": C.MARKET_WEIGHT},
                 "api_log": D.API_LOG[:10], "squadre_non_riconosciute": unknown})
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
    if con.execute("SELECT COUNT(*) FROM matches WHERE league='I1'").fetchone()[0] < 300:
        print("ERRORE: storico insufficiente (download fallito?) - non sovrascrivo i JSON")
        sys.exit(1)
    meta = export(con, a.giorni, status)
    if a.backtest:
        import backtest
        prev = str(int(C.CURRENT_SEASON[:2]) - 1).zfill(2) + C.CURRENT_SEASON[:2]
        for lg in C.LEAGUES:
            r = backtest.run(prev, league=lg)
            if r:
                meta["backtest"][lg] = r
        _write("meta.json", meta)


if __name__ == "__main__":
    main()
