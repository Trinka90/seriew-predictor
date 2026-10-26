"""Lettura delle notizie con IA (Claude Haiku): assenti, importanza, squalifiche, porte chiuse, turnover.

Per ogni partita dei prossimi giorni:
  1. cerca notizie mirate per le due squadre nella lingua del campionato (Google News RSS)
  2. passa i titoli al modello, che restituisce un JSON strutturato
  3. traduce assenze ed eventi in correzioni dei gol attesi (tabella IMPATTO)
Il risultato è in cache: se i titoli non cambiano, nessuna nuova chiamata.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

import config as C
from data_sources import _get, _parse_rss, _pubdate, tag_text

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
CACHE = Path(__file__).resolve().parent / "cache" / "news_ai.json"
DAYS_AHEAD = 3
MAX_TITLES = 35

# query mirate per lingua: (hl, gl, ceid, modelli di query)
LANG = {
    "it": ("it", "IT", "IT:it", ["{t} infortunati squalificati", "{t} probabile formazione", "{t} calcio"]),
    "en": ("en-GB", "GB", "GB:en", ["{t} injury news", "{t} team news suspension"]),
    "es": ("es", "ES", "ES:es", ["{t} lesionados sancionados", "{t} convocatoria"]),
    "de": ("de", "DE", "DE:de", ["{t} verletzt gesperrt", "{t} Aufstellung"]),
    "fr": ("fr", "FR", "FR:fr", ["{t} blessés suspendus", "{t} groupe convoqué"]),
    "nl": ("nl", "NL", "NL:nl", ["{t} blessures geschorst", "{t} opstelling"]),
    "pt": ("pt-PT", "PT", "PT:pt-150", ["{t} lesionados castigados", "{t} convocados"]),
    "tr": ("tr", "TR", "TR:tr", ["{t} sakat cezalı", "{t} kadro"]),
}
LEAGUE_LANG = {"I1": "it", "I2": "it", "E0": "en", "E1": "en", "SC0": "en", "SP1": "es", "SP2": "es",
               "D1": "de", "D2": "de", "F1": "fr", "F2": "fr", "N1": "nl", "P1": "pt", "B1": "nl", "T1": "tr"}

# (calo gol fatti, aumento gol subiti) per assente
IMPATTO = {
    "attaccante":     {"stella": (0.10, 0.00), "titolare": (0.045, 0.00), "riserva": (0.01, 0.0)},
    "centrocampista": {"stella": (0.06, 0.03), "titolare": (0.025, 0.015), "riserva": (0.005, 0.005)},
    "difensore":      {"stella": (0.00, 0.07), "titolare": (0.005, 0.035), "riserva": (0.0, 0.005)},
    "portiere":       {"stella": (0.00, 0.08), "titolare": (0.00, 0.05), "riserva": (0.0, 0.0)},
}
CERTEZZA = {"certo": 1.0, "probabile": 0.7, "in dubbio": 0.35}
TURNOVER = {"nessuno": (0, 0), "medio": (0.03, 0.02), "alto": (0.08, 0.05)}

SYSTEM = """Sei un analista di calcio. Ricevi titoli di notizie recenti (in varie lingue) sulle due squadre di una partita.
Estrai SOLO le informazioni presenti nei titoli e rilevanti per QUESTA partita. Regole:
- Non inventare e non usare tue conoscenze su infortuni o squalifiche: possono essere vecchie. Usa le tue conoscenze SOLO per stimare ruolo e importanza di un giocatore citato nei titoli.
- Un giocatore "recuperato", "rientra", "torna disponibile", "convocato", "back in training" NON è assente.
- Ignora calciomercato, altre squadre, nazionali, giovanili, calcio femminile, partite già giocate e infortuni già superati.
- importanza: "stella" = tra i 2-3 giocatori più decisivi della squadra; "titolare" = gioca di solito dall'inizio; "riserva" = altrimenti.
- certezza: "certo" se il titolo dice che salta la partita (out, squalificato, lesione di settimane); "probabile" se è fortemente a rischio; "in dubbio" se in forse.
- turnover: "alto" se la squadra farà ampio turnover (es. coppa importante subito dopo, partita senza valore), "medio" se si parla di rotazioni, altrimenti "nessuno".
- porte_chiuse: true solo se un titolo dice che QUESTA partita si gioca a porte chiuse o con curva/stadio chiuso.
Rispondi SOLO con un JSON valido, senza testo prima o dopo, con questa struttura:
{"casa":{"assenti":[{"nome":"","ruolo":"portiere|difensore|centrocampista|attaccante","importanza":"stella|titolare|riserva","motivo":"infortunio|squalifica|altro","certezza":"certo|probabile|in dubbio","titoli":[1]}],"turnover":"nessuno|medio|alto","nuovo_allenatore":false,"clima":"positivo|neutro|negativo","note":"max 15 parole in italiano"},
 "ospite":{...stessa struttura...},
 "partita":{"porte_chiuse":false,"campo_neutro":false,"note":"max 15 parole in italiano"}}"""


# ----------------------------------------------------------------- notizie mirate
def fetch_targeted(con, teams_by_league):
    """Scarica titoli mirati (infortuni/formazione) per le squadre che giocano a breve."""
    n = 0
    cutoff = dt.date.today() - dt.timedelta(days=C.NEWS_DAYS)
    for lg, teams in teams_by_league.items():
        hl, gl, ceid, queries = LANG[LEAGUE_LANG.get(lg, "en")]
        for team in teams:
            for q in queries:
                url = (f"https://news.google.com/rss/search?q={quote_plus(q.format(t=team))}"
                       f"&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}")
                try:
                    for title, link, pub, src in _parse_rss(_get(url).content):
                        d = _pubdate(pub)
                        if d and d < cutoff:
                            continue
                        con.execute("INSERT OR IGNORE INTO news VALUES(?,?,?,?,?,?)",
                                    (link, team, title.strip(), src or "Google News",
                                     (d or dt.date.today()).isoformat(), ",".join(tag_text(title))))
                        n += 1
                except Exception as e:
                    print(f"  [!] news {team}: {e}")
                time.sleep(0.2)
    con.commit()
    return n


# ----------------------------------------------------------------- IA
def _load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c):
    limit = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    c = {k: v for k, v in c.items() if v.get("data", "9999") >= limit}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=0), encoding="utf-8")


def _titles(con, team):
    since = (dt.date.today() - dt.timedelta(days=C.NEWS_DAYS)).isoformat()
    seen, out = set(), []
    for pub, title, src in con.execute(
            "SELECT published, title, source FROM news WHERE team=? AND published>=? ORDER BY published DESC",
            (team, since)):
        key = re.sub(r"\W+", "", title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append((pub, title, src))
        if len(out) >= MAX_TITLES:
            break
    return out


def _call(prompt):
    r = requests.post(API_URL, timeout=90, headers={
        "x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 1500, "system": SYSTEM,
              "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0))


def analyze(con, fixtures):
    """fixtures: lista di (league, date, home, away). Ritorna {(home, away): analisi} e statistiche."""
    stats = {"partite": 0, "chiamate": 0, "da_cache": 0, "errori": 0}
    if not API_KEY:
        return {}, {"attivo": False}
    cache = _load_cache()
    results = {}
    for lg, date, home, away in fixtures:
        th, ta = _titles(con, home), _titles(con, away)
        if not th and not ta:
            continue
        stats["partite"] += 1
        key = hashlib.sha1(json.dumps([home, away, date, th, ta]).encode()).hexdigest()
        if key in cache:
            results[(home, away)] = cache[key]["r"]
            stats["da_cache"] += 1
            continue
        lines = [f"Partita: {home} (casa) - {away} (ospite), {C.LEAGUES[lg]['nome']}, data {date}. Oggi è {dt.date.today()}.", ""]
        k = 1
        allt = th + ta
        for label, tl in ((f"TITOLI SU {home}", th), (f"TITOLI SU {away}", ta)):
            lines.append(label + ":")
            for pub, title, src in tl:
                lines.append(f"[{k}] ({pub}, {src}) {title}")
                k += 1
            lines.append("")
        try:
            res = _call("\n".join(lines))
            res["n_titoli"] = len(th) + len(ta)
            for side in ("casa", "ospite"):   # collega ogni assente al titolo da cui viene
                for a in (res.get(side) or {}).get("assenti", []) or []:
                    idx = [i for i in (a.get("titoli") or []) if isinstance(i, int) and 1 <= i <= len(allt)]
                    if idx:
                        a["fonte"] = allt[idx[0] - 1][1]
            results[(home, away)] = res
            cache[key] = {"data": date, "r": res}
            stats["chiamate"] += 1
        except Exception as e:
            stats["errori"] += 1
            print(f"  [!] IA {home}-{away}: {e}")
        time.sleep(0.3)
    _save_cache(cache)
    stats["attivo"] = True
    return results, stats


# ----------------------------------------------------------------- traduzione in numeri
def impact_of(a):
    ruolo = a.get("ruolo", "centrocampista")
    imp = IMPATTO.get(ruolo, IMPATTO["centrocampista"]).get(a.get("importanza", "titolare"), (0.02, 0.01))
    c = CERTEZZA.get(a.get("certezza", "probabile"), 0.7)
    return imp[0] * c, imp[1] * c


def store(con, results):
    """Salva assenti ed eventi nel DB (sostituisce le analisi precedenti)."""
    today = dt.date.today().isoformat()
    con.execute("DELETE FROM absences WHERE source='IA'")
    con.execute("DELETE FROM match_events")
    for (home, away), r in results.items():
        for side, team in (("casa", home), ("ospite", away)):
            s = r.get(side) or {}
            for a in s.get("assenti", []) or []:
                if not a.get("nome"):
                    continue
                ia, idf = impact_of(a)
                info = json.dumps({k: a.get(k) for k in ("ruolo", "importanza", "certezza", "motivo", "fonte")},
                                  ensure_ascii=False)
                con.execute("INSERT OR REPLACE INTO absences(team, player, reason, source, updated, impact_att, impact_def, info) "
                            "VALUES(?,?,?,?,?,?,?,?)", (team, a["nome"], a.get("motivo", ""), "IA", today, ia, idf, info))
        p = r.get("partita") or {}
        ev = {"porte_chiuse": bool(p.get("porte_chiuse")), "campo_neutro": bool(p.get("campo_neutro")),
              "note": p.get("note", ""), "n_titoli": r.get("n_titoli", 0)}
        for side in ("casa", "ospite"):
            s = r.get(side) or {}
            ev[side] = {"turnover": s.get("turnover", "nessuno"), "nuovo_allenatore": bool(s.get("nuovo_allenatore")),
                        "clima": s.get("clima", "neutro"), "note": s.get("note", "")}
        con.execute("INSERT OR REPLACE INTO match_events VALUES(?,?,?)", (home, away, json.dumps(ev, ensure_ascii=False)))
    con.commit()


def run(con):
    """Pipeline completa, chiamata da export_json.update_all."""
    horizon = (dt.date.today() + dt.timedelta(days=DAYS_AHEAD)).isoformat()
    today = dt.date.today().isoformat()
    fx = con.execute("SELECT league, date, home, away FROM fixtures WHERE date>=? AND date<=? ORDER BY date",
                     (today, horizon)).fetchall()
    by_lg = {}
    for lg, _, h, a in fx:
        by_lg.setdefault(lg, set()).update((h, a))
    if not API_KEY:
        print("  IA notizie: ANTHROPIC_API_KEY non impostata (solo parole chiave)")
        return {"attivo": False}
    n = fetch_targeted(con, by_lg)
    results, stats = analyze(con, fx)
    store(con, results)
    stats["titoli_mirati"] = n
    print(f"  IA notizie: {stats}")
    return stats
