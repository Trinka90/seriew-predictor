"""Fattori di contesto attuali: classifica, forma, assenze, motivazioni, allenatore, notizie."""
import datetime as dt
import json

import pandas as pd

import config as C

DEFAULT_IMPACT = 0.03   # peso di un assente "generico" (da API, ruolo ignoto)
CAP = 0.25              # massimo effetto cumulato assenze


def load_matches(con):
    df = pd.read_sql("SELECT * FROM matches", con, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def standings(df, season=C.CURRENT_SEASON):
    rows = {}
    for r in df[df.season == season].itertuples():
        for team, gf, ga in ((r.home, r.fthg, r.ftag), (r.away, r.ftag, r.fthg)):
            s = rows.setdefault(team, {"team": team, "pg": 0, "pt": 0, "gf": 0, "gs": 0})
            s["pg"] += 1
            s["gf"] += gf
            s["gs"] += ga
            s["pt"] += 3 if gf > ga else (1 if gf == ga else 0)
    t = pd.DataFrame(rows.values())
    if t.empty:
        return t
    t["dr"] = t.gf - t.gs
    t = t.sort_values(["pt", "dr", "gf"], ascending=False).reset_index(drop=True)
    t["pos"] = t.index + 1
    return t.set_index("team")


def form(df, team, n=5):
    d = df[(df.home == team) | (df.away == team)].tail(n)
    out = ""
    for r in d.itertuples():
        gf, ga = (r.fthg, r.ftag) if r.home == team else (r.ftag, r.fthg)
        out += "V" if gf > ga else ("N" if gf == ga else "P")
    return out


def load_manual():
    try:
        return json.loads(C.MANUAL_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def absences(con, team, manual):
    """Assenti con effetto separato su gol fatti (att) e subiti (def)."""
    out = []
    for player, reason, src, ia, idf, info in con.execute(
            "SELECT player, reason, source, impact_att, impact_def, info FROM absences WHERE team=?", (team,)):
        if ia is None:   # fonte senza ruolo (API): effetto generico
            w = 0.5 if "questionable" in (reason or "").lower() else 1.0
            ia, idf = DEFAULT_IMPACT * w / 2, DEFAULT_IMPACT * w / 2
        out.append({"player": player, "reason": reason, "src": src, "att": ia, "def": idf,
                    **(json.loads(info) if info else {})})
    for a in manual.get("absences", {}).get(team, []):
        out = [o for o in out if o["player"].lower() != a["player"].lower()]
        imp, role = float(a.get("impact", 0.08)), a.get("role", "att")
        att, de = (imp, 0) if role == "att" else (0, imp) if role == "def" else (imp / 2, imp / 2)
        out.append({"player": a["player"], "reason": a.get("reason", "manuale"), "src": "manuale",
                    "att": att, "def": de})
    return out


def absence_multipliers(abs_list):
    """-> (moltiplicatore gol fatti, moltiplicatore gol subiti)"""
    att = sum(a["att"] for a in abs_list)
    de = sum(a["def"] for a in abs_list)
    return 1 - min(att, CAP), 1 + min(de, CAP)


def match_event(con, home, away):
    r = con.execute("SELECT data FROM match_events WHERE home=? AND away=?", (home, away)).fetchone()
    return json.loads(r[0]) if r else None


def motivation(table, team, total_rounds=38):
    """Ultime giornate: squadra salva e fuori dalla corsa Europa -> leggero calo."""
    if table.empty or team not in table.index:
        return 1.0, ""
    pg = table.loc[team, "pg"]
    if pg < 30:
        return 1.0, ""
    left = total_rounds - pg
    pts, me, n = table.pt, table.loc[team, "pt"], len(table)
    safe = me - pts.iloc[n - 3] > 3 * left
    no_europe = me + 3 * left < pts.iloc[min(6, n - 1)]
    if safe and no_europe:
        return 0.95, "niente da chiedere"
    if not safe:
        return 1.0, "lotta salvezza"
    return 1.0, "corsa Europa/titolo"


def coach_notes(con, team, news):
    notes = []
    r = con.execute("SELECT coach, since FROM coaches WHERE team=?", (team,)).fetchone()
    if r:
        notes.append(f"all. {r[0]}" + (f" dal {r[1]}" if r[1] else ""))
        if r[1]:
            try:
                if (dt.date.today() - dt.date.fromisoformat(r[1][:10])).days <= 30:
                    notes.append("NUOVO ALLENATORE (<30gg)")
            except ValueError:
                pass
    if any("allenatore" in (t or "") for t in news.tags):
        notes.append("news: panchina in discussione/cambio")
    return notes


def news_for(con, team, days=C.NEWS_DAYS):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return pd.read_sql("SELECT published, tags, title, source, link FROM news WHERE team=? AND published>=? "
                       "ORDER BY published DESC", con, params=(team, since))


def manual_adjust(manual, team):
    a = manual.get("adjust", {}).get(team, {})
    return float(a.get("att", 1.0)), float(a.get("def", 1.0))
