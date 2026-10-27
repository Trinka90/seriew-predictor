"""Pronostici per le prossime partite: modello + contesto + confronto con le quote."""
import datetime as dt

import numpy as np
import pandas as pd

import config as C
import context as X
from db import connect
from markets import count_markets, goal_markets, top_scores
from model import DixonColes, RateModel, blend, ht_ft, implied_lambdas, market_probs, score_matrix
from news_ai import TURNOVER

SHOW = ["1", "X", "2", "1X", "X2", "Over 1.5", "Over 2.5", "Over 3.5", "GG", "NG",
        "Multigol 2-4", "1T Over 0.5", "1 + Over 1.5", "2 + Over 1.5", "GG + Over 2.5"]


def build_models(df, ref):
    df = df.copy()
    df["hcard"], df["acard"] = df.hy + df.hr.fillna(0), df.ay + df.ar.fillna(0)
    dc = DixonColes().fit(df, ref)
    corners = RateModel("hc", "ac").fit(df, ref)
    cards = RateModel("hcard", "acard").fit(df, ref)
    recent = df[(pd.Timestamp(ref) - df.date).dt.days <= C.TRAIN_DAYS].dropna(subset=["hthg", "htag"])
    r_h = recent.hthg.sum() / max(recent.fthg.sum(), 1)
    r_a = recent.htag.sum() / max(recent.ftag.sum(), 1)
    return dc, corners, cards, r_h, r_a


def predict_match(con, df, models, table, manual, home, away, rounds=38, odds=None):
    dc, corners, cards, r_h, r_a = models
    lam0, mu0 = dc.lambdas(home, away)
    info = {"home": home, "away": away, "lam_base": lam0, "mu_base": mu0}
    ev = X.match_event(con, home, away) or {}

    mkey = f"{home}|{away}"
    ab_h, ab_a = X.absences(con, home, manual, mkey), X.absences(con, away, manual, mkey)
    att_h, leak_h = X.absence_multipliers(ab_h)
    att_a, leak_a = X.absence_multipliers(ab_a)
    mot_h, mot_h_txt = X.motivation(table, home, rounds)
    mot_a, mot_a_txt = X.motivation(table, away, rounds)
    man_att_h, man_def_h = X.manual_adjust(manual, home)
    man_att_a, man_def_a = X.manual_adjust(manual, away)

    lam = lam0 * att_h * leak_a * mot_h * man_att_h * man_def_a
    mu = mu0 * att_a * leak_h * mot_a * man_att_a * man_def_h

    # eventi letti dalle notizie: turnover, porte chiuse, campo neutro
    eff = []
    for side, team in (("casa", home), ("ospite", away)):
        to = (ev.get(side) or {}).get("turnover", "nessuno")
        a_, d_ = TURNOVER.get(to, (0, 0))
        if a_ or d_:
            eff.append(f"turnover {to} {team}")
            if side == "casa":
                lam, mu = lam * (1 - a_), mu * (1 + d_)
            else:
                mu, lam = mu * (1 - a_), lam * (1 + d_)
    if ev.get("campo_neutro") or ev.get("porte_chiuse"):
        k = 0.5 if ev.get("campo_neutro") else 0.25   # quota di vantaggio casa che si perde
        lam, mu = lam * np.exp(-dc.home * k), mu * np.exp(dc.home * k)
        eff.append("campo neutro" if ev.get("campo_neutro") else "porte chiuse")
    info["lam_news"], info["mu_news"] = lam, mu

    # probabilità del solo modello (servono per cercare quote convenienti)
    info["model_markets"] = goal_markets(score_matrix(lam, mu, dc.rho))

    # fusione con le quote di mercato (se disponibili)
    info["mercato"] = False
    info["book_markets"] = None
    if odds is not None and C.MARKET_WEIGHT > 0:
        mp = market_probs(*odds)
        if mp:
            lb, mb = implied_lambdas(mp, dc.rho)
            info["book_markets"] = goal_markets(score_matrix(lb, mb, dc.rho))   # probabilità del mercato
            # base: modello puro (con motivazione e correzioni manuali) fuso con il mercato
            lam_b0 = lam0 * mot_h * man_att_h * man_def_a
            mu_b0 = mu0 * mot_a * man_att_a * man_def_h
            lbl, mbl = blend(lam_b0, mu_b0, lb, mb, C.MARKET_WEIGHT)
            # più l'effetto di notizie/assenti, a intensità ridotta
            k = C.NEWS_WITH_MARKET
            lam = lbl * (lam / lam_b0) ** k
            mu = mbl * (mu / mu_b0) ** k
            info["mercato"] = True
    info["eventi"], info["effetti"] = ev, eff

    M = score_matrix(lam, mu, dc.rho)
    mk = goal_markets(M)
    mk.update(ht_ft(lam, mu, r_h, r_a))
    mk.update(count_markets("Angoli", corners, *corners.expect(home, away), [7.5, 8.5, 9.5, 10.5, 11.5]))
    mk.update(count_markets("Cartellini", cards, *cards.expect(home, away), [2.5, 3.5, 4.5, 5.5]))

    news_h, news_a = X.news_for(con, home), X.news_for(con, away)
    info.update(lam=lam, mu=mu, markets=mk, scores=top_scores(M),
                abs_h=ab_h, abs_a=ab_a, mot_h=mot_h_txt, mot_a=mot_a_txt,
                form_h=X.form(df, home), form_a=X.form(df, away),
                coach_h=X.coach_notes(con, home, news_h), coach_a=X.coach_notes(con, away, news_a),
                news_h=news_h, news_a=news_a)
    return info


def _pos(table, t):
    if table.empty or t not in table.index:
        return "n.d."
    r = table.loc[t]
    return f"{int(r.pos)}° {int(r.pt)}pt ({int(r.gf)}-{int(r.gs)})"


def _value(p, odd):
    if odd is None or (isinstance(odd, float) and np.isnan(odd)) or not odd:
        return ""
    edge = p * odd - 1
    return f"  quota {odd:.2f} edge {edge:+.0%}" + ("  <<< VALUE" if edge > C.VALUE_EDGE else "")


def print_match(fx, info, table):
    h, a, mk = info["home"], info["away"], info["markets"]
    print("\n" + "=" * 78)
    print(f"{h} - {a}   {fx.date:%a %d/%m} {fx.time or ''}")
    print(f"Classifica: {_pos(table, h)} | {_pos(table, a)}    Forma: {info['form_h'] or '-'} | {info['form_a'] or '-'}")
    print(f"Gol attesi: {info['lam']:.2f} - {info['mu']:.2f}   (base modello {info['lam_base']:.2f} - {info['mu_base']:.2f})")
    for side, team in (("h", h), ("a", a)):
        ab = info[f"abs_{side}"]
        extra = [x for x in info[f"coach_{side}"] + [info[f"mot_{side}"]] if x]
        if ab:
            print(f"  Assenti {team}: " + ", ".join(f"{x['player']} ({x.get('importanza') or x['reason']})" for x in ab[:8]))
        if extra:
            print(f"  {team}: " + " | ".join(extra))
        nw = info[f"news_{side}"]
        hot = nw[nw.tags.str.len() > 0].head(4)
        for r in hot.itertuples():
            print(f"    [{r.tags}] {r.title[:95]}")

    odds = {"1": fx.odd_h, "X": fx.odd_d, "2": fx.odd_a, "Over 2.5": fx.odd_o25, "Under 2.5": fx.odd_u25}
    print("-" * 78)
    for k in SHOW + ["Under 2.5"]:
        p = mk[k]
        print(f"  {k:<16} {p:6.1%}  quota equa {1 / p:5.2f}{_value(p, odds.get(k))}")
    print("  Parziale/Finale: " + "  ".join(f"{k[3:]} {mk[k]:.0%}" for k in sorted(mk) if k.startswith("PF ")))
    print("  Risultati esatti: " + "  ".join(f"{s} {p:.1%}" for p, s in info["scores"]))
    print(f"  Angoli attesi {mk['Angoli attesi']:.1f}: O8.5 {mk['Angoli Over 8.5']:.0%}  O9.5 {mk['Angoli Over 9.5']:.0%}  O10.5 {mk['Angoli Over 10.5']:.0%}")
    print(f"  Cartellini attesi {mk['Cartellini attesi']:.1f}: O3.5 {mk['Cartellini Over 3.5']:.0%}  O4.5 {mk['Cartellini Over 4.5']:.0%}")

    best = sorted(((p, k) for k, p in mk.items()
                   if 0.55 <= p <= 0.97 and "attesi" not in k and k not in ("12",)), reverse=True)
    print("  Più probabili (>55%): " + ", ".join(f"{k} {p:.0%}" for p, k in best[:6]))


def run(days=10, export=True, quiet=False, ref=None, league="I1"):
    con = connect()
    df = X.load_matches(con)
    df = df[df.league == league].reset_index(drop=True)
    if df.empty:
        raise SystemExit("DB vuoto: lancia prima `python main.py update`")
    ref = pd.Timestamp(ref or dt.date.today())
    fx = pd.read_sql("SELECT * FROM fixtures WHERE league=?", con, params=(league,), parse_dates=["date"])
    fx = fx[(fx.date >= ref) & (fx.date <= ref + pd.Timedelta(days=days))].sort_values(["date", "time"])
    if fx.empty:
        print("Nessuna partita in programma nel periodo (fixtures.csv esce di solito a metà settimana).")
        return pd.DataFrame()

    models = build_models(df, ref)
    table = X.standings(df)
    manual = X.load_manual()
    rows = []
    for f in fx.itertuples():
        info = predict_match(con, df, models, table, manual, f.home, f.away, C.LEAGUES[league]["giornate"],
                             odds=(f.odd_h, f.odd_d, f.odd_a, f.odd_o25, f.odd_u25))
        if not quiet:
            print_match(f, info, table)
        rows.append({"data": f.date.date(), "casa": f.home, "ospite": f.away,
                     "gol_casa": round(info["lam"], 2), "gol_ospite": round(info["mu"], 2),
                     **{k: round(v, 4) for k, v in info["markets"].items()}})
    out = pd.DataFrame(rows)
    if export:
        C.OUT_DIR.mkdir(exist_ok=True)
        path = C.OUT_DIR / f"pronostici_{league}_{ref:%Y%m%d}.csv"
        out.to_csv(path, index=False, sep=";", decimal=",")
        print(f"\nEsportato: {path}")
    return out
