"""Simulazione: "se avessi usato l'app nella stagione 2025/26 e giocato i pronostici consigliati".

Stesse regole dell'app (pronostico consigliato, schedina del giorno da 4), modello rifatto ogni settimana
con i soli dati disponibili prima delle partite, fuso con le quote reali (come fa l'app quando ci sono).
Non usa le notizie (non esistono storiche). Solo report: non modifica l'app.

Esecuzione (da engine/, dopo aver scaricato lo storico):  python ../experiments/backtest_app.py
"""
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import numpy as np  # noqa: E402

import config as C  # noqa: E402
import context as X  # noqa: E402
import sports  # noqa: E402
from db import connect  # noqa: E402
from markets import goal_markets  # noqa: E402
from model import DixonColes, blend, implied_lambdas, market_probs, score_matrix  # noqa: E402

SEASON = "2526"
QMIN, PMIN = 1.20, 0.65          # filtri predefiniti dell'app
MARGIN = 0.06                    # margine bookmaker stimato per i mercati senza quota storica
N_SLIP = 4

# mercati come nell'app: la schedina usa le categorie predefinite (niente combo), il consigliato tutti
SLIP_KEYS = ["1", "X", "2", "1X", "X2", "12", "Over 1.5", "Over 2.5", "Over 3.5", "Under 1.5", "Under 2.5",
             "Under 3.5", "GG", "NG", "Multigol 1-3", "Multigol 2-4", "Multigol 2-3"]
ALL_KEYS = SLIP_KEYS + ["1 + Over 1.5", "2 + Over 1.5", "1 + GG", "2 + GG", "GG + Over 2.5"]


def hit(k, h, a):
    t = h + a
    simple = {"1": h > a, "X": h == a, "2": h < a, "1X": h >= a, "X2": h <= a, "12": h != a,
              "GG": h > 0 and a > 0, "NG": h == 0 or a == 0,
              "1 + Over 1.5": h > a and t > 1.5, "2 + Over 1.5": a > h and t > 1.5,
              "1 + GG": h > a and a > 0, "2 + GG": a > h and h > 0, "GG + Over 2.5": h > 0 and a > 0 and t > 2.5}
    if k in simple:
        return bool(simple[k])
    r = re.match(r"(Over|Under) ([\d.]+)$", k)
    if r:
        return t > float(r[2]) if r[1] == "Over" else t < float(r[2])
    r = re.match(r"Multigol (\d)-(\d)$", k)
    if r:
        return int(r[1]) <= t <= int(r[2])
    raise ValueError(k)


# ----------------------------------------------------------------- calcio
def run_league(lg):
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == lg].reset_index(drop=True)
    test = df[df.season == SEASON].dropna(subset=["fthg", "ftag"])
    if len(test) < 100:
        return []
    out = []
    for wk in test.date.dt.to_period("W").unique():
        part = test[test.date.dt.to_period("W") == wk]
        dc = DixonColes().fit(df, wk.start_time)
        for r in part.itertuples():
            mp = market_probs(r.odd_h, r.odd_d, r.odd_a, r.odd_o25, r.odd_u25)
            if not mp:
                continue
            lb, mb = implied_lambdas(mp, dc.rho)
            lm, mm = dc.lambdas(r.home, r.away)
            lam, mu = blend(lm, mm, lb, mb, C.MARKET_WEIGHT)
            mk = goal_markets(score_matrix(lam, mu, dc.rho))
            book = goal_markets(score_matrix(lb, mb, dc.rho))        # mercato, per stimare le quote mancanti
            real = {"1": r.odd_h, "X": r.odd_d, "2": r.odd_a, "Over 2.5": r.odd_o25, "Under 2.5": r.odd_u25}
            h, a = int(r.fthg), int(r.ftag)

            def pick(k):
                q = real.get(k)
                q = q if q and q == q else None
                pay = q or max(1.01, 1 / (book[k] * (1 + MARGIN)))   # quota pagata (reale o stimata)
                return {"k": k, "p": float(mk[k]), "odd": q or 1 / mk[k], "pay": float(pay),
                        "reale": q is not None, "hit": hit(k, h, a)}

            allp = sorted((pick(k) for k in ALL_KEYS), key=lambda x: -x["p"])
            rec = next((x for x in allp if x["odd"] >= QMIN), allp[-1])              # pronostico consigliato
            cand = [x for x in allp if x["k"] in SLIP_KEYS and x["p"] >= PMIN and x["odd"] >= QMIN]
            out.append({"lega": lg, "data": r.date.strftime("%Y-%m-%d"), "partita": f"{r.home}-{r.away}",
                        "ris": f"{h}-{a}", "rec": rec, "slip": cand[0] if cand else None})
    print(f"  {lg}: {len(out)} partite", flush=True)
    return out


# ----------------------------------------------------------------- tennis
def tennis_hit(k, s1, s2):
    r = re.match(r"Set (\d)-(\d)$", k)
    if r:
        return s1 == int(r[1]) and s2 == int(r[2])
    r = re.match(r"Set Over ([\d.]+)$", k)
    if r:
        return s1 + s2 > float(r[1])
    return {"1": s1 > s2, "2": s2 > s1, "1 vince un set": s1 >= 1, "2 vince un set": s2 >= 1}[k]


def run_tennis(code):
    db = sports._load(code)
    ms = sorted(db["matches"].values(), key=lambda m: (m["date"], m.get("ora", ""), m["id"]))
    elo = sports.TennisElo()
    cut = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    out = []
    for m in ms:
        if not m["done"] or m["w"] is None or m.get("wo"):
            continue
        p1, p2, sf = m["p1"], m["p2"], m["superficie"]
        s1, s2 = m["sets"]
        need = 3 if m["bo5"] else 2
        valid = max(s1, s2) == need and (s1 > s2) == (m["w"] == 0)          # niente ritiri
        if valid and m["date"] >= cut and elo.n.get(p1, 0) >= 10 and elo.n.get(p2, 0) >= 10:
            mk = sports.tennis_markets(elo.prob(p1, p2, sf), m["bo5"])
            allp = sorted(({"k": k, "p": p, "odd": 1 / p, "hit": tennis_hit(k, s1, s2)} for k, p in mk.items()),
                          key=lambda x: -x["p"])
            rec = next((x for x in allp if x["odd"] >= QMIN), allp[-1])
            cand = [x for x in allp if x["p"] >= PMIN and x["odd"] >= QMIN]
            out.append({"lega": code, "data": m["date"], "partita": f"{p1}-{p2}", "ris": f"{s1}-{s2}",
                        "rec": rec, "slip": cand[0] if cand else None})
        w, l = (p1, p2) if m["w"] == 0 else (p2, p1)
        elo.update(w, l, sf)
    return out


# ----------------------------------------------------------------- analisi
BANDS = [(0.0, 0.6), (0.6, 0.7), (0.7, 0.75), (0.75, 0.8), (0.8, 0.85), (0.85, 1.01)]


def calibration(recs):
    rows = []
    for lo, hi in BANDS:
        b = [r for r in recs if lo <= r["p"] < hi]
        if b:
            rows.append((f"{int(lo * 100)}–{min(100, int(hi * 100))}%", len(b), np.mean([r["p"] for r in b]),
                         np.mean([r["hit"] for r in b])))
    return rows


def roi(recs):
    if not recs:
        return None
    return float(np.mean([(r["pay"] - 1) if r["hit"] else -1 for r in recs]))


def slips(rows, n, key="data"):
    """Una schedina per giorno (o giornata) con i migliori n pronostici, come 'Schedina consigliata'."""
    by = defaultdict(list)
    for r in rows:
        if r["slip"]:
            by[r[key]].append(r["slip"] | {"partita": r["partita"], "ris": r["ris"], "lega": r["lega"]})
    res = []
    for d in sorted(by):
        best = sorted(by[d], key=lambda x: -x["p"])[:n]
        if len(best) < min(n, 2):
            continue
        res.append({"giorno": d, "n": len(best), "prob": float(np.prod([x["p"] for x in best])),
                    "ok": all(x["hit"] for x in best), "giusti": sum(x["hit"] for x in best),
                    "quota": float(np.prod([x.get("pay", x["odd"]) for x in best])), "picks": best})
    return res


def slip_stats(sl):
    if not sl:
        return None
    wins = [s for s in sl if s["ok"]]
    streak = cur = 0
    for s in sl:
        cur = 0 if s["ok"] else cur + 1
        streak = max(streak, cur)
    return {"giorni": len(sl), "vinte": len(wins), "perc": len(wins) / len(sl),
            "attese": float(np.mean([s["prob"] for s in sl])), "quota_media": float(np.mean([s["quota"] for s in sl])),
            "roi": float(np.mean([(s["quota"] - 1) if s["ok"] else -1 for s in sl])),
            "serie_negativa": streak}


def pct(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def sroi(x):
    return "–" if x is None else f"{x * 100:+.1f}%"


def main():
    leagues = list(C.LEAGUES)
    with Pool(4) as pool:
        foot = [r for lst in pool.map(run_league, leagues) for r in lst]
    ten = []
    for code in ("ATP", "WTA"):
        try:
            ten += run_tennis(code)
        except Exception as e:
            print(f"  [!] tennis {code}: {e}")

    L = []
    recs = [r["rec"] for r in foot]
    L += [f"# Simulazione: l'app usata nella stagione {SEASON[:2]}/{SEASON[2:]}",
          f"_Generato il {dt.date.today()} · {len(foot)} partite di calcio in {len({r['lega'] for r in foot})} campionati"
          f" · {len(ten)} partite di tennis (ultimi 12 mesi)_", "",
          "Regole uguali all'app: pronostico consigliato = il più probabile con quota ≥ 1,20; schedina del giorno = "
          f"i {N_SLIP} pronostici più probabili (uno per partita, sicurezza ≥ 65%, quota ≥ 1,20). Modello rifatto ogni "
          "settimana con i soli dati precedenti e fuso con le quote reali. Notizie e infortuni NON inclusi.", "",
          "Quote pagate: reali (media bookmaker) per 1/X/2 e Over/Under 2.5; per gli altri mercati stimate dalle "
          f"quote reali con un margine del {int(MARGIN * 100)}%.", ""]

    # 1. calibrazione
    L += ["## 1. Il pronostico consigliato esce davvero con la probabilità indicata?", "",
          "| Fiducia indicata | Pronostici | Media indicata | Usciti davvero |", "|---|---|---|---|"]
    L += [f"| {b} | {n} | {pct(p)} | **{pct(h)}** |" for b, n, p, h in calibration(recs)]
    L += ["", f"Totale: {len(recs)} pronostici, usciti **{pct(np.mean([r['hit'] for r in recs]))}** "
              f"(indicato in media {pct(np.mean([r['p'] for r in recs]))}). Giocando 1 € su ciascuno: **{sroi(roi(recs))}**.", ""]
    kinds = defaultdict(list)
    for r in recs:
        kinds[r["k"]].append(r)
    L += ["Pronostici consigliati più frequenti:", "", "| Pronostico | Volte | Usciti | Resa giocando 1 € |", "|---|---|---|---|"]
    for k, v in sorted(kinds.items(), key=lambda kv: -len(kv[1]))[:8]:
        L.append(f"| {k} | {len(v)} | {pct(np.mean([x['hit'] for x in v]))} | {sroi(roi(v))} |")
    L.append("")

    # 2. per campionato
    L += ["## 2. Per campionato (pronostico consigliato)", "", "| Campionato | Partite | Usciti | Indicato | Resa |", "|---|---|---|---|---|"]
    for lg in leagues:
        v = [r["rec"] for r in foot if r["lega"] == lg]
        if v:
            L.append(f"| {C.LEAGUES[lg]['nome']} | {len(v)} | {pct(np.mean([x['hit'] for x in v]))} | "
                     f"{pct(np.mean([x['p'] for x in v]))} | {sroi(roi(v))} |")
    L.append("")

    # 3. schedina del giorno
    L += ["## 3. La schedina del giorno (tutti i campionati di calcio)", "",
          "| Pronostici in schedina | Giorni giocati | Uscite tutte | % | Attese dal modello | Quota media | Resa | Peggior serie di sconfitte |",
          "|---|---|---|---|---|---|---|---|"]
    results = {}
    for n in (2, 3, 4, 5, 6, 8):
        st = slip_stats(slips(foot, n))
        results[f"giorno_{n}"] = st
        if st:
            L.append(f"| {n}{' (come l’app)' if n == N_SLIP else ''} | {st['giorni']} | {st['vinte']} | **{pct(st['perc'])}** | "
                     f"{pct(st['attese'])} | {st['quota_media']:.2f} | {sroi(st['roi'])} | {st['serie_negativa']} giorni |")
    sl4 = slips(foot, N_SLIP)
    dist = defaultdict(int)
    for s in sl4:
        if s["n"] == N_SLIP:
            dist[s["giusti"]] += 1
    L += ["", f"Con la schedina da {N_SLIP}, quanti pronostici indovinati ogni giorno: " +
          " · ".join(f"{k}/{N_SLIP}: {dist[k]} giorni" for k in range(N_SLIP, -1, -1)), ""]

    # 4. Serie A giornata per giornata
    ia = sorted([r for r in foot if r["lega"] == "I1"], key=lambda r: (r["data"], r["partita"]))
    if ia:
        for i, r in enumerate(ia):
            r["giornata"] = i // 10 + 1
        g = defaultdict(list)
        for r in ia:
            g[r["giornata"]].append(r)
        all10 = [sum(x["rec"]["hit"] for x in v) for v in g.values() if len(v) == 10]
        d10 = defaultdict(int)
        for c in all10:
            d10[c] += 1
        st4 = slip_stats(slips(ia, 4, key="giornata"))
        L += ["## 4. Serie A, giornata per giornata", "",
              f"**Tutte le 10 partite col pronostico consigliato:** indovinate tutte e 10 in **{d10[10]} giornate su {len(all10)}**.",
              "", "Quante giuste su 10 a ogni giornata: " + " · ".join(f"{k}/10: {d10[k]}" for k in range(10, -1, -1) if d10[k]), "",
              f"**Schedina con le 4 più sicure della giornata:** uscita {st4['vinte']} volte su {st4['giorni']} "
              f"(**{pct(st4['perc'])}**, attese {pct(st4['attese'])}), quota media {st4['quota_media']:.2f}, resa {sroi(st4['roi'])}.", ""]
        results["serie_a_10"] = dict(d10)
        results["serie_a_4"] = st4

    # 5. tennis
    if ten:
        tr = [r["rec"] for r in ten]
        L += ["## 5. Tennis ATP + WTA (ultimi 12 mesi, senza quote reali)", "",
              "| Fiducia indicata | Pronostici | Media indicata | Usciti davvero |", "|---|---|---|---|"]
        L += [f"| {b} | {n} | {pct(p)} | **{pct(h)}** |" for b, n, p, h in calibration(tr)]
        st = slip_stats(slips(ten, N_SLIP))
        if st:
            L += ["", f"Schedina tennis del giorno da {N_SLIP}: uscita {st['vinte']} volte su {st['giorni']} "
                      f"(**{pct(st['perc'])}**, attese {pct(st['attese'])}). Resa non calcolabile (mancano le quote storiche).", ""]
        results["tennis_4"] = st

    # 6. esempi
    L += ["## 6. Alcune schedine del giorno (da 4), con risultati", ""]
    for s in [x for x in sl4 if x["n"] == N_SLIP][::max(1, len(sl4) // 8)][:8]:
        L.append(f"**{s['giorno']}** — {'✅ USCITA' if s['ok'] else f'❌ {s['giusti']}/4'} · prob. {pct(s['prob'])} · quota {s['quota']:.2f}")
        for p in s["picks"]:
            L.append(f"- {'✅' if p['hit'] else '❌'} {p['partita']} ({C.LEAGUES[p['lega']]['nome']}): **{p['k']}** {pct(p['p'])} → finita {p['ris']}")
        L.append("")

    (ROOT / "experiments" / "backtest_app.md").write_text("\n".join(L), encoding="utf-8")
    (ROOT / "experiments" / "backtest_app.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
