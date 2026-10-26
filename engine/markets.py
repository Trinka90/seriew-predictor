"""Dalla matrice dei risultati a tutti i mercati."""
import numpy as np


def goal_markets(M):
    i, j = np.indices(M.shape)
    tot, diff = i + j, i - j
    p1, px, p2 = M[diff > 0].sum(), M[diff == 0].sum(), M[diff < 0].sum()
    gg = M[(i > 0) & (j > 0)].sum()
    o = {"1": p1, "X": px, "2": p2, "1X": p1 + px, "X2": px + p2, "12": p1 + p2,
         "DNB 1": p1 / (p1 + p2), "DNB 2": p2 / (p1 + p2),
         "GG": gg, "NG": 1 - gg}
    for L in (0.5, 1.5, 2.5, 3.5, 4.5):
        o[f"Over {L}"] = M[tot > L].sum()
        o[f"Under {L}"] = 1 - o[f"Over {L}"]
    for a, b in ((1, 2), (1, 3), (2, 3), (2, 4), (3, 5)):
        o[f"Multigol {a}-{b}"] = M[(tot >= a) & (tot <= b)].sum()
    o.update({
        "Casa Over 0.5": M[i > 0].sum(), "Casa Over 1.5": M[i > 1].sum(),
        "Ospite Over 0.5": M[j > 0].sum(), "Ospite Over 1.5": M[j > 1].sum(),
        "Handicap 1 (-1)": M[diff >= 2].sum(), "Handicap 2 (-1)": M[diff <= -2].sum(),
        "1 + Over 1.5": M[(diff > 0) & (tot > 1.5)].sum(),
        "2 + Over 1.5": M[(diff < 0) & (tot > 1.5)].sum(),
        "1 + GG": M[(diff > 0) & (i > 0) & (j > 0)].sum(),
        "2 + GG": M[(diff < 0) & (i > 0) & (j > 0)].sum(),
        "GG + Over 2.5": M[(i > 0) & (j > 0) & (tot > 2.5)].sum(),
        "1X + Under 3.5": M[(diff >= 0) & (tot < 3.5)].sum(),
        "X2 + Under 3.5": M[(diff <= 0) & (tot < 3.5)].sum(),
        "Pari": M[tot % 2 == 0].sum(), "Dispari": M[tot % 2 == 1].sum(),
    })
    return o


def top_scores(M, n=6):
    flat = [(M[a, b], f"{a}-{b}") for a in range(M.shape[0]) for b in range(M.shape[1])]
    return sorted(flat, reverse=True)[:n]


def count_markets(name, model, eh, ea, lines):
    m = eh + ea
    dist = model.dist(m)
    o = {f"{name} attesi": m, f"{name} casa attesi": eh, f"{name} ospite attesi": ea}
    for L in lines:
        o[f"{name} Over {L}"] = 1 - dist.cdf(np.floor(L))
    return o
