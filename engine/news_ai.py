"""Lettura delle notizie con IA (Groq gratuito, OpenRouter o Claude): assenti, importanza, squalifiche, porte chiuse, turnover.

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

# Fornitori IA in ordine di preferenza: si usa il primo con la chiave impostata.
# Groq e OpenRouter hanno piani GRATUITI (nessuna carta di credito).
PROVIDERS = [
    {"nome": "Groq", "env": "GROQ_API_KEY", "tipo": "openai",
     "url": "https://api.groq.com/openai/v1/chat/completions",
     "lista": "https://api.groq.com/openai/v1/models",
     "modelli": [os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]},
    {"nome": "OpenRouter", "env": "OPENROUTER_API_KEY", "tipo": "openai",
     "url": "https://openrouter.ai/api/v1/chat/completions", "lista": "https://openrouter.ai/api/v1/models",
     "modelli": [os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")]},
    {"nome": "Anthropic", "env": "ANTHROPIC_API_KEY", "tipo": "anthropic",
     "url": "https://api.anthropic.com/v1/messages", "modelli": ["claude-haiku-5-5"]},
]
ACTIVE = [p for p in PROVIDERS if os.environ.get(p["env"])]   # tutti quelli con chiave: catena di riserva
PROVIDER = ACTIVE[0] if ACTIVE else None
API_KEY = os.environ.get(PROVIDER["env"], "") if PROVIDER else ""
MAX_CALLS = int(os.environ.get("NEWS_AI_MAX_CALLS", "70"))   # per giro: resta nei limiti gratuiti
AI_BUDGET_MIN = float(os.environ.get("NEWS_AI_MINUTES", "15"))     # tempo massimo per l'analisi IA
FETCH_BUDGET_MIN = 6                                              # tempo massimo per scaricare i titoli
_deadline = [float("inf")]
_failures = {}        # errori consecutivi per fornitore: dopo 3 il fornitore viene saltato in questo giro
_used = {}            # fornitore -> chiamate riuscite
_last_error = [""]
_models_checked = set()


def _refresh_models(p):
    """Chiede al fornitore quali modelli esistono oggi: i ritirati vengono tolti, e se non resta nulla
    si sceglie da solo un modello grande disponibile. Così un ritiro non blocca mai l'analisi."""
    if p["nome"] in _models_checked or not p.get("lista"):
        return
    _models_checked.add(p["nome"])
    try:
        r = requests.get(p["lista"], headers={"Authorization": f"Bearer {os.environ.get(p['env'], '')}"}, timeout=20)
        r.raise_for_status()
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        if not ids:
            return
        keep = [m for m in p["modelli"] if m in ids]
        if p["nome"] == "OpenRouter":
            extra = [i for i in ids if i.endswith(":free")]
        else:
            bad = ("whisper", "tts", "guard", "orpheus", "safeguard", "embed", "playai", "distil")
            extra = [i for i in ids if not any(b in i.lower() for b in bad)]
        size = lambda i: max([int(x) for x in re.findall(r"(\d+)b", i.lower())] or [0])
        extra = sorted((i for i in extra if i not in keep), key=size, reverse=True)
        p["modelli"] = (keep + extra)[:4]
        print(f"  modelli {p['nome']}: {p['modelli']}")
    except Exception as e:
        print(f"  [!] elenco modelli {p['nome']}: {e}")

ROLES = {"portiere", "difensore", "centrocampista", "attaccante"}
LEVELS = {"stella", "titolare", "riserva"}
CERTS = {"certo", "probabile", "in dubbio"}
CACHE = Path(__file__).resolve().parent / "cache" / "news_ai.json"
DAYS_AHEAD = 3
MAX_TITLES = 25

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
    stop = time.time() + FETCH_BUDGET_MIN * 60
    for lg, teams in teams_by_league.items():
        hl, gl, ceid, queries = LANG[LEAGUE_LANG.get(lg, "en")]
        for team in teams:
            for q in queries:
                if time.time() > stop:
                    break
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
    last = {k: v for k, v in c.pop("_ultima", {}).items() if v.get("data", "9999") >= limit}
    c = {k: v for k, v in c.items() if v.get("data", "9999") >= limit}
    c["_ultima"] = last
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


def _left():
    return _deadline[0] - time.time()


def _post(url, headers, payload):
    """POST con attesa sui limiti (429) ma mai oltre il tempo disponibile."""
    last = None
    for attempt in range(4):
        if _left() < 20:
            raise TimeoutError("tempo IA esaurito")
        r = requests.post(url, headers=headers, json=payload, timeout=min(90, max(10, _left() - 5)))
        if r.status_code == 429 or r.status_code >= 500:
            last = r
            wait = float(r.headers.get("retry-after", 0) or 0) or 8 * (attempt + 1)
            if wait > 90 or wait > _left() - 30:   # quota giornaliera finita o troppo lunga: rinuncia
                break
            time.sleep(wait)
            continue
        if r.status_code >= 400:   # messaggio leggibile (es. modello ritirato), senza esporre la chiave
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()
    raise RuntimeError(f"HTTP {last.status_code if last is not None else '?'} (limite raggiunto)")


def _call_provider(p, prompt):
    key = os.environ.get(p["env"], "")
    if p["tipo"] == "anthropic":
        j = _post(p["url"], {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": p["modelli"][0], "max_tokens": 1500, "system": SYSTEM,
                   "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in j.get("content", []))
    _refresh_models(p)
    err = None
    for model in list(p["modelli"]):
        for fmt in (True, False):   # alcuni modelli non accettano response_format / reasoning_effort
            try:
                body = {"model": model, "temperature": 0, "max_tokens": 3000,
                        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
                if fmt:
                    body["response_format"] = {"type": "json_object"}
                    if "gpt-oss" in model or "qwen3" in model:
                        body["reasoning_effort"] = "low"
                j = _post(p["url"], {"Authorization": f"Bearer {key}"}, body)
                return j["choices"][0]["message"]["content"]
            except TimeoutError:
                raise
            except Exception as e:
                err = e
                if "limite" in str(e):
                    break
                low = str(e).lower()
                if "404" in low or "decommission" in low or ("model" in low and "not" in low):
                    break   # modello inesistente: passa al prossimo senza riprovare
    raise err


def _parse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        raise ValueError("risposta senza JSON")
    return json.loads(m.group(0))


def _call(prompt):
    """Prova i fornitori in ordine; un fornitore che fallisce 3 volte di fila viene saltato."""
    err = None
    for p in ACTIVE:
        if _failures.get(p["nome"], 0) >= 3:
            continue
        try:
            res = _parse(_call_provider(p, prompt))
            _failures[p["nome"]] = 0
            _used[p["nome"]] = _used.get(p["nome"], 0) + 1
            return res
        except TimeoutError:
            raise
        except Exception as e:
            _failures[p["nome"]] = _failures.get(p["nome"], 0) + 1
            _last_error[0] = f"{p['nome']}: {str(e)[:150]}"
            err = e
    raise err or RuntimeError("nessun fornitore IA disponibile")


ROLE_ALIASES = {
    "attaccante": ["att", "punta", "ala", "esterno offensivo", "trequartista", "forward", "striker", "winger",
                   "delantero", "stürmer", "sturmer", "attaquant", "aanvaller", "avançado", "forvet"],
    "centrocampista": ["centrocamp", "mediano", "regista", "mezzala", "midfield", "centrocampo", "medio",
                       "mittelfeld", "milieu", "middenvelder", "médio", "orta saha"],
    "difensore": ["difens", "terzino", "centrale", "defender", "back", "defensa", "verteidiger", "défenseur",
                  "verdediger", "defesa", "defans"],
    "portiere": ["portier", "goalkeeper", "keeper", "portero", "torwart", "gardien", "doelman", "guarda-redes", "kaleci"],
}


def _role(v):
    v = str(v or "").lower()
    if v in ROLES:
        return v
    for r, al in ROLE_ALIASES.items():
        if any(a in v for a in al):
            return r
    return "centrocampista"


def _bool(v):
    return v is True or str(v).strip().lower() in ("true", "si", "sì", "yes", "1")


def _sanitize(res):
    """Ripulisce la risposta: valori ammessi, al massimo 8 assenti per squadra, niente campi strani."""
    def side(s):
        s = s if isinstance(s, dict) else {}
        out = []
        for a in (s.get("assenti") or [])[:8]:
            if not isinstance(a, dict) or not str(a.get("nome") or "").strip():
                continue
            out.append({"nome": str(a["nome"]).strip()[:40],
                        "ruolo": _role(a.get("ruolo")),
                        "importanza": a.get("importanza") if a.get("importanza") in LEVELS else "titolare",
                        "motivo": str(a.get("motivo") or "")[:20],
                        "certezza": a.get("certezza") if a.get("certezza") in CERTS else "probabile",
                        "titoli": [i for i in (a.get("titoli") or []) if isinstance(i, int)][:3]})
        return {"assenti": out,
                "turnover": s.get("turnover") if s.get("turnover") in TURNOVER else "nessuno",
                "nuovo_allenatore": _bool(s.get("nuovo_allenatore")),
                "clima": s.get("clima") if s.get("clima") in ("positivo", "neutro", "negativo") else "neutro",
                "note": str(s.get("note") or "")[:120]}
    res = res if isinstance(res, dict) else {}
    p = res.get("partita") if isinstance(res.get("partita"), dict) else {}
    return {"casa": side(res.get("casa")), "ospite": side(res.get("ospite")),
            "partita": {"porte_chiuse": _bool(p.get("porte_chiuse")), "campo_neutro": _bool(p.get("campo_neutro")),
                        "note": str(p.get("note") or "")[:120]}}


def analyze(con, fixtures):
    """fixtures: lista di (league, date, home, away). Ritorna {(home, away): analisi} e statistiche."""
    stats = {"partite": 0, "chiamate": 0, "da_cache": 0, "errori": 0, "precedenti": 0, "rimandate": 0}
    if not ACTIVE:
        return {}, {"attivo": False}
    cache = _load_cache()
    last = cache.get("_ultima", {})   # ultima analisi riuscita per partita: riserva se oggi non si può
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
        mkey = f"{home}|{away}|{date}"
        dead = all(_failures.get(p["nome"], 0) >= 3 for p in ACTIVE)
        if stats["chiamate"] >= MAX_CALLS or _left() < 30 or dead:   # il resto al prossimo giro
            stats["rimandate"] += 1
            if mkey in last:
                results[(home, away)] = last[mkey]["r"]
                stats["precedenti"] += 1
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
            res = _sanitize(_call("\n".join(lines)))
            res["n_titoli"] = len(th) + len(ta)
            for side in ("casa", "ospite"):   # collega ogni assente al titolo da cui viene
                for a in (res.get(side) or {}).get("assenti", []) or []:
                    idx = [i for i in (a.get("titoli") or []) if isinstance(i, int) and 1 <= i <= len(allt)]
                    if idx:
                        a["fonte"] = allt[idx[0] - 1][1]
            results[(home, away)] = res
            cache[key] = {"data": date, "r": res}
            last[mkey] = {"data": date, "r": res}
            stats["chiamate"] += 1
        except Exception as e:
            stats["errori"] += 1
            print(f"  [!] IA {home}-{away}: {e}")
            if mkey in last:
                results[(home, away)] = last[mkey]["r"]
                stats["precedenti"] += 1
        time.sleep(0.3)
    cache["_ultima"] = last
    _save_cache(cache)
    stats["attivo"] = True
    stats["fornitore"] = ", ".join(f"{k} ({v})" for k, v in _used.items()) or ACTIVE[0]["nome"]
    stats["modelli"] = {p["nome"]: p["modelli"][:2] for p in ACTIVE}
    if _last_error[0]:
        stats["ultimo_errore"] = _last_error[0]
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
    if not ACTIVE:
        print("  IA notizie: nessuna chiave (GROQ_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY)")
        return {"attivo": False}
    _deadline[0] = time.time() + AI_BUDGET_MIN * 60
    try:
        n = fetch_targeted(con, by_lg)
    except Exception as e:
        print(f"  [!] titoli mirati: {e}")
        n = 0
    results, stats = analyze(con, fx)
    try:
        store(con, results)
    except Exception as e:
        print(f"  [!] salvataggio analisi: {e}")
    stats["titoli_mirati"] = n
    print(f"  IA notizie: {stats}")
    return stats
