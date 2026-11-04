"""Isolated football experiment. Never writes app data or the source database."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from model import DixonColes, blend, implied_lambdas, market_probs, score_matrix
from markets import goal_markets

KEYS = ['1', 'X', '2', 'Over 2.5', 'Under 2.5']
ODDS = ['bk_h', 'bk_d', 'bk_a', 'bk_o25', 'bk_u25']


def features(shape):
    h, a = np.indices(shape)
    return np.stack([(h > a).astype(float), (h == a).astype(float), (h + a) / 3], axis=-1)


def apply_calibration(matrix, theta):
    # One joint distribution: all derived markets remain coherent.
    logits = np.log(np.maximum(matrix, 1e-300)) + features(matrix.shape) @ theta
    return np.exp(logits - logsumexp(logits))


def fit_calibration(matrices, scores, penalty=0.01):
    if len(matrices) < 100:
        raise ValueError('At least 100 training matches required')
    shapes = {m.shape for m in matrices}
    if len(shapes) != 1:
        raise ValueError('Inconsistent score grids')
    f = features(matrices[0].shape).reshape(-1, 3)
    logp = np.log(np.maximum(np.asarray(matrices).reshape(len(matrices), -1), 1e-300))
    y = np.array([h * matrices[0].shape[1] + a for h, a in scores])
    obs = f[y].mean(axis=0)
    def objective(theta):
        logits = logp + f @ theta
        z = logsumexp(logits, axis=1)
        loss = np.mean(z - logits[np.arange(len(y)), y]) + penalty * (theta @ theta)
        q = np.exp(logits - z[:, None])
        grad = (q @ f).mean(axis=0) - obs + 2 * penalty * theta
        return loss, grad
    res = minimize(objective, np.zeros(3), jac=True, method='L-BFGS-B', bounds=[(-1, 1)] * 3)
    if not res.success or not np.isfinite(res.fun):
        raise ValueError(f'Calibration failed: {res.message}')
    return res.x


def split_records(records, tune, test):
    tr = [r for r in records if r['season'] == tune]
    te = [r for r in records if r['season'] == test]
    if not tr or not te:
        raise ValueError('Both training and test seasons are required')
    if max(r['date'] for r in tr) >= min(r['date'] for r in te):
        raise ValueError('Training must finish strictly before test starts')
    return tr, te


def build_records(df, seasons):
    records, skipped = [], []
    for league, group in df.groupby('league'):
        group = group.sort_values('date').reset_index(drop=True)
        target = group[group.season.isin(seasons)].dropna(subset=['fthg', 'ftag'])
        for week in target.date.dt.to_period('W').unique():
            past = group[group.date < week.start_time]
            if len(past) < 200:
                skipped.append({'league': league, 'week': str(week), 'reason': 'history < 200'})
                continue
            dc = DixonColes().fit(past, week.start_time)
            for r in target[target.date.dt.to_period('W') == week].itertuples():
                mp = market_probs(r.odd_h, r.odd_d, r.odd_a, r.odd_o25, r.odd_u25)
                # Same complete quote cohort for every model and both metrics.
                if mp is None or 'over' not in mp:
                    skipped.append({'league': league, 'date': str(r.date.date()), 'reason': 'missing market odds'})
                    continue
                h, a = int(r.fthg), int(r.ftag)
                if min(h, a) < 0 or max(h, a) > 10:
                    skipped.append({'league': league, 'date': str(r.date.date()), 'reason': 'score outside grid'})
                    continue
                lm, mm = dc.lambdas(r.home, r.away)
                lb, mb = implied_lambdas(mp, dc.rho)
                l, m = blend(lm, mm, lb, mb, 0.9)
                records.append({'season': r.season, 'date': str(r.date.date()), 'league': league,
                                'home': r.home, 'away': r.away, 'h': h, 'a': a,
                                'base': score_matrix(l, m, dc.rho),
                                'model': score_matrix(lm, mm, dc.rho),
                                'market': [*mp['1x2'], mp['over'], 1-mp['over']],
                                'odds': [getattr(r, k) for k in ODDS]})
        print(f'{league}: processed', flush=True)
    return records, skipped


def metrics(records, probabilities):
    p = np.clip(np.asarray(probabilities), 1e-12, 1-1e-12)
    y = np.array([[r['h'] > r['a'], r['h'] == r['a'], r['h'] < r['a'],
                   r['h']+r['a'] > 2, r['h']+r['a'] <= 2] for r in records], float)
    losses = -np.log(p[:, :3][np.arange(len(y)), y[:, :3].argmax(axis=1)])
    picks = []
    for r, pp, yy in zip(records, p, y):
        eligible = [k for k, q in enumerate(r['odds']) if q is not None and np.isfinite(q) and q >= 1.2]
        if eligible:
            k = max(eligible, key=lambda k: pp[k])
            picks.append((float(pp[k]), bool(yy[k]), float(r['odds'][k])))
    return {'n': len(records), 'logloss_1x2': float(losses.mean()),
            'brier_1x2': float(((p[:, :3]-y[:, :3])**2).sum(axis=1).mean()),
            'logloss_over25': float(-(y[:,3]*np.log(p[:,3])+(1-y[:,3])*np.log(1-p[:,3])).mean()),
            'real_odds_picks': len(picks), 'coverage': len(picks)/len(records),
            'pick_hit_rate': float(np.mean([w for _, w, _ in picks])) if picks else None,
            'pick_expected': float(np.mean([v for v, _, _ in picks])) if picks else None,
            'roi_real_odds': float(np.mean([q*w-1 for _,w,q in picks])) if picks else None}, losses


def daily_interval(records, delta, seed=42, repeats=2000):
    # Paired bootstrap by day; residual dependencies across days remain possible.
    days = sorted({r['date'] for r in records})
    blocks = [np.array([v for r,v in zip(records, delta) if r['date']==d]) for d in days]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(repeats):
        selected = [blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]
        means.append(sum(x.sum() for x in selected)/sum(len(x) for x in selected))
    return [float(x) for x in np.quantile(means,[.025,.975])]


def evaluate(records, tune, test):
    tr, te = split_records(records, tune, test)
    theta = fit_calibration([r['base'] for r in tr], [(r['h'],r['a']) for r in tr])
    def probs(matrix):
        mk = goal_markets(matrix)
        return [mk[k] for k in KEYS]
    variants = {'market': [r['market'] for r in te],
                'model': [probs(r['model']) for r in te],
                'base90': [probs(r['base']) for r in te],
                'calibrated90': [probs(apply_calibration(r['base'],theta)) for r in te]}
    results, losses = {}, {}
    for name, pp in variants.items():
        results[name], losses[name] = metrics(te, pp)
    comparisons = {}
    for baseline in ('base90','market'):
        delta = losses['calibrated90']-losses[baseline]
        comparisons[baseline] = {'delta_logloss': float(delta.mean()),
                                'day_bootstrap_95': daily_interval(te,delta)}
    return {'tune': tune, 'test': test, 'train_n': len(tr), 'theta': theta.tolist(),
            'results': results, 'calibrated_minus_baseline': comparisons,
            'limits': ['No historical news or injury snapshots; those effects are not tested.',
                       'Historical odds have no verified execution timestamp.',
                       'Only 1X2 and O/U2.5 selections with actual stored bookmaker odds; no estimated payouts.',
                       'No automatic promotion; inspect untouched future data and coverage.',
                       'Calibration is a new joint-score variant, not the old independent-market calibration.']}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', required=True)
    ap.add_argument('--tune', default='2425')
    ap.add_argument('--test', default='2526')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    db = Path(args.db).resolve(strict=True)
    out = Path(args.out).resolve()
    if out == db or out == ROOT / 'engine/seriea.db' or (ROOT/'docs') in out.parents:
        raise ValueError('Output must be separate from database and app')
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as con:
        df = pd.read_sql('SELECT * FROM matches',con,parse_dates=['date'])
    df['season'] = df.season.astype(str)
    records, skipped = build_records(df,{args.tune,args.test})
    result = evaluate(records,args.tune,args.test)
    result['skipped'] = skipped
    result['db_sha256'] = hashlib.sha256(db.read_bytes()).hexdigest()
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result['results'],indent=2))


if __name__ == '__main__':
    main()
