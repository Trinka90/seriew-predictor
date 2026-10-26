"""Raccolta dati: storico/quote, calendario, infortuni, allenatori, notizie."""
import datetime as dt
import email.utils
import io
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests

import config as C
from db import connect
from teams import canonical, news_query, teams_in_text

MATCH_COLS = ["season", "date", "home", "away", "fthg", "ftag", "hthg", "htag",
              "hs", "as_", "hst", "ast", "hc", "ac", "hy", "ay", "hr", "ar",
              "odd_h", "odd_d", "odd_a", "odd_o25", "odd_u25"]
STAT_MAP = {"fthg": "FTHG", "ftag": "FTAG", "hthg": "HTHG", "htag": "HTAG",
            "hs": "HS", "as_": "AS", "hst": "HST", "ast": "AST", "hc": "HC",
            "ac": "AC", "hy": "HY", "ay": "AY", "hr": "HR", "ar": "AR"}
ODDS = {"h": ["AvgH", "B365H", "PSH"], "d": ["AvgD", "B365D", "PSD"],
        "a": ["AvgA", "B365A", "PSA"], "o25": ["Avg>2.5", "B365>2.5", "P>2.5"],
        "u25": ["Avg<2.5", "B365<2.5", "P<2.5"]}


def _get(url, **kw):
    r = requests.get(url, headers=C.HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


def _csv(url):
    txt = _get(url).content.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(txt), on_bad_lines="skip")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    return df


def _num(df, col):
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _odds(df, kind):
    for c in ODDS[kind]:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _rows(out):
    return out.astype(object).where(out.notna(), None).values.tolist()


# ----------------------------------------------------------------- storico
def update_history(seasons=C.SEASONS, con=None):
    con = con or connect()
    tot = 0
    for s in seasons:
        try:
            df = _csv(C.HIST_URL.format(season=s))
        except Exception as e:
            print(f"  [!] stagione {s}: {e}")
            continue
        df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG"])
        if df.empty:
            continue
        out = pd.DataFrame({
            "season": s,
            "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d"),
            "home": df["HomeTeam"].str.strip(), "away": df["AwayTeam"].str.strip(),
            **{k: _num(df, v) for k, v in STAT_MAP.items()},
            **{f"odd_{k}": _odds(df, k) for k in ODDS},
        })[MATCH_COLS].dropna(subset=["date"])
        con.executemany(f"INSERT OR REPLACE INTO matches VALUES({','.join('?' * len(MATCH_COLS))})", _rows(out))
        tot += len(out)
        print(f"  stagione {s}: {len(out)} partite")
    con.commit()
    return tot


# ----------------------------------------------------------------- calendario
def update_fixtures(con=None):
    con = con or connect()
    known = [r[0] for r in con.execute("SELECT DISTINCT home FROM matches")]
    n = 0
    con.execute("DELETE FROM fixtures")
    try:
        df = _csv(C.FIXTURES_URL)
        df = df[df["Div"].astype(str).str.strip() == "I1"]
        if not df.empty:
            out = pd.DataFrame({
                "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d"),
                "time": df.get("Time", pd.Series("", index=df.index)).astype(str),
                "home": df["HomeTeam"].str.strip(), "away": df["AwayTeam"].str.strip(),
                **{f"odd_{k}": _odds(df, k) for k in ODDS},
            }).dropna(subset=["date"])
            con.executemany("INSERT OR REPLACE INTO fixtures VALUES(?,?,?,?,?,?,?,?,?)", _rows(out))
            n = len(out)
    except Exception as e:
        print(f"  [!] fixtures.csv: {e}")

    # Calendario completo di stagione (senza quote): copre le giornate non ancora in fixtures.csv
    try:
        for f in _get(C.FIXTURES_FALLBACK_URL).json():
            if f.get("HomeTeamScore") is not None:
                continue
            ts = pd.Timestamp(f["DateUtc"].replace("Z", "")).tz_localize("UTC").tz_convert("Europe/Rome")
            if ts.date() < dt.date.today():
                continue
            h, a = canonical(f["HomeTeam"], known), canonical(f["AwayTeam"], known)
            dup = con.execute("SELECT 1 FROM fixtures WHERE home=? AND away=?", (h, a)).fetchone()
            if not dup:
                con.execute("INSERT OR IGNORE INTO fixtures(date,time,home,away) VALUES(?,?,?,?)",
                            (ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M"), h, a))
                n += 1
    except Exception as e:
        print(f"  [!] calendario di riserva: {e}")

    if n == 0 and C.API_FOOTBALL_KEY:  # fallback: prossime 10 da API-Football
        resp = _api("/fixtures", league=C.API_FOOTBALL_LEAGUE, season=C.API_FOOTBALL_SEASON, next=10) or []
        for f in resp:
            d = f["fixture"]["date"][:10]
            t = f["fixture"]["date"][11:16]
            h = canonical(f["teams"]["home"]["name"], known)
            a = canonical(f["teams"]["away"]["name"], known)
            con.execute("INSERT OR REPLACE INTO fixtures(date,time,home,away) VALUES(?,?,?,?)", (d, t, h, a))
            n += 1
    con.commit()
    print(f"  calendario: {n} partite in programma")
    return n


# ----------------------------------------------------------------- API-Football
API_LOG = []   # esito chiamate API-Football, finisce in meta.json


def _api(path, **params):
    if not C.API_FOOTBALL_KEY:
        return None
    r = requests.get(C.API_FOOTBALL_URL + path, params=params, timeout=30,
                     headers={"x-apisports-key": C.API_FOOTBALL_KEY})
    r.raise_for_status()
    j = r.json()
    resp = j.get("response", [])
    entry = {"endpoint": path, "risultati": len(resp) if isinstance(resp, list) else 1}
    if j.get("errors"):
        entry["errore"] = j["errors"]
        print(f"  [!] API-Football {path}: {j['errors']}")
    if path != "/coachs" or j.get("errors"):
        API_LOG.append(entry)
    return resp


def update_injuries(con=None, horizon_days=10):
    """Infortunati/squalificati per le partite dei prossimi giorni."""
    con = con or connect()
    resp = _api("/injuries", league=C.API_FOOTBALL_LEAGUE, season=C.API_FOOTBALL_SEASON)
    if resp is None:
        print("  infortuni: API_FOOTBALL_KEY non impostata -> uso solo manual.json")
        return 0
    known = [r[0] for r in con.execute("SELECT DISTINCT home FROM matches")]
    today = dt.date.today()
    con.execute("DELETE FROM absences WHERE source='API'")
    n = 0
    for it in resp:
        fdate = dt.date.fromisoformat(it["fixture"]["date"][:10])
        if not (today <= fdate <= today + dt.timedelta(days=horizon_days)):
            continue
        team = canonical(it["team"]["name"], known)
        reason = f'{it["player"].get("type", "")}: {it["player"].get("reason", "")}'
        con.execute("INSERT OR REPLACE INTO absences VALUES(?,?,?,?,?)",
                    (team, it["player"]["name"], reason, "API", today.isoformat()))
        n += 1
    con.commit()
    print(f"  infortuni/squalifiche: {n}")
    return n


def update_coaches(con=None):
    con = con or connect()
    teams = _api("/teams", league=C.API_FOOTBALL_LEAGUE, season=C.API_FOOTBALL_SEASON)
    if teams is None:
        return 0
    known = [r[0] for r in con.execute("SELECT DISTINCT home FROM matches")]
    n = 0
    for t in teams:
        tid, tname = t["team"]["id"], canonical(t["team"]["name"], known)
        for c in _api("/coachs", team=tid) or []:
            cur = [x for x in c.get("career", []) if x["team"]["id"] == tid and not x.get("end")]
            if cur:
                con.execute("INSERT OR REPLACE INTO coaches VALUES(?,?,?,?)",
                            (tname, c["name"], cur[0].get("start"), dt.date.today().isoformat()))
                n += 1
                break
        time.sleep(0.3)
    con.commit()
    print(f"  allenatori: {n}")
    return n


# ----------------------------------------------------------------- notizie
TAGS = {
    "infortunio": r"infortun|lesion|stiramento|distorsion|risentiment|frattur|forfait|ai box|operat|\bko\b|out per|affaticament",
    "squalifica": r"squalific|giudice sportivo|espuls|diffidat",
    "allenatore": r"esoner|nuovo allenatore|panchina a rischio|dimission|ultimatum|traghettator",
    "crisi": r"crisi|contestazion|ritiro punitivo|polemic",
    "rientro": r"recuperat|rientr|torna a disposizione|tra i convocati|ok per",
    "formazione": r"probabil[ei] formazion|formazione ufficiale|titolar",
}


def tag_text(text):
    t = text.lower()
    return [k for k, rx in TAGS.items() if re.search(rx, t)]


def _parse_rss(content):
    root = ET.fromstring(content)
    for it in root.iter("item"):
        src = it.find("source")
        yield (it.findtext("title") or "", it.findtext("link") or "",
               it.findtext("pubDate") or "", src.text if src is not None else "")


def _pubdate(s):
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except Exception:
        return None


def update_news(con=None, teams=None):
    con = con or connect()
    if teams is None:
        teams = [r[0] for r in con.execute(
            "SELECT DISTINCT home FROM matches WHERE season=?", (C.CURRENT_SEASON,))]
    cutoff = dt.date.today() - dt.timedelta(days=C.NEWS_DAYS)
    n = 0

    def store(team, title, link, pub, src):
        nonlocal n
        d = _pubdate(pub)
        if d and d < cutoff:
            return
        con.execute("INSERT OR IGNORE INTO news VALUES(?,?,?,?,?,?)",
                    (link, team, title.strip(), src, (d or dt.date.today()).isoformat(),
                     ",".join(tag_text(title))))
        n += 1

    for team in teams:
        url = C.GOOGLE_NEWS_RSS.format(q=quote_plus(news_query(team) + " serie A"))
        try:
            for title, link, pub, src in _parse_rss(_get(url).content):
                if teams_in_text(title, [team]):
                    store(team, title, link, pub, src or "Google News")
        except Exception as e:
            print(f"  [!] news {team}: {e}")
        time.sleep(0.4)

    for feed in C.EXTRA_RSS:
        try:
            for title, link, pub, src in _parse_rss(_get(feed).content):
                for team in teams_in_text(title, teams):
                    store(team, title, link + "#" + team, pub, src or feed.split("/")[2])
        except Exception as e:
            print(f"  [!] feed {feed}: {e}")

    con.execute("DELETE FROM news WHERE published < ?", ((cutoff - dt.timedelta(days=30)).isoformat(),))
    con.commit()
    print(f"  notizie: {n} titoli analizzati")
    return n
