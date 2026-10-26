"""Walk-forward: ogni settimana rifitta su dati passati e prevede la settimana successiva.
Confronta con le quote medie dei bookmaker (il vero benchmark da battere)."""
import numpy as np
import pandas as pd

import config as C
import context as X
from db import connect
from markets import goal_markets
from model import DixonColes, score_matrix


def run(season="2526", edge=C.VALUE_EDGE, league="I1", verbose=True):
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == league].reset_index(drop=True)
    test = df[df.season == season].dropna(subset=["fthg", "ftag"])
    if test.empty:
        print(f"  {league}: nessuna partita per la stagione {season}")
        return None
    weeks = test.date.dt.to_period("W").unique()
    rec = []
    for i, wk in enumerate(weeks, 1):
        start = wk.start_time
        part = test[test.date.dt.to_period("W") == wk]
        dc = DixonColes().fit(df, start)
        for r in part.itertuples():
            mk = goal_markets(score_matrix(*dc.lambdas(r.home, r.away), dc.rho))
            res = "1" if r.fthg > r.ftag else ("X" if r.fthg == r.ftag else "2")
            rec.append({"p1": mk["1"], "px": mk["X"], "p2": mk["2"], "po": mk["Over 2.5"],
                        "res": res, "over": (r.fthg + r.ftag) > 2.5,
                        "o1": r.odd_h, "ox": r.odd_d, "o2": r.odd_a, "oo": r.odd_o25, "ou": r.odd_u25})
    print(f"  {len(weeks)} settimane simulate")
    d = pd.DataFrame(rec)

    P = d[["p1", "px", "p2"]].values
    Y = np.c_[d.res == "1", d.res == "X", d.res == "2"].astype(float)
    has = d[["o1", "ox", "o2"]].notna().all(axis=1).values
    B = 1 / d[["o1", "ox", "o2"]].values
    B = B / B.sum(axis=1, keepdims=True)

    def scores(P, Y):
        return (-np.log(np.clip((P * Y).sum(1), 1e-12, None)).mean(),
                ((P - Y) ** 2).sum(1).mean(), (P.argmax(1) == Y.argmax(1)).mean())

    m = scores(P[has], Y[has])
    b = scores(B[has], Y[has])
    print(f"\nStagione {season}: {len(d)} partite ({has.sum()} con quote)")
    print(f"{'':12}{'LogLoss':>9}{'Brier':>8}{'Acc 1X2':>9}")
    print(f"{'Modello':12}{m[0]:9.4f}{m[1]:8.4f}{m[2]:9.1%}")
    print(f"{'Bookmaker':12}{b[0]:9.4f}{b[1]:8.4f}{b[2]:9.1%}")

    # ROI scommettendo 1 unità dove il modello vede edge
    bets = []
    for r in d[has].itertuples():
        for p, o, win in ((r.p1, r.o1, r.res == "1"), (r.px, r.ox, r.res == "X"), (r.p2, r.o2, r.res == "2"),
                          (r.po, r.oo, r.over), (1 - r.po, r.ou, not r.over)):
            if o and not np.isnan(o) and p * o - 1 > edge:
                bets.append(o - 1 if win else -1)
    metrics = {"stagione": season, "partite": int(len(d)), "logloss_modello": round(m[0], 4),
               "logloss_book": round(b[0], 4), "brier_modello": round(m[1], 4), "brier_book": round(b[1], 4),
               "acc_modello": round(m[2], 4), "acc_book": round(b[2], 4),
               "value_bet": len(bets), "roi": round(float(np.mean(bets)), 4) if bets else None}
    if bets:
        print(f"Value bet (edge>{edge:.0%}): {len(bets)} giocate, ROI {np.mean(bets):+.1%}, "
              f"profitto {np.sum(bets):+.1f} unità")
    print("Nota: ROI su quote MEDIE di chiusura. Se il modello non batte il bookmaker in LogLoss, "
          "il ROI positivo è quasi sempre rumore.")
    return metrics
