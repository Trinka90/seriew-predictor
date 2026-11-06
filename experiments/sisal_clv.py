"""Quote italiane (Sisal e altri ADM) contro Pinnacle, su partite già giocate — esperimento a parte.

Domanda: giocando su un bookmaker italiano quando la sua quota supera il prezzo equo Pinnacle,
si batte la chiusura (CLV > 0)?  Il CLV non richiede i risultati: bastano le quote nel tempo.

Fonte: OddsPapi /historical-odds (storico da gennaio 2026, max 3 bookmaker per chiamata).
Budget: piano gratuito da 250 richieste al mese -> lo script si ferma da solo lasciando una riserva.

Per ogni partita e per due momenti di giocata (48 ore e 6 ore prima dell'inizio):
  prezzo equo   = quote Pinnacle in quel momento, margine tolto (metodo power)
  giocata       = esito con  p_equa * quota_ITA - 1 > soglia
  CLV           = quota_ITA * p_Pinnacle_chiusura - 1
Mercati: 1X2 (101) e Over/Under 2,5 (1010).

Uso: ODDSPAPI_KEY=... python experiments/sisal_clv.py  ->  experiments/sisal_clv.md + .json
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KEY = os.environ.get("ODDSPAPI_KEY", "")
BASE = "https://api.oddspapi.io/v4"
HERE = Path(__file__).parent
RESERVE = int(os.environ.get("RESERVE", "40"))        # richieste da lasciare libere per il resto del mese
MAX_FIX = int(os.environ.get("MAX_FIX", "150"))       # tetto partite analizzate
SINCE = os.environ.get("SINCE", "2026-08-01")
TOURNAMENTS = {23: "Serie A", 53: "Serie B", 17: "Premier League", 18: "Championship", 8: "LaLiga", 54: "LaLiga 2"}
ITA_PREF = ["sisal.it", "snai.it", "bet365.it", "lottomatica.it", "goldbet.it", "eurobet.it", "betflag.it"]
SHARP = "pinnacle"
MARKETS = {"1X2": ("101", ["101", "102", "103"]), "OU": ("1010", ["1010", "1011"])}
BET_HOURS = (48, 6)
THRESHOLDS = (0.0, 0.02, 0.04)
log = {"chiamate": 0, "errori": []}


def get(path, cooldown, **params):
    for attempt in range(3):
        time.sleep(cooldown)
        r = requests.get(f"{BASE}/{path}", params={**params, "apiKey": KEY}, timeout=60)
        log["chiamate"] += 1
        if r.status_code == 429:
            time.sleep(10)
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text[:300]
    return 429, None


def remaining():
    """Richieste libere nel mese (la chiamata /account non consuma quota)."""
    st, a = get("account", 0.5)
    log["chiamate"] -= 1
    try:
        lim = a["subscriptions"][0]["request_limit"]
        if isinstance(lim, dict):  # forma vista nella sonda: {request_limit, request_count, ...}
            return lim["request_limit"] - lim.get("request_count", 0), lim
        return lim - a["subscriptions"][0].get("request_count", 0), a["subscriptions"][0]
    except (KeyError, IndexError, TypeError):
        return None, str(a)[:300]


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def devig(prices):
    inv = [1 / p for p in prices]
    if sum(inv) <= 1:
        s = sum(inv)
        return [x / s for x in inv]
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if sum(x ** k for x in inv) > 1 else (lo, k)
    p = [x ** ((lo + hi) / 2) for x in inv]
    s = sum(p)
    return [x / s for x in p]


def find_books(node):
    """Trova il dizionario {slug: {"markets": ...}} ovunque sia nella risposta."""
    if isinstance(node, dict):
        if any(isinstance(v, dict) and "markets" in v for v in node.values()):
            return {k: v for k, v in node.items() if isinstance(v, dict) and "markets" in v}
        for v in node.values():
            found = find_books(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = find_books(v)
            if found:
                return found
    return {}


def series(book, market_id, outcome_id):
    """Lista ordinata (istante, quota) per un esito, linea principale (giocatore '0')."""
    try:
        players = book["markets"][market_id]["outcomes"][outcome_id]["players"]
    except (KeyError, TypeError):
        return []
    entries = players.get("0") if isinstance(players, dict) else None
    if entries is None and isinstance(players, dict) and players:
        entries = next(iter(players.values()))
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for e in entries or []:
        try:
            if e.get("active") is False or not e.get("price") or e["price"] <= 1:
                continue
            out.append((parse_ts(e["createdAt"]), float(e["price"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def at(ser, t):
    """Ultima quota valida all'istante t (None se non ancora quotata)."""
    v = None
    for ts, p in ser:
        if ts > t:
            break
        v = p
    return v


def prices_at(book, market, t):
    mid, oids = MARKETS[market]
    vals = [at(series(book, mid, o), t) for o in oids]
    return vals if all(vals) else None


def main():
    if not KEY:
        sys.exit("ODDSPAPI_KEY mancante")
    left, account = remaining()
    out = {"richieste_libere_inizio": left}
    budget = (left - RESERVE) if left is not None else 60
    if budget < 15:
        sys.exit(f"budget insufficiente: {left} richieste libere")

    st, books = get("bookmakers", 1.0)
    slugs = [b.get("slug") or b.get("bookmakerSlug") for b in books] if isinstance(books, list) else []
    ita_all = sorted(s for s in slugs if s and (s.endswith(".it") or s.endswith("-it")))
    out["bookmaker_italiani_disponibili"] = ita_all
    ita = [s for s in ITA_PREF if s in ita_all][:2] or ita_all[:2]
    if not ita:
        out["errore"] = "nessun bookmaker italiano trovato"
        out["risposta_bookmakers"] = str(books)[:1500]
        (HERE / "sisal_clv.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
        sys.exit(out["errore"])
    out["bookmaker_usati"] = ita
    budget -= 1

    now = datetime.now(timezone.utc)
    fixtures = []
    for tid, name in TOURNAMENTS.items():
        st, fx = get("fixtures", 2.2, tournamentId=tid, statusId=2,
                     **{"from": f"{SINCE}T00:00:00Z", "to": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        budget -= 1
        if not isinstance(fx, list):
            # alcuni filtri potrebbero non essere accettati: riprova con il solo torneo
            st, fx = get("fixtures", 2.2, tournamentId=tid)
            budget -= 1
        if not isinstance(fx, list):
            log["errori"].append(f"fixtures {name}: {st} {str(fx)[:200]}")
            continue
        for f in fx:
            try:
                ts = parse_ts(f["startTime"])
            except (KeyError, ValueError, TypeError):
                continue
            if ts < parse_ts(f"{SINCE}T00:00:00Z") or ts > now - timedelta(hours=3) or f.get("statusId") not in (2, None):
                continue
            fixtures.append({"id": f["fixtureId"], "lg": name, "ts": ts,
                             "match": f"{f.get('participant1Name', '?')} - {f.get('participant2Name', '?')}"})
    # le più recenti per prime, a rotazione fra campionati
    per = defaultdict(list)
    for f in sorted(fixtures, key=lambda f: f["ts"], reverse=True):
        per[f["lg"]].append(f)
    queue = []
    while any(per.values()):
        for lg in list(per):
            if per[lg]:
                queue.append(per[lg].pop(0))
    n_fix = max(0, min(MAX_FIX, budget))
    out["partite_trovate"] = len(fixtures)
    queue = queue[:n_fix]

    plays = defaultdict(list)          # (bookmaker, mercato, ore, soglia) -> [(quota, clv)]
    margin = defaultdict(list)         # (bookmaker, mercato) -> quota_ITA * p_equa - 1 su tutti gli esiti (48h)
    per_league = defaultdict(list)
    analysed, sample = 0, None
    for f in queue:
        st, h = get("historical-odds", 5.2, fixtureId=f["id"], bookmakers=",".join(ita + [SHARP]))
        if st != 200:
            log["errori"].append(f"{f['id']}: {st} {str(h)[:150]}")
            if st in (401, 402, 403):
                break
            continue
        bks = find_books(h)
        if sample is None and bks:
            sample = {k: list(v.get("markets", {}))[:10] for k, v in bks.items()}
        pin = bks.get(SHARP)
        if not pin:
            continue
        analysed += 1
        close_t = f["ts"] - timedelta(minutes=1)
        for market in MARKETS:
            pc = prices_at(pin, market, close_t)
            if not pc:
                continue
            p_close = devig(pc)
            for hours in BET_HOURS:
                t = f["ts"] - timedelta(hours=hours)
                pp = prices_at(pin, market, t)
                if not pp:
                    continue
                p_fair = devig(pp)
                for bk in ita:
                    if bk not in bks:
                        continue
                    q = prices_at(bks[bk], market, t)
                    if not q:
                        continue
                    edges = [(p_fair[i] * q[i] - 1, i) for i in range(len(q))]
                    if hours == BET_HOURS[0]:
                        margin[(bk, market)].extend(e for e, _ in edges)
                    edge, i = max(edges)
                    for th in THRESHOLDS:
                        if edge > th:
                            plays[(bk, market, hours, th)].append((q[i], q[i] * p_close[i] - 1))
                            if th == 0.02 and hours == BET_HOURS[0]:
                                per_league[(bk, f["lg"])].append(q[i] * p_close[i] - 1)

    left_end, _ = remaining()
    out.update({"partite_analizzate": analysed, "richieste_libere_fine": left_end, "chiamate": log["chiamate"],
                "errori": log["errori"][:20], "esempio_struttura": sample})

    def st_(rows):
        n = len(rows)
        if not n:
            return None
        c = [x for _, x in rows]
        m = sum(c) / n
        se = math.sqrt(max(sum(x * x for x in c) / n - m * m, 0) / n)
        return n, sum(o for o, _ in rows) / n, m, se, sum(x > 0 for x in c) / n

    md = ["# Quote italiane contro Pinnacle (storico OddsPapi)", "",
          f"Partite analizzate: **{analysed}** (trovate {len(fixtures)}, dal {SINCE}) · bookmaker: {', '.join(ita)}",
          f"Bookmaker italiani disponibili su OddsPapi: {', '.join(ita_all)}", "",
          "Prezzo equo = Pinnacle nello stesso istante, senza margine. CLV = quota italiana × prob. Pinnacle alla chiusura − 1.", "",
          "## Margine medio contro il prezzo equo (tutti gli esiti, 48 h prima)", "",
          "| Bookmaker | Mercato | Esiti | Valore medio | Esiti con valore > 0 |", "|---|---|---|---|---|"]
    for (bk, market), v in sorted(margin.items()):
        md.append(f"| {bk} | {market} | {len(v)} | {sum(v) / len(v) * 100:+.1f}% | {sum(x > 0 for x in v) / len(v):.1%} |")
    md += ["", "## Giocate di valore", "",
           "| Bookmaker | Mercato | Giocata | Soglia | Giocate | Quota media | CLV | ± | Batte chiusura |",
           "|---|---|---|---|---|---|---|---|---|"]
    for bk in ita:
        for market in MARKETS:
            for hours in BET_HOURS:
                for th in THRESHOLDS:
                    s = st_(plays.get((bk, market, hours, th), []))
                    if s:
                        md.append(f"| {bk} | {market} | {hours} h prima | {th:.0%} | {s[0]} | {s[1]:.2f} | "
                                  f"**{s[2] * 100:+.1f}%** | {s[3] * 100:.1f} | {s[4]:.0%} |")
                    else:
                        md.append(f"| {bk} | {market} | {hours} h prima | {th:.0%} | 0 | | | | |")
    md += ["", "## 1X2, soglia 2%, 48 h prima — CLV per campionato", "", "| Bookmaker | Campionato | Giocate | CLV |", "|---|---|---|---|"]
    for (bk, lg), v in sorted(per_league.items()):
        md.append(f"| {bk} | {lg} | {len(v)} | {sum(v) / len(v) * 100:+.1f}% |")
    md += ["", "## Note",
           f"- Richieste OddsPapi usate: {log['chiamate']}; libere a fine giro: {left_end}.",
           "- CLV positivo di oltre 2 errori standard = vantaggio probabilmente reale. Con meno di ~100 giocate è indicativo.",
           "- Il CLV misura se si batte il prezzo finale, non quanto si vince: il ROI richiede molte più partite."]
    if log["errori"]:
        md += ["- Errori: " + "; ".join(log["errori"][:5])]
    (HERE / "sisal_clv.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (HERE / "sisal_clv.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
