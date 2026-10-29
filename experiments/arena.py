"""Arena dei modelli: stessi dati, stesse regole, stesse quote Bet365. Solo report, l'app non cambia.

Stagione 2024/25 = taratura (pesi dell'ensemble, ritaratura per tipo di pronostico)
Stagione 2025/26 = giudizio (nessun modello ha visto quei risultati)
Ogni previsione usa solo dati precedenti alla partita (modelli rifatti ogni settimana o ogni mese).

Modelli:
  Mercato            quote medie di apertura dei bookmaker
  App attuale        Dixon-Coles (gol + tiri in porta) fuso al 90% con il mercato
  Dixon-Coles        solo modello statistico, senza quote
  Elo                rating Elo calcio con margine di gol
  Pi-rating          rating separati casa/trasferta (Constantinou & Fenton)
  ML                 gradient boosting su forma, tiri, riposo, rating (senza quote)
  ML + quote         come sopra, con le quote tra le variabili
  Ensemble           media pesata dei modelli statistici (pesi scelti sul 2024/25)
  Ensemble + quote   come sopra, mercato incluso tra i modelli
  App + ritaratura   app attuale con correzione per tipo di pronostico (stimata sul 2024/25)
Esecuzione (da engine/, dopo aver scaricato lo storico): python ../experiments/arena.py
"""
import datetime as dt
import sys
from collections import defaultdict, deque
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "experiments"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

import backtest_app as BA  # noqa: E402
import config as C  # noqa: E402
import context as X  # noqa: E402
from db import connect  # noqa: E402
from markets import goal_markets  # noqa: E402
from model import DixonColes, blend, implied_lambdas, market_probs, score_matrix  # noqa: E402

TUNE, TEST = "2425", "2526"
SEASONS_FEAT = ("2324", TUNE, TEST)     # stagioni con previsioni (2022/23 serve solo a far partire i rating)
EPS = 1e-6


# ----------------------------------------------------------------- rating sequenziali
def psi(x):
    return np.sign(x) * (10 ** (abs(x) / 3) - 1)


class Pi:
    """Pi-rating: un rating casa e uno trasferta per squadra; aggiornati dall'errore sulla differenza reti."""
    LAM, GAM = 0.035, 0.7

    def __init__(self):
        self.H, self.A = defaultdict(float), defaultdict(float)

    def pred(self, h, a):
        return psi(self.H[h]) - psi(self.A[a])

    def update(self, h, a, gh, ga):
        e = (gh - ga) - self.pred(h, a)
        w = 3 * np.log10(1 + abs(e)) * np.sign(e)
        self.H[h] += self.LAM * w
        self.A[h] += self.GAM * self.LAM * w
        self.A[a] -= self.LAM * w
        self.H[a] -= self.GAM * self.LAM * w


class Elo:
    K, HOME = 20.0, 60.0

    def __init__(self):
        self.r = defaultdict(lambda: 1500.0)

    def diff(self, h, a):
        return self.r[h] + self.HOME - self.r[a]

    def update(self, h, a, gh, ga):
        d = self.diff(h, a)
        exp = 1 / (1 + 10 ** (-d / 400))
        res = 1.0 if gh > ga else 0.5 if gh == ga else 0.0
        mult = np.log(abs(gh - ga) + 1) + 1 if gh != ga else 1.0
        self.r[h] += self.K * mult * (res - exp)
        self.r[a] -= self.K * mult * (res - exp)


# ----------------------------------------------------------------- dati per campionato
def build_league(lg):
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == lg].dropna(subset=["fthg", "ftag"]).sort_values("date").reset_index(drop=True)
    if len(df[df.season == TEST]) < 100:
        return []
    # Dixon-Coles settimanale (come l'app) sulle stagioni con previsioni
    dcl = {}
    for wk in df[df.season.isin(SEASONS_FEAT)].date.dt.to_period("W").unique():
        try:
            dc = DixonColes().fit(df, wk.start_time)
        except Exception:      # nessuna partita precedente (inizio dello storico)
            continue
        for i in df.index[df.date.dt.to_period("W") == wk]:
            r = df.loc[i]
            dcl[i] = (*dc.lambdas(r.home, r.away), dc.rho)
    pi, elo = Pi(), Elo()
    last = defaultdict(lambda: deque(maxlen=6))     # ultime partite: (data, punti, gf, gs, tiri in porta f, s)
    rows = []
    for i, r in df.iterrows():
        h, a, gh, ga = r.home, r.away, int(r.fthg), int(r.ftag)

        def form(t):
            g = list(last[t])[-5:]
            if not g:
                return [np.nan] * 5
            return [np.mean([x[1] for x in g]), np.mean([x[2] for x in g]), np.mean([x[3] for x in g]),
                    np.nanmean([x[4] for x in g]) if any(x[4] == x[4] for x in g) else np.nan,
                    np.nanmean([x[5] for x in g]) if any(x[5] == x[5] for x in g) else np.nan]

        def rest(t):
            return min(30, (r.date - last[t][-1][0]).days) if last[t] else 30

        if r.season in SEASONS_FEAT and i in dcl:
            mp = market_probs(r.odd_h, r.odd_d, r.odd_a, r.odd_o25, r.odd_u25)
            rows.append({
                "lega": lg, "season": r.season, "date": r.date, "home": h, "away": a, "gh": gh, "ga": ga,
                "res": 0 if gh > ga else 1 if gh == ga else 2, "over": int(gh + ga > 2),
                "dc_lh": dcl[i][0], "dc_la": dcl[i][1], "rho": dcl[i][2],
                "pi": pi.pred(h, a), "pi_h": pi.H[h], "pi_a": pi.A[a], "elo": elo.diff(h, a),
                **{f"fh{k}": v for k, v in enumerate(form(h))}, **{f"fa{k}": v for k, v in enumerate(form(a))},
                "rest_h": rest(h), "rest_a": rest(a),
                "m1": mp["1x2"][0] if mp else np.nan, "mx": mp["1x2"][1] if mp else np.nan,
                "m2": mp["1x2"][2] if mp else np.nan, "mo": mp.get("over", np.nan) if mp else np.nan,
                "bk_h": r.bk_h, "bk_d": r.bk_d, "bk_a": r.bk_a, "bk_o25": r.bk_o25, "bk_u25": r.bk_u25})
        pi.update(h, a, gh, ga)
        elo.update(h, a, gh, ga)
        pts = (3, 0) if gh > ga else (1, 1) if gh == ga else (0, 3)
        fl = lambda v: float(v) if v is not None and v == v else np.nan
        last[h].append((r.date, pts[0], gh, ga, fl(r.hst), fl(r.ast)))
        last[a].append((r.date, pts[1], ga, gh, fl(r.ast), fl(r.hst)))
    print(f"  {lg}: {len(rows)} partite", flush=True)
    return rows


# ----------------------------------------------------------------- modelli 1X2
def ordered_fit(x, y):
    """Logit ordinato: da un numero (differenza di forza) a probabilità 2/X/1."""
    def nll(p):
        b, c1, c2 = p[0], p[1], p[1] + np.exp(p[2])
        pa = 1 / (1 + np.exp(-(c1 - b * x)))
        pd_ = 1 / (1 + np.exp(-(c2 - b * x))) - pa
        ph = 1 - pa - pd_
        pr = np.where(y == 0, ph, np.where(y == 1, pd_, pa))
        return -np.sum(np.log(np.clip(pr, EPS, 1)))
    s = np.std(x) or 1
    r = minimize(nll, [1 / s, -0.3, -0.5], method="Nelder-Mead", options={"maxiter": 4000})
    return r.x


def ordered_pred(p, x):
    b, c1, c2 = p[0], p[1], p[1] + np.exp(p[2])
    pa = 1 / (1 + np.exp(-(c1 - b * x)))
    pd_ = 1 / (1 + np.exp(-(c2 - b * x))) - pa
    return np.c_[1 - pa - pd_, pd_, pa]


def dc_probs(df):
    P, O = [], []
    for r in df.itertuples():
        mk = goal_markets(score_matrix(r.dc_lh, r.dc_la, r.rho))
        P.append([mk["1"], mk["X"], mk["2"]])
        O.append(mk["Over 2.5"])
    return np.array(P), np.array(O)


FEATS = ["dc_lh", "dc_la", "pi", "pi_h", "pi_a", "elo", "rest_h", "rest_a"] + \
        [f"f{s}{k}" for s in "ha" for k in range(5)]
MKT = ["m1", "mx", "m2", "mo"]


def monthly(df, fit_pred):
    """Rifà il modello ogni mese con i soli dati precedenti; ritorna le previsioni per le stagioni TUNE e TEST."""
    out = {}
    target = df[df.season.isin([TUNE, TEST])]
    for mo in sorted(target.date.dt.to_period("M").unique()):
        start = mo.start_time
        train = df[df.date < start]
        test = target[target.date.dt.to_period("M") == mo]
        if len(train) < 500 or test.empty:
            continue
        out.update(dict(zip(test.index, fit_pred(train, test))))
    return out


def run_models(df):
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB
    preds = {}
    p, o = dc_probs(df)
    preds["Dixon-Coles"] = {i: (p[k], o[k]) for k, i in enumerate(df.index)}
    preds["Mercato"] = {i: (np.array([r.m1, r.mx, r.m2]), r.mo) for i, r in zip(df.index, df.itertuples())}

    def ord_model(col):
        def f(train, test):
            par = ordered_fit(train[col].values, train.res.values)
            P = ordered_pred(par, test[col].values)
            return [(P[k], np.nan) for k in range(len(test))]
        return f
    print("  Elo / Pi-rating…", flush=True)
    preds["Elo"] = monthly(df, ord_model("elo"))
    preds["Pi-rating"] = monthly(df, ord_model("pi"))

    def ml(cols0):
        def f(train, test):
            cols = list(cols0)
            tr = train.dropna(subset=["m1"]) if "m1" in cols else train
            tr = tr.copy()
            for c in cols:
                tr[c] = tr[c].astype(float)
            cols = [c for c in cols if tr[c].astype(float).notna().sum() > 50]   # scarta colonne vuote
            c1 = HGB(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=0)
            c1.fit(tr[cols], tr.res)
            c2 = HGB(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=0)
            c2.fit(tr[cols], tr.over)
            P = c1.predict_proba(test[cols].astype(float))
            O = c2.predict_proba(test[cols].astype(float))[:, 1]
            return [(P[k], O[k]) for k in range(len(test))]
        return f
    print("  Machine learning…", flush=True)
    preds["ML"] = monthly(df, ml(FEATS))
    preds["ML + quote"] = monthly(df, ml(FEATS + MKT))
    return preds


def logloss(P, y):
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))))


def ens(preds, names, w, idx):
    """Media geometrica pesata (pesi normalizzati) di 1X2; per l'Over media delle sole fonti che lo stimano."""
    w = np.array(w) / np.sum(w)
    L = sum(wi * np.log(np.clip(np.array([preds[n][i][0] for i in idx]), EPS, 1)) for wi, n in zip(w, names))
    P = np.exp(L)
    P /= P.sum(1, keepdims=True)
    ov = []
    for i in idx:
        vals = [(wi, preds[n][i][1]) for wi, n in zip(w, names) if preds[n][i][1] == preds[n][i][1]]
        ov.append(sum(a * b for a, b in vals) / sum(a for a, _ in vals) if vals else np.nan)
    return P, np.array(ov)


def fit_weights(preds, names, idx, y):
    def loss(v):
        P, _ = ens(preds, names, np.exp(v), idx)
        return logloss(P, y)
    r = minimize(loss, np.zeros(len(names)), method="Nelder-Mead", options={"maxiter": 600})
    w = np.exp(r.x)
    return w / w.sum()


# ----------------------------------------------------------------- pronostici come nell'app
def picks_for(args):
    """Per ogni partita: mercati completi dal modello, pronostico consigliato, candidato schedina."""
    rows, recal = args
    out = []
    for r in rows:
        mk = r["mk"]
        if recal:
            mk = {k: (1 / (1 + np.exp(-(recal[k][0] * np.log(v / (1 - v)) + recal[k][1])))
                      if k in recal and 0 < v < 1 else v) for k, v in mk.items()}
        book, marg, real = r["book"], r["marg"], r["real"]
        h, a = r["gh"], r["ga"]

        def pk(k):
            q = real.get(k)
            odd = float(q) if q and q == q else max(1.01, 1 / (book[k] * (1 + marg)))
            return {"k": k, "p": float(mk[k]), "odd": odd, "hit": BA.hit(k, h, a)}
        allp = sorted((pk(k) for k in BA.ALL_KEYS), key=lambda x: -x["p"])
        rec = next((x for x in allp if x["odd"] >= BA.QMIN), allp[-1])
        cand = [x for x in allp if x["k"] in BA.SLIP_KEYS and x["p"] >= BA.PMIN and x["odd"] >= BA.QMIN]
        out.append({"data": r["data"], "rec": rec, "slip": cand[0] if cand else None})
    return out


def markets_from(P1x2, over, rho):
    mp = {"1x2": np.asarray(P1x2) / np.sum(P1x2)}
    if over == over and over is not None:
        mp["over"] = float(over)
    lh, la = implied_lambdas(mp, rho)
    return goal_markets(score_matrix(lh, la, rho))


def build_market_rows(args):
    df_part, probs = args
    rows = []
    for (i, r), (P, O) in zip(df_part.iterrows(), probs):
        mpb = {"1x2": np.array([r.m1, r.mx, r.m2])}
        if r.mo == r.mo:
            mpb["over"] = r.mo
        lb, mb = implied_lambdas(mpb, r.rho)
        book = goal_markets(score_matrix(lb, mb, r.rho))
        b3 = [r.bk_h, r.bk_d, r.bk_a]
        marg = (sum(1 / x for x in b3) - 1) if all(x and x == x and x > 1 for x in b3) else BA.MARGIN
        real = {"1": r.bk_h, "X": r.bk_d, "2": r.bk_a, "Over 2.5": r.bk_o25, "Under 2.5": r.bk_u25}
        mk = P if isinstance(P, dict) else markets_from(P, O, r.rho)
        rows.append({"data": r.date.strftime("%Y-%m-%d"), "gh": r.gh, "ga": r.ga, "mk": mk, "book": book,
                     "marg": marg, "real": real})
    return rows


def app_markets(df_part):
    """App attuale: lambda Dixon-Coles fusi al 90% con quelli impliciti nel mercato."""
    out = []
    for r in df_part.itertuples():
        mpb = {"1x2": np.array([r.m1, r.mx, r.m2])}
        if r.mo == r.mo:
            mpb["over"] = r.mo
        lb, mb = implied_lambdas(mpb, r.rho)
        lam, mu = blend(r.dc_lh, r.dc_la, lb, mb, C.MARKET_WEIGHT)
        out.append((goal_markets(score_matrix(lam, mu, r.rho)), None))
    return out


def chunks(df, n=8):
    k = int(np.ceil(len(df) / n))
    return [df.iloc[j:j + k] for j in range(0, len(df), k)]


def evaluate(pool, df, name, probs_list, recal=None):
    parts = chunks(df)
    pr_parts, j = [], 0
    for p in parts:
        pr_parts.append(probs_list[j:j + len(p)])
        j += len(p)
    rows = [x for lst in pool.map(build_market_rows, list(zip(parts, pr_parts))) for x in lst]
    res = [x for lst in pool.map(picks_for, [(rows[j:j + 500], recal) for j in range(0, len(rows), 500)]) for x in lst]
    return rows, res


def fit_recal(rows):
    """Correzione per tipo di pronostico: logit(p') = a·logit(p) + b, stimata sulla stagione di taratura."""
    out = {}
    for k in BA.ALL_KEYS:
        p = np.clip(np.array([r["mk"][k] for r in rows]), 1e-4, 1 - 1e-4)
        y = np.array([BA.hit(k, r["gh"], r["ga"]) for r in rows], float)
        lg = np.log(p / (1 - p))

        def nll(v):
            q = 1 / (1 + np.exp(-(v[0] * lg + v[1])))
            return -np.sum(y * np.log(np.clip(q, EPS, 1)) + (1 - y) * np.log(np.clip(1 - q, EPS, 1)))
        out[k] = minimize(nll, [1.0, 0.0], method="Nelder-Mead").x
    return out


def pct(x):
    return "–" if x is None or x != x else f"{x * 100:.1f}%"


def main():
    with Pool(4) as pool:
        df = pd.DataFrame([r for lst in pool.map(build_league, list(C.LEAGUES)) for r in lst])
    df = df.sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["m1"]).reset_index(drop=True)          # servono le quote per confrontare col mercato
    print(f"Partite con quote: {len(df)}", flush=True)
    preds = run_models(df)

    tune = df.index[(df.season == TUNE) & df.index.isin(preds["ML + quote"].keys())]
    test = df.index[(df.season == TEST) & df.index.isin(preds["ML + quote"].keys())]
    yt, ye = df.loc[tune, "res"].values, df.loc[test, "res"].values

    stat = ["Dixon-Coles", "Elo", "Pi-rating", "ML"]
    w_stat = fit_weights(preds, stat, tune, yt)
    w_all = fit_weights(preds, stat + ["Mercato"], tune, yt)
    for nm, names, w in (("Ensemble", stat, w_stat), ("Ensemble + quote", stat + ["Mercato"], w_all)):
        P, O = ens(preds, names, w, list(tune) + list(test))
        preds[nm] = {i: (P[k], O[k]) for k, i in enumerate(list(tune) + list(test))}

    D = df.loc[test]
    models = ["Mercato", "Dixon-Coles", "Elo", "Pi-rating", "ML", "ML + quote", "Ensemble", "Ensemble + quote"]
    results = {}
    with Pool(4) as pool:
        # app attuale e app ritarata
        app_t = app_markets(df.loc[tune])
        rows_t, _ = evaluate(pool, df.loc[tune], "App", app_t)
        recal = fit_recal(rows_t)
        app_e = app_markets(D)
        for nm, rc in (("App attuale", None), ("App + ritaratura", recal)):
            rows, res = evaluate(pool, D, nm, app_e, rc)
            results[nm] = (rows, res)
        for nm in models:
            print(f"  pronostici {nm}…", flush=True)
            plist = [preds[nm][i] for i in test]
            results[nm] = evaluate(pool, D, nm, plist)

    yo = D.over.values
    L = [f"# Arena dei modelli — stagione {TEST[:2]}/{TEST[2:]}",
         f"_Generato il {dt.date.today()} · {len(D)} partite di prova in {D.lega.nunique()} campionati · "
         f"taratura su {len(tune)} partite della stagione {TUNE[:2]}/{TUNE[2:]}_", "",
         f"Pesi ensemble: " + ", ".join(f"{n} {w:.2f}" for n, w in zip(stat, w_stat)) +
         " · con quote: " + ", ".join(f"{n} {w:.2f}" for n, w in zip(stat + ['Mercato'], w_all)), "",
         "**Precisione** (LogLoss: più basso = meglio) · **Pronostico** = il consigliato come nell'app (più probabile con quota "
         "Bet365 ≥ 1,20) · **Schedina** = le 4 più sicure del giorno, quote Bet365", "",
         "| Modello | LogLoss 1X2 | Esito 1X2 indovinato | LogLoss Over 2.5 | Pronostico uscito | Indicato | Resa singoli | Schedina 4 uscita | Resa schedina |",
         "|---|---|---|---|---|---|---|---|---|"]
    order = ["Mercato", "App attuale", "App + ritaratura", "Dixon-Coles", "Elo", "Pi-rating", "ML", "ML + quote",
             "Ensemble", "Ensemble + quote"]
    summary = {}
    for nm in order:
        rows, res = results[nm]
        P = np.array([[r["mk"]["1"], r["mk"]["X"], r["mk"]["2"]] for r in rows])
        Po = np.clip(np.array([r["mk"]["Over 2.5"] for r in rows]), EPS, 1 - EPS)
        ll = logloss(P / P.sum(1, keepdims=True), ye)
        acc = float(np.mean(P.argmax(1) == ye))
        llo = float(-np.mean(yo * np.log(Po) + (1 - yo) * np.log(1 - Po)))
        recs = [x["rec"] for x in res]
        hitr, ind = np.mean([x["hit"] for x in recs]), np.mean([x["p"] for x in recs])
        rroi = np.mean([(x["odd"] - 1) if x["hit"] else -1 for x in recs])
        st = BA.slip_stats(BA.slips([{**x, "partita": "", "ris": "", "lega": ""} for x in res], 4))
        summary[nm] = ll
        L.append(f"| {nm} | {ll:.4f} | {pct(acc)} | {llo:.4f} | **{pct(hitr)}** | {pct(ind)} | {BA.sroi(rroi)} | "
                 f"{pct(st['perc']) if st else '–'} | {BA.sroi(st['roi']) if st else '–'} |")
    best = min((n for n in order if n != "Mercato"), key=lambda n: summary[n])
    L += ["", f"Più preciso (escluso il mercato puro): **{best}** — LogLoss {summary[best]:.4f} contro "
              f"{summary['App attuale']:.4f} dell'app attuale e {summary['Mercato']:.4f} del mercato.", ""]
    # dettaglio per campionato: app vs migliore
    L += ["## LogLoss 1X2 per campionato", "", "| Campionato | Partite | Mercato | App attuale | " + best + " |", "|---|---|---|---|---|"]
    for lg in C.LEAGUES:
        m = (D.lega == lg).values
        if m.sum() < 50:
            continue
        def ll_of(nm):
            P = np.array([[r["mk"]["1"], r["mk"]["X"], r["mk"]["2"]] for r in results[nm][0]])[m]
            return logloss(P / P.sum(1, keepdims=True), ye[m])
        L.append(f"| {C.LEAGUES[lg]['nome']} | {m.sum()} | {ll_of('Mercato'):.4f} | {ll_of('App attuale'):.4f} | {ll_of(best):.4f} |")
    (ROOT / "experiments" / "arena.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
