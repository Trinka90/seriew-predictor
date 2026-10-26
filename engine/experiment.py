"""Esperimento: quali impostazioni del modello prevedono meglio?
Walk-forward sulla stagione 2025/26 di ogni campionato (refit settimanale, solo dati passati).
Varianti: decadimento temporale (xi) x peso tiri in porta (sot) x peso quote di mercato (w).
Metrica: LogLoss 1X2 e Over 2.5 (più basso = meglio). Output: ../experiments/result.json e .md"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import context as X
from db import connect
from model import DixonColes, blend, implied_lambdas, market_probs, score_matrix

SEASON = "2526"
XIS = [0.0010, 0.0019, 0.0030]
SOTS = [0.0, 0.3, 0.5, 0.7]
WS = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
OUT = Path(__file__).resolve().parent.parent / "experiments"


def probs(lam, mu, rho):
    M = score_matrix(lam, mu, rho, maxg=8)
    i, j = np.indices(M.shape)
    return np.array([M[i > j].sum(), M[i == j].sum(), M[i < j].sum()]), M[i + j > 2].sum()


def run_league(lg):
    t0 = time.time()
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == lg].reset_index(drop=True)
    test = df[df.season == SEASON].dropna(subset=["fthg", "ftag"])
    if len(test) < 100:
        return lg, None
    # quote di mercato e gol attesi impliciti (una volta per partita)
    mk = {}
    for r in test.itertuples():
        mp = market_probs(r.odd_h, r.odd_d, r.odd_a, r.odd_o25, r.odd_u25)
        if mp:
            mk[r.Index] = (mp, implied_lambdas(mp, -0.05))
    test = test[test.index.isin(mk)]
    y = {i: (0 if r.fthg > r.ftag else 1 if r.fthg == r.ftag else 2, int(r.fthg + r.ftag > 2.5))
         for i, r in test.iterrows()}
    ll = {}   # (xi, sot, w) -> [sum logloss 1x2, sum logloss over, n]
    ll_book = [0.0, 0.0, 0]
    weeks = test.date.dt.to_period("W").unique()
    for wk in weeks:
        part = test[test.date.dt.to_period("W") == wk]
        fits = {(xi, s): DixonColes().fit(df, wk.start_time, xi=xi, sot=s) for xi in XIS for s in SOTS}
        for idx, r in part.iterrows():
            res, ov = y[idx]
            mp, (lb, mb) = mk[idx]
            ll_book[0] -= np.log(mp["1x2"][res])
            if "over" in mp:
                ll_book[1] -= np.log(mp["over"] if ov else 1 - mp["over"])
            ll_book[2] += 1
            for (xi, s), dc in fits.items():
                lm, mm = dc.lambdas(r.home, r.away)
                for w in WS:
                    l, m = blend(lm, mm, lb, mb, w)
                    p, po = probs(l, m, dc.rho)
                    acc = ll.setdefault((xi, s, w), [0.0, 0.0, 0])
                    acc[0] -= np.log(max(p[res], 1e-9))
                    acc[1] -= np.log(max(po if ov else 1 - po, 1e-9))
                    acc[2] += 1
    out = {"n": ll_book[2], "book": [ll_book[0] / ll_book[2], ll_book[1] / ll_book[2]],
           "varianti": {f"{xi}|{s}|{w}": [v[0] / v[2], v[1] / v[2]] for (xi, s, w), v in ll.items()},
           "secondi": round(time.time() - t0)}
    print(f"{lg}: {out['n']} partite in {out['secondi']}s", flush=True)
    return lg, out


def main():
    leagues = [lg for lg in C.LEAGUES if len(sys.argv) < 2 or lg in sys.argv[1:]]
    with Pool(4) as pool:
        res = dict(r for r in pool.map(run_league, leagues) if r[1])
    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(res, indent=1))

    # riepilogo: media pesata sul numero di partite
    keys = list(next(iter(res.values()))["varianti"])
    N = sum(r["n"] for r in res.values())
    agg = {k: [sum(r["varianti"][k][j] * r["n"] for r in res.values()) / N for j in (0, 1)] for k in keys}
    book = [sum(r["book"][j] * r["n"] for r in res.values()) / N for j in (0, 1)]
    lines = [f"# Esperimento stagione {SEASON} — {N} partite, {len(res)} campionati",
             f"Bookmaker: LogLoss 1X2 {book[0]:.4f} | Over2.5 {book[1]:.4f}", "",
             "## Migliori 15 (1X2)", "| xi | sot | w mercato | LL 1X2 | LL O2.5 |", "|---|---|---|---|---|"]
    for k, v in sorted(agg.items(), key=lambda kv: kv[1][0])[:15]:
        xi, s, w = k.split("|")
        lines.append(f"| {xi} | {s} | {w} | {v[0]:.4f} | {v[1]:.4f} |")
    lines += ["", "## Solo modello (w=0)", "| xi | sot | LL 1X2 | LL O2.5 |", "|---|---|---|---|"]
    for k, v in sorted(((k, v) for k, v in agg.items() if k.endswith("|0.0")), key=lambda kv: kv[1][0]):
        xi, s, _ = k.split("|")
        lines.append(f"| {xi} | {s} | {v[0]:.4f} | {v[1]:.4f} |")
    lines += ["", "## Per campionato: book vs miglior variante", "| lega | n | book | miglior | variante |", "|---|---|---|---|---|"]
    for lg, r in res.items():
        k, v = min(r["varianti"].items(), key=lambda kv: kv[1][0])
        lines.append(f"| {lg} | {r['n']} | {r['book'][0]:.4f} | {v[0]:.4f} | {k} |")
    (OUT / "result.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
