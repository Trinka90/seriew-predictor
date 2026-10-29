"""Backtest del "semaforo" (partite incerte) e del "pronostico robusto", stagione 2025/26. Solo report.

Segnali ricostruibili sui dati storici:
  - calendario fitto: una delle due squadre ha giocato 3 giorni prima o meno (solo partite di campionato)
  - disaccordo: il modello puro e il mercato differiscono di 10+ punti su 1/X/2
  - pochi dati: una squadra con meno di 15 partite nello storico usato dal modello (neopromosse ecc.)
  - quote mosse (proxy delle notizie): tra apertura e chiusura le probabilità del mercato si spostano di 5+ punti.
    Nella realtà non lo sappiamo prima, ma corrisponde alle notizie (turnover, infortuni) che l'IA legge.
Esecuzione (da engine/, dopo aver scaricato lo storico): python ../experiments/backtest_semaforo.py
"""
import datetime as dt
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "experiments"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest_app as BA  # noqa: E402
import config as C  # noqa: E402
import context as X  # noqa: E402
import data_sources as D  # noqa: E402
from db import connect  # noqa: E402
from markets import goal_markets  # noqa: E402
from model import DixonColes, blend, implied_lambdas, market_probs, score_matrix  # noqa: E402

SEASON = "2526"
QMIN = BA.QMIN
SHIFT = 0.20          # scenario "squadra più debole/forte del previsto" per il pronostico robusto


def closing(lg):
    """Quote di chiusura dal CSV originale (non sono nel database dell'app)."""
    try:
        df = D._csv(C.HIST_URL.format(season=SEASON, league=lg))
    except Exception:
        return {}
    out = {}
    for _, r in df.iterrows():
        try:
            d = pd.to_datetime(r["Date"], dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            continue
        g = lambda *cols: next((float(r[c]) for c in cols if c in r and pd.notna(r[c])), None)
        out[(d, str(r["HomeTeam"]).strip(), str(r["AwayTeam"]).strip())] = {
            "h": g("AvgCH", "PSCH", "B365CH"), "d": g("AvgCD", "PSCD", "B365CD"), "a": g("AvgCA", "PSCA", "B365CA"),
            "o": g("AvgC>2.5", "PC>2.5", "B365C>2.5"), "u": g("AvgC<2.5", "PC<2.5", "B365C<2.5"),
            "bh": g("B365CH"), "bd": g("B365CD"), "ba": g("B365CA")}
    return out


def run_league(lg):
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == lg].reset_index(drop=True)
    test = df[df.season == SEASON].dropna(subset=["fthg", "ftag"])
    if len(test) < 100:
        return []
    close = closing(lg)
    # giorni di riposo: data dell'ultima partita di campionato di ogni squadra
    games = defaultdict(list)
    for r in df.dropna(subset=["fthg"]).itertuples():
        games[r.home].append(r.date)
        games[r.away].append(r.date)

    def rest(team, day):
        prev = [g for g in games[team] if g < day]
        return (day - max(prev)).days if prev else 99

    out = []
    for wk in test.date.dt.to_period("W").unique():
        part = test[test.date.dt.to_period("W") == wk]
        dc = DixonColes().fit(df, wk.start_time)
        mask = (wk.start_time - df.date).dt.days.between(1, C.TRAIN_DAYS)
        hist = df[mask]
        cnt = pd.concat([hist.home, hist.away]).value_counts()
        for r in part.itertuples():
            mp = market_probs(r.odd_h, r.odd_d, r.odd_a, r.odd_o25, r.odd_u25)
            if not mp:
                continue
            lb, mb = implied_lambdas(mp, dc.rho)
            lm, mm = dc.lambdas(r.home, r.away)
            model_only = goal_markets(score_matrix(lm, mm, dc.rho))
            lam, mu = blend(lm, mm, lb, mb, C.MARKET_WEIGHT)
            mk = goal_markets(score_matrix(lam, mu, dc.rho))
            book = goal_markets(score_matrix(lb, mb, dc.rho))
            real = {"1": r.bk_h, "X": r.bk_d, "2": r.bk_a, "Over 2.5": r.bk_o25, "Under 2.5": r.bk_u25}
            b3 = [r.bk_h, r.bk_d, r.bk_a]
            marg = (sum(1 / x for x in b3) - 1) if all(x and x == x and x > 1 for x in b3) else BA.MARGIN
            h, a = int(r.fthg), int(r.ftag)

            def odd(k):
                q = real.get(k)
                return float(q) if q and q == q else max(1.01, 1 / (book[k] * (1 + marg)))

            def pick(markets, k):
                return {"k": k, "p": float(markets[k]), "odd": odd(k), "hit": BA.hit(k, h, a)}

            # pronostico standard (come l'app)
            allp = sorted((pick(mk, k) for k in BA.ALL_KEYS), key=lambda x: -x["p"])
            std = next((x for x in allp if x["odd"] >= QMIN), allp[-1])
            # pronostico robusto: il migliore nel caso peggiore tra "casa più debole" e "casa più forte"
            sc = [mk, goal_markets(score_matrix(lam * (1 - SHIFT), mu * (1 + SHIFT * .75), dc.rho)),
                  goal_markets(score_matrix(lam * (1 + SHIFT * .75), mu * (1 - SHIFT), dc.rho))]
            cands = [x for x in allp if x["odd"] >= QMIN]
            rob = max(cands, key=lambda x: min(s[x["k"]] for s in sc)) if cands else std
            rob = dict(rob, worst=min(s[rob["k"]] for s in sc))
            # quote di chiusura
            cl = close.get((r.date.strftime("%Y-%m-%d"), r.home, r.away))
            moved, close_pick, ll_open, ll_close = None, None, None, None
            res_idx = 0 if h > a else 1 if h == a else 2
            if cl and all(cl[k] for k in "hda"):
                mpc = market_probs(cl["h"], cl["d"], cl["a"], cl["o"], cl["u"])
                if mpc:
                    moved = float(np.max(np.abs(mpc["1x2"] - mp["1x2"])))
                    lc, mc = implied_lambdas(mpc, dc.rho)
                    lam2, mu2 = blend(lm, mm, lc, mc, C.MARKET_WEIGHT)
                    mk2 = goal_markets(score_matrix(lam2, mu2, dc.rho))
                    allc = sorted((pick(mk2, k) for k in BA.ALL_KEYS), key=lambda x: -x["p"])
                    close_pick = next((x for x in allc if x["odd"] >= QMIN), allc[-1])
                    ll_open = -np.log(max([mk["1"], mk["X"], mk["2"]][res_idx], 1e-9))
                    ll_close = -np.log(max([mk2["1"], mk2["X"], mk2["2"]][res_idx], 1e-9))
            day = r.date
            sig = {
                "calendario": min(rest(r.home, day), rest(r.away, day)) <= 3,
                "disaccordo": max(abs(model_only[k] - book[k]) for k in ("1", "X", "2")) >= 0.10,
                "pochi_dati": min(cnt.get(r.home, 0), cnt.get(r.away, 0)) < 15,
                "quote_mosse": moved is not None and moved >= 0.05,
            }
            out.append({"lega": lg, "data": day.strftime("%Y-%m-%d"), "partita": f"{r.home}-{r.away}", "ris": f"{h}-{a}",
                        "std": std, "rob": rob, "close": close_pick, "ll_open": ll_open, "ll_close": ll_close,
                        "sig": sig, "moved": moved})
    print(f"  {lg}: {len(out)} partite", flush=True)
    return out


# ----------------------------------------------------------------- analisi
def pct(x):
    return "–" if x is None or x != x else f"{x * 100:.0f}%"


def pts(x):
    return "–" if x is None or x != x else f"{x * 100:+.1f}"


def roi(picks):
    return float(np.mean([(p["odd"] - 1) if p["hit"] else -1 for p in picks])) if picks else None


def row(name, rows, key="std"):
    ps = [r[key] for r in rows if r[key]]
    if not ps:
        return f"| {name} | 0 | – | – | – | – |"
    hitr, pr = np.mean([p["hit"] for p in ps]), np.mean([p["p"] for p in ps])
    return f"| {name} | {len(ps)} | {pct(pr)} | **{pct(hitr)}** | {pts(hitr - pr)} | {BA.sroi(roi(ps))} |"


def level(r, with_news):
    s = r["sig"]
    n = s["calendario"] + s["disaccordo"] + s["pochi_dati"] + (s["quote_mosse"] if with_news else 0)
    return "🔴" if n >= 2 else "🟡" if n == 1 else "🟢"


def daily(rows, key, exclude_red=False, with_news=True, n=4):
    by = defaultdict(list)
    for r in rows:
        if exclude_red and level(r, with_news) == "🔴":
            continue
        p = r[key]
        if p and p["p"] >= BA.PMIN and p["odd"] >= QMIN and p["k"] in BA.SLIP_KEYS:
            by[r["data"]].append(p)
    res = []
    for d in sorted(by):
        best = sorted(by[d], key=lambda x: -x["p"])[:n]
        if len(best) == n:
            res.append({"ok": all(x["hit"] for x in best), "q": float(np.prod([x["odd"] for x in best])),
                        "prob": float(np.prod([x["p"] for x in best]))})
    if not res:
        return None
    return {"giorni": len(res), "perc": np.mean([x["ok"] for x in res]), "attese": np.mean([x["prob"] for x in res]),
            "quota": np.mean([x["q"] for x in res]), "roi": np.mean([(x["q"] - 1) if x["ok"] else -1 for x in res])}


def main():
    with Pool(4) as pool:
        rows = [r for lst in pool.map(run_league, list(C.LEAGUES)) for r in lst]
    L = [f"# Backtest semaforo e pronostico robusto — stagione {SEASON[:2]}/{SEASON[2:]}",
         f"_Generato il {dt.date.today()} · {len(rows)} partite in {len({r['lega'] for r in rows})} campionati · "
         f"quote di chiusura trovate per {sum(r['moved'] is not None for r in rows)} partite_", "",
         "Colonne: **Indicato** = probabilità media data dall'app · **Uscito** = quante volte è uscito davvero · "
         "**Scarto** = Uscito − Indicato in punti (negativo = l'app era troppo sicura) · **Resa** = giocando 1 € a quote Bet365.", ""]

    # 1. segnali singoli
    L += ["## 1. Nelle partite segnalate il pronostico esce meno?", "",
          "| Gruppo | Partite | Indicato | Uscito | Scarto | Resa |", "|---|---|---|---|---|---|",
          row("Tutte le partite", rows)]
    names = {"calendario": "Calendario fitto (≤3 giorni di riposo)", "disaccordo": "Modello e mercato in disaccordo (≥10 punti)",
             "pochi_dati": "Pochi dati su una squadra", "quote_mosse": "Quote mosse ≥5 punti (proxy notizie)"}
    for k, nm in names.items():
        L.append(row(f"{nm} — SÌ", [r for r in rows if r["sig"][k]]))
        L.append(row(f"{nm} — no", [r for r in rows if not r["sig"][k]]))
    L.append("")

    # 2. semaforo
    for wn, title in ((False, "solo segnali noti prima della partita"), (True, "con le quote mosse come proxy delle notizie")):
        L += [f"## 2{'b' if wn else 'a'}. Semaforo ({title})", "",
              "| Semaforo | Partite | Indicato | Uscito | Scarto | Resa |", "|---|---|---|---|---|---|"]
        for lv in ("🟢", "🟡", "🔴"):
            L.append(row(lv, [r for r in rows if level(r, wn) == lv]))
        L.append("")

    # 3. robusto
    L += ["## 3. Pronostico robusto contro pronostico standard", "",
          "| Partite | Pronostico | Volte | Indicato | Uscito | Scarto | Resa |", "|---|---|---|---|---|---|---|"]
    for lv_name, sel in (("Tutte", lambda r: True), ("🟡+🔴 (con notizie)", lambda r: level(r, True) != "🟢"),
                         ("🔴 (con notizie)", lambda r: level(r, True) == "🔴")):
        sub = [r for r in rows if sel(r)]
        for key, nm in (("std", "standard"), ("rob", "robusto")):
            ps = [r[key] for r in sub]
            if ps:
                hr, pr = np.mean([p["hit"] for p in ps]), np.mean([p["p"] for p in ps])
                L.append(f"| {lv_name} | {nm} | {len(ps)} | {pct(pr)} | **{pct(hr)}** | {pts(hr - pr)} | {BA.sroi(roi(ps))} |")
    changed = [r for r in rows if r["rob"]["k"] != r["std"]["k"]]
    L += ["", f"Il pronostico robusto è diverso da quello standard in {len(changed)} partite su {len(rows)}. "
              f"In quelle partite: standard uscito {pct(np.mean([r['std']['hit'] for r in changed]) if changed else None)}, "
              f"robusto uscito {pct(np.mean([r['rob']['hit'] for r in changed]) if changed else None)}.", ""]
    kinds = defaultdict(int)
    for r in changed:
        kinds[f"{r['std']['k']} → {r['rob']['k']}"] += 1
    L += ["Cambi più frequenti: " + ", ".join(f"{k} ({v})" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:6]), ""]

    # 4. quote di chiusura
    wc = [r for r in rows if r["close"] is not None]
    if wc:
        L += ["## 4. Quote di apertura contro quote di chiusura", "",
              f"Su {len(wc)} partite con entrambe le quote:", "",
              "| Quote usate | LogLoss 1X2 (più basso = meglio) | Pronostico uscito | Indicato | Resa |", "|---|---|---|---|---|",
              f"| Apertura (come oggi) | {np.mean([r['ll_open'] for r in wc]):.4f} | **{pct(np.mean([r['std']['hit'] for r in wc]))}** | "
              f"{pct(np.mean([r['std']['p'] for r in wc]))} | {BA.sroi(roi([r['std'] for r in wc]))} |",
              f"| Chiusura (aggiornate al fischio) | {np.mean([r['ll_close'] for r in wc]):.4f} | **{pct(np.mean([r['close']['hit'] for r in wc]))}** | "
              f"{pct(np.mean([r['close']['p'] for r in wc]))} | {BA.sroi(roi([r['close'] for r in wc]))} |", ""]

    # 5. schedina
    L += ["## 5. Effetto sulla schedina del giorno da 4", "",
          "| Regola | Giorni | Uscita | Attese | Quota media | Resa |", "|---|---|---|---|---|---|"]
    for nm, key, ex, wn in (("Come oggi", "std", False, True), ("Senza le 🔴 (solo segnali noti)", "std", True, False),
                            ("Senza le 🔴 (con notizie)", "std", True, True), ("Pronostico robusto", "rob", False, True),
                            ("Robusto + senza 🔴 (con notizie)", "rob", True, True)):
        st = daily(rows, key, ex, wn)
        if st:
            L.append(f"| {nm} | {st['giorni']} | **{pct(st['perc'])}** | {pct(st['attese'])} | {st['quota']:.2f} | {BA.sroi(st['roi'])} |")
    L.append("")
    (ROOT / "experiments" / "backtest_semaforo.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
