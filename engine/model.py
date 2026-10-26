"""Modelli: Dixon-Coles (gol) con decadimento temporale + modello a tassi per angoli/cartellini."""
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

import config as C


def _weights(dates, ref_date, xi):
    age = (pd.Timestamp(ref_date) - pd.to_datetime(dates)).dt.days.values
    mask = (age > 0) & (age <= C.TRAIN_DAYS)
    return mask, np.exp(-xi * age)


class DixonColes:
    def fit(self, df, ref_date, xi=C.XI):
        mask, w = _weights(df["date"], ref_date, xi)
        ok = mask & df["fthg"].notna().values & df["ftag"].notna().values
        d, w = df[ok], w[ok]
        self.teams = sorted(set(d.home) | set(d.away))
        ix = {t: i for i, t in enumerate(self.teams)}
        h, a = d.home.map(ix).values, d.away.map(ix).values
        x, y = d.fthg.values.astype(int), d.ftag.values.astype(int)
        lgx, lgy = gammaln(x + 1), gammaln(y + 1)
        m00, m01, m10, m11 = (x == 0) & (y == 0), (x == 0) & (y == 1), (x == 1) & (y == 0), (x == 1) & (y == 1)
        n = len(self.teams)

        def nll(p):
            att, de, home, rho = p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1]
            lam = np.exp(home + att[h] + de[a])
            mu = np.exp(att[a] + de[h])
            t = np.ones_like(lam)
            t[m00] = 1 - lam[m00] * mu[m00] * rho
            t[m01] = 1 + lam[m01] * rho
            t[m10] = 1 + mu[m10] * rho
            t[m11] = 1 - rho
            ll = np.log(np.clip(t, 1e-10, None)) + x * np.log(lam) - lam - lgx + y * np.log(mu) - mu - lgy
            return -(w * ll).sum() + 100 * att.sum() ** 2 + 0.5 * (att @ att + de @ de)

        p0 = np.r_[np.zeros(2 * n), 0.25, -0.05]
        bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.3, 0.3)]
        res = minimize(nll, p0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 500})
        p = res.x
        self.att = dict(zip(self.teams, p[:n]))
        self.de = dict(zip(self.teams, p[n:2 * n]))
        self.home, self.rho = p[2 * n], p[2 * n + 1]
        # squadre senza storico (neopromosse): valori da squadra debole
        self._att_def = np.quantile(p[:n], 0.2)
        self._de_def = np.quantile(p[n:2 * n], 0.8)
        return self

    def lambdas(self, home, away):
        ah, dh = self.att.get(home, self._att_def), self.de.get(home, self._de_def)
        aa, da = self.att.get(away, self._att_def), self.de.get(away, self._de_def)
        return float(np.exp(self.home + ah + da)), float(np.exp(aa + dh))

    def ratings(self):
        r = pd.DataFrame({"attacco": self.att, "difesa": self.de})
        r["forza"] = r.attacco - r.difesa
        return r.sort_values("forza", ascending=False)


def score_matrix(lam, mu, rho=0.0, maxg=C.MAX_GOALS):
    g = np.arange(maxg + 1)
    M = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    M[0, 0] *= 1 - lam * mu * rho
    M[0, 1] *= 1 + lam * rho
    M[1, 0] *= 1 + mu * rho
    M[1, 1] *= 1 - rho
    M = np.clip(M, 0, None)
    return M / M.sum()


class RateModel:
    """Tassi attesi per conteggi (angoli, cartellini): forza 'fatti' e 'subiti' per squadra."""

    def __init__(self, hcol, acol, k=4.0):
        self.hcol, self.acol, self.k = hcol, acol, k

    def fit(self, df, ref_date, xi=C.XI):
        mask, w = _weights(df["date"], ref_date, xi)
        ok = mask & df[self.hcol].notna().values & df[self.acol].notna().values
        d, w = df[ok], w[ok]
        hv, av = d[self.hcol].values.astype(float), d[self.acol].values.astype(float)
        self.Lh, self.La = np.average(hv, weights=w), np.average(av, weights=w)
        self.f, self.g = {}, {}
        for t in set(d.home) | set(d.away):
            ih, ia = (d.home == t).values, (d.away == t).values
            obs_f = np.r_[hv[ih] / self.Lh, av[ia] / self.La]
            obs_g = np.r_[av[ih] / self.La, hv[ia] / self.Lh]
            ww = np.r_[w[ih], w[ia]]
            self.f[t] = (ww @ obs_f + self.k) / (ww.sum() + self.k)
            self.g[t] = (ww @ obs_g + self.k) / (ww.sum() + self.k)
        tot = hv + av
        m = np.average(tot, weights=w)
        v = np.average((tot - m) ** 2, weights=w)
        self.r = m ** 2 / (v - m) if v > 1.05 * m else None   # sovradispersione -> Binomiale Negativa
        return self

    def expect(self, home, away):
        eh = self.Lh * self.f.get(home, 1) * self.g.get(away, 1)
        ea = self.La * self.f.get(away, 1) * self.g.get(home, 1)
        return eh, ea

    def dist(self, mean):
        if self.r:
            return nbinom(self.r, self.r / (self.r + mean))
        return poisson(mean)


def ht_ft(lam, mu, r_h, r_a, maxg=8):
    """Primo tempo e Parziale/Finale assumendo tempi indipendenti."""
    g = np.arange(maxg + 1)
    k = np.arange(-maxg, maxg + 1)

    def diff(l, m):
        M = np.outer(poisson.pmf(g, l), poisson.pmf(g, m))
        i, j = np.indices(M.shape)
        out = np.zeros(2 * maxg + 1)
        np.add.at(out, (i - j + maxg).ravel(), M.ravel())
        return out, M

    d1, M1 = diff(lam * r_h, mu * r_a)
    d2, _ = diff(lam * (1 - r_h), mu * (1 - r_a))
    s = lambda v: "1" if v > 0 else ("X" if v == 0 else "2")
    res = {}
    for a, p1 in zip(k, d1):
        for b, p2 in zip(k, d2):
            key = f"PF {s(a)}/{s(a + b)}"
            res[key] = res.get(key, 0) + p1 * p2
    i, j = np.indices(M1.shape)
    res.update({"1T 1": d1[k > 0].sum(), "1T X": d1[k == 0].sum(), "1T 2": d1[k < 0].sum(),
                "1T Over 0.5": 1 - M1[0, 0], "1T Over 1.5": M1[i + j > 1].sum(),
                "1T GG": M1[(i > 0) & (j > 0)].sum()})
    return res
