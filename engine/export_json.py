"""Aggiorna i dati (dati reali, API-Football attiva) e scrive i JSON letti dall'app (docs/data/).

  python export_json.py                 # update completo + pronostici
  python export_json.py --backtest      # in più ricalcola l'affidabilità sulla stagione scorsa
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
import traceback

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
            "titolo": a.get("fonte"), "effetto": round(a["att"] + a["def"], 3), "memoria": bool(a.get("memoria"))}


def _step(status, name, fn, *args):
    """Esegue un passaggio; se fallisce lo annota e prosegue con gli altri."""
    t = time.time()
    try:
        status[name] = fn(*args)
    except Exception as e:
        status[name] = {"errore": str(e)[:200]}
        print(f"  [!] {name}: {e}")
        traceback.print_exc()
    print(f"  ({name}: {time.time() - t:.0f}s)")


EST_KEYS = ["1X", "X2", "12", "Over 1.5", "Over 3.5", "Under 1.5", "Under 3.5", "GG", "NG",
            "Multigol 1-3", "Multigol 2-4", "Multigol 2-3", "1 + Over 1.5", "2 + Over 1.5", "1 + GG", "2 + GG",
            "GG + Over 2.5"]


def _estimated(info, odds, f):
    """Quote Bet365 stimate per i mercati senza quota nei dati: probabilità del mercato + margine Bet365 della partita."""
    bm = info.get("book_markets")
    b3 = [f.bk_h, f.bk_d, f.bk_a]
    if not bm or not all(x and not pd.isna(x) and x > 1 for x in b3):
        return {}
    marg = sum(1 / x for x in b3) - 1
    return {k: round(max(1.01, 1 / (bm[k] * (1 + marg))), 2) for k in EST_KEYS if k in bm and k not in odds and bm[k] > 0}


def _semaforo(info, home, away, df, day, drift=None):
    """Semaforo SOLO informativo: segnala le situazioni particolari, non cambia pronostico né schedina.
    (Nel backtest 25/26 queste partite non sono uscite meno delle altre: serve a capire, non a scartare.)"""
    mot = []
    for side, team in (("h", home), ("a", away)):
        big = [a for a in info[f"abs_{side}"] if a.get("importanza") in ("stella", "titolare")]
        if any(a.get("importanza") == "stella" for a in big) or len(big) >= 2:
            mot.append(f"{team}: {len(big)} assent{'e importante' if len(big) == 1 else 'i importanti'} "
                       f"({', '.join(a['player'] for a in big[:3])})")
    ev = info.get("eventi") or {}
    for side, team in (("casa", home), ("ospite", away)):
        to = (ev.get(side) or {}).get("turnover")
        if to and to != "nessuno":
            mot.append(f"{team}: previsto turnover {to}")
        if (ev.get(side) or {}).get("nuovo_allenatore"):
            mot.append(f"{team}: nuovo allenatore")
    if ev.get("porte_chiuse"):
        mot.append("porte chiuse")
    if ev.get("campo_neutro"):
        mot.append("campo neutro")
    mm, bm = info.get("model_markets"), info.get("book_markets")
    if mm and bm:
        k = max(("1", "X", "2"), key=lambda x: abs(mm[x] - bm[x]))
        if abs(mm[k] - bm[k]) >= 0.10:
            mot.append(f"modello e quote non d'accordo sul segno {k} (modello {mm[k]:.0%}, quote {bm[k]:.0%})")
    if drift and abs(drift[1]) >= 0.05:
        mot.append(f"quote cambiate nelle ultime ore sul segno {drift[0]} ({drift[1]:+.0%}): forse c'è una notizia")
    lo = day - pd.Timedelta(days=365)
    for team in (home, away):
        played = df[((df.home == team) | (df.away == team)) & (df.date >= lo)]
        if len(played) < 15:
            mot.append(f"{team}: pochi dati in questo campionato (neopromossa?)")
        elif len(played):
            gap = (day - played.date.max()).days
            if 0 < gap <= 3:
                mot.append(f"{team}: ha giocato {gap} giorn{'o' if gap == 1 else 'i'} fa (calendario fitto)")
    return {"livello": "giallo" if mot else "verde", "motivi": mot}


def update_all(con):
    import news_ai
    status = {}
    _step(status, "storico", D.update_history, C.SEASONS, con)
    _step(status, "calendario", D.update_fixtures, con)
    _step(status, "espn_calcio", D.update_espn_soccer, con)
    if C.API_FOOTBALL_KEY:
        _step(status, "infortuni", D.update_injuries, con)
    _step(status, "notizie", D.update_news, con)
    _step(status, "ia", news_ai.run, con)
    return status


def _espn_rows(con, lg):
    try:
        rows = pd.read_sql("SELECT * FROM espn_soccer WHERE league=?", con, params=(lg,))
    except Exception:
        return {}
    out = {}
    for r in rows.itertuples():
        out.setdefault((r.home, r.away), []).append(r)
    return out


def _ok(x):
    return x is not None and not pd.isna(x) and x > 1


def _combo_odds(f, e):
    """Probabilità del mercato: media bookmaker (football-data) e quote ESPN aggiornate a ridosso, insieme.
    Ritorna quote 'giuste' (senza margine) da passare al modello, la variazione delle quote e la fonte."""
    from model import market_probs
    pa = market_probs(f.odd_h, f.odd_d, f.odd_a, f.odd_o25, f.odd_u25) if _ok(f.odd_h) else None
    pe = market_probs(e.o_h, e.o_d, e.o_a, e.o_o25, e.o_u25) if e is not None and _ok(e.o_h) and _ok(e.o_d) and _ok(e.o_a) else None
    drift = None
    if e is not None and pe and all(_ok(x) for x in (e.op_h, e.op_d, e.op_a)):
        po = market_probs(e.op_h, e.op_d, e.op_a)["1x2"]
        d = pe["1x2"] - po
        i = int(abs(d).argmax())
        drift = (["1", "X", "2"][i], float(d[i]))
    if not pa and not pe:
        return (f.odd_h, f.odd_d, f.odd_a, f.odd_o25, f.odd_u25), drift, None
    if pa and pe:
        w = C.ESPN_WEIGHT
        p = (1 - w) * pa["1x2"] + w * pe["1x2"]
        ov = [x["over"] for x in (pa, pe) if "over" in x]
        over = sum(ov) / len(ov) if ov else None
        fonte = "media bookmaker + DraftKings aggiornata"
    else:
        x = pa or pe
        p, over = x["1x2"], x.get("over")
        fonte = "media bookmaker" if pa else "DraftKings aggiornata"
    odds = (1 / p[0], 1 / p[1], 1 / p[2], 1 / over if over else None, 1 / (1 - over) if over else None)
    return odds, drift, fonte


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
    espn = _espn_rows(con, lg)
    for f in fx.itertuples():
        e = min((r for r in espn.get((f.home, f.away), [])
                 if abs((pd.Timestamp(r.date) - f.date).days) <= 1), key=lambda r: r.date, default=None)
        odds_p, drift, fonte = _combo_odds(f, e)
        ora = f.time if f.time and f.time != "nan" else (e.time if e is not None else "")
        info = predict_match(con, df, models, table, manual, f.home, f.away, rounds, odds=odds_p)
        mk = info["markets"]
        mm = info["model_markets"]
        odds = {"1": f.bk_h, "X": f.bk_d, "2": f.bk_a, "Over 2.5": f.bk_o25, "Under 2.5": f.bk_u25}   # Bet365
        odds = {k: v for k, v in odds.items() if v and not pd.isna(v)}
        value = []
        for k, o in odds.items():
            if o and not pd.isna(o):
                edge = mm[k] * o - 1
                if edge > C.VALUE_EDGE:
                    value.append({"mercato": k, "prob": mm[k], "quota": o, "edge": edge})
        matches.append({
            "sport": "calcio", "lega": lg,
            "data": f.date.strftime("%Y-%m-%d"), "ora": ora, "fonte_quote": fonte if info["mercato"] else None,
            "casa": f.home, "ospite": f.away,
            "gol_casa": info["lam"], "gol_ospite": info["mu"],
            "gol_casa_base": info["lam_base"], "gol_ospite_base": info["mu_base"],
            "classifica_casa": pos(f.home), "classifica_ospite": pos(f.away),
            "forma_casa": info["form_h"], "forma_ospite": info["form_a"],
            "quote": odds, "bookmaker": C.BOOKMAKER if odds else None, "value": value, "mercati": mk,
            "quote_stimate": _estimated(info, odds, f),
            "risultati": [{"r": s, "p": p} for p, s in info["scores"]],
            "assenti_casa": [_abs(a) for a in info["abs_h"]],
            "assenti_ospite": [_abs(a) for a in info["abs_a"]],
            "gol_casa_notizie": info["lam_news"], "gol_ospite_notizie": info["mu_news"],
            "eventi": info["eventi"], "effetti": info["effetti"], "con_mercato": info["mercato"],
            "note_casa": [x for x in info["coach_h"] + [info["mot_h"]] if x],
            "note_ospite": [x for x in info["coach_a"] + [info["mot_a"]] if x],
            "news_casa": _news(info["news_h"]), "news_ospite": _news(info["news_a"]),
            "semaforo": _semaforo(info, f.home, f.away, df, f.date, drift),
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
    errori = {}
    for lg in C.LEAGUES:
        try:
            m, rows, unk = export_league(con, lg, df_all, fx_all, manual, today)
        except Exception as e:   # un campionato con problemi non blocca gli altri
            errori[lg] = str(e)[:200]
            print(f"  [!] {lg}: {e}")
            traceback.print_exc()
            m, rows, unk = [], [], []
        matches += m
        if rows:
            standings[lg] = rows
        if unk:
            unknown[lg] = unk
        sub = df_all[df_all.league == lg]
        per_lega[lg] = {"partite_storico": int(len(sub)), "in_programma": len(m),
                        "ultima_partita": sub.date.max().strftime("%Y-%m-%d") if len(sub) else None}
        print(f"  {C.LEAGUES[lg]['nome']}: {len(m)} partite in programma")
    leghe = {k: {"nome": v["nome"], "flag": v["flag"], "sport": "calcio"} for k, v in C.LEAGUES.items()
             if per_lega[k]["partite_storico"] >= 200}

    # altri sport (tennis, NBA): mai bloccanti
    sport_bt = {}
    try:
        import sports
        sm, stabs, sport_bt, sstat = sports.run_all()
        matches += [_clean(m) for m in sm]
        standings.update(stabs)
        for code, cfg in sports.SPORTS.items():
            leghe[code] = {"nome": cfg["nome"], "flag": cfg["flag"], "sport": cfg["sport"]}
        (status if status is not None else {}).update({"altri_sport": sstat})
        print(f"  altri sport: {sstat}")
    except Exception as e:
        traceback.print_exc()
        errori["altri_sport"] = str(e)[:200]
    order = list(C.LEAGUES) + ["ATP", "WTA", "NBA"]
    matches.sort(key=lambda x: (x["data"], x["ora"] or "99", order.index(x["lega"]) if x["lega"] in order else 99))
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="minutes")
    _write("predictions.json", {"aggiornato": now, "leghe": leghe, "partite": matches})
    try:   # pagella: pronostici consigliati e come sono andati (mai bloccante)
        import pagella
        _write("pagella.json", pagella.update(matches, con))
    except Exception as e:
        traceback.print_exc()
        errori["pagella"] = str(e)[:200]
    _write("standings.json", {"aggiornato": now, "stagione": C.CURRENT_SEASON, "leghe": standings})

    meta_path = C.DOCS_DATA / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if not isinstance(meta.get("backtest"), dict) or "stagione" in meta.get("backtest", {}):
        meta["backtest"] = {}   # vecchio formato (solo Serie A)
    meta["backtest"].update(sport_bt)
    meta.update({"aggiornato": now, "partite_storico": int(len(df_all)),
                 "ultima_partita": df_all.date.max().strftime("%Y-%m-%d"),
                 "per_lega": per_lega, "fonti": status or meta.get("fonti", {}), "api_football": bool(C.API_FOOTBALL_KEY),
                 "modello": {"xi": C.XI, "tiri_in_porta": C.SOT_WEIGHT, "peso_mercato": C.MARKET_WEIGHT},
                 "api_log": D.API_LOG[:10], "squadre_non_riconosciute": unknown, "errori_campionati": errori,
                 "stato": "ok"})
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
    n_hist = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    if n_hist < 2000:
        # download fallito: si tengono i pronostici precedenti, si segnala soltanto
        print(f"ATTENZIONE: storico insufficiente ({n_hist} partite) - tengo i dati precedenti")
        _mark_failed(f"download dati non riuscito ({n_hist} partite)", status)
        return
    try:
        meta = export(con, a.giorni, status)
    except Exception as e:
        traceback.print_exc()
        _mark_failed(f"calcolo non riuscito: {e}", status)
        return
    if a.backtest:
        import backtest
        prev = str(int(C.CURRENT_SEASON[:2]) - 1).zfill(2) + C.CURRENT_SEASON[:2]
        for lg in C.LEAGUES:
            try:
                r = backtest.run(prev, league=lg)
                if r:
                    meta["backtest"][lg] = r
            except Exception as e:
                print(f"  [!] backtest {lg}: {e}")
        _write("meta.json", meta)


def _mark_failed(msg, status):
    path = C.DOCS_DATA / "meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        meta = {}
    meta.update({"stato": "errore", "errore": msg, "tentativo": dt.datetime.now().isoformat(timespec="minutes"),
                 "fonti_ultimo_tentativo": status or {}})
    _write("meta.json", meta)


if __name__ == "__main__":
    try:
        main()
    except Exception:   # mai far fallire il giro: i dati precedenti restano validi
        traceback.print_exc()
        _mark_failed("errore imprevisto", None)
