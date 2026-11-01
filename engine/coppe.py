"""Coppe: Champions League, Europa League, Conference League, Coppa Italia, FA Cup.

Il modello del calcio confronta solo squadre dello stesso campionato, quindi nelle coppe le probabilità
vengono dalle quote del bookmaker (ESPN/DraftKings, aggiornate fino al fischio): nei test sono la fonte più
precisa (analisi_sport.json: favorito >=70% -> vince 75-78%, come indicato). Dalle quote si ricavano i gol
attesi e quindi tutti i mercati (Over, GG, Multigol...). Assenti e turnover letti dall'IA correggono i gol
attesi a metà intensità, come nei campionati quando ci sono le quote.
"""
import datetime as dt
import time

import pandas as pd
import requests

import config as C
import context as X
from data_sources import _am, _known
from markets import goal_markets, top_scores
from model import ht_ft, implied_lambdas, market_probs, score_matrix
from teams import canonical

RHO = -0.08          # correzione Dixon-Coles tipica (risultati bassi)
HT = 0.44            # quota di gol nel primo tempo (media dei campionati)
DAYS = (-3, 9)       # giorni scaricati: indietro (risultati per la pagella) e avanti (in programma)


def _domestic(name, known_by_lg):
    """Nome ESPN -> nome usato nei campionati (serve per notizie, assenti e forma). Altrimenti il nome ESPN."""
    for lg, known in known_by_lg.items():
        c = canonical(name, known)
        if c in known:
            return c, lg
    return name, None


def update(con, budget_s=180):
    """Scarica le partite di coppa da ESPN: in programma -> calendario, finite -> risultati (pagella)."""
    stop = time.time() + budget_s
    known_by_lg = {lg: _known(con, lg) for lg in C.LEAGUES}
    known_by_lg = {k: v for k, v in known_by_lg.items() if v}
    today = dt.date.today()
    now = dt.datetime.now().isoformat(timespec="minutes")
    n = nq = 0
    for code, cfg in C.COPPE.items():
        for dd in range(DAYS[0], DAYS[1] + 1):
            if time.time() > stop:
                break
            day = today + dt.timedelta(days=dd)
            try:
                r = requests.get(C.ESPN_SOCCER.format(slug=cfg["espn"]), params={"dates": f"{day:%Y%m%d}"},
                                 headers=C.HEADERS, timeout=20)
                evs = r.json().get("events", []) if r.ok else []
            except Exception as e:
                print(f"  [!] coppa {code} {day}: {e}")
                continue
            for ev in evs:
                try:
                    c = ev["competitions"][0]
                    cs = {x.get("homeAway"): x for x in c.get("competitors", [])}
                    h, lg_h = _domestic(cs["home"]["team"]["displayName"], known_by_lg)
                    a, lg_a = _domestic(cs["away"]["team"]["displayName"], known_by_lg)
                    ts = pd.Timestamp(c["date"]).tz_convert("Europe/Rome")
                    st = (c.get("status") or {}).get("type") or {}
                    o = next((x for x in c.get("odds") or [] if isinstance(x, dict)), {})
                    ml = o.get("moneyline") or {}
                    g = lambda side, w="close": _am(((ml.get(side) or {}).get(w) or {}).get("odds"))
                    tot = o.get("total") or {}
                    ov, un = (tot.get("over") or {}).get("close") or {}, (tot.get("under") or {}).get("close") or {}
                    o25 = u25 = None
                    if str(ov.get("line", "")).endswith("2.5") and str(un.get("line", "")).endswith("2.5"):
                        o25, u25 = _am(ov.get("odds")), _am(un.get("odds"))
                    hs = as_ = None
                    if st.get("completed"):
                        nm = (st.get("name") or "") + (st.get("detail") or "")
                        if any(x in nm.upper() for x in ("AET", "PEN", "EXTRA", "SHOOTOUT")):
                            hs = as_ = -1        # finita ai supplementari/rigori: l'1X2 vale sui 90', non verificabile
                        else:
                            hs, as_ = int(float(cs["home"]["score"])), int(float(cs["away"]["score"]))
                    fase = ((c.get("series") or {}).get("summary") or (ev.get("season") or {}).get("slug") or "")
                    row = (code, ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M"), h, a, st.get("state"), hs, as_,
                           g("home"), g("draw"), g("away"), o25, u25, g("home", "open"), g("draw", "open"),
                           g("away", "open"), (o.get("provider") or {}).get("name"), now)
                    con.execute("INSERT OR REPLACE INTO espn_soccer VALUES(" + ",".join("?" * len(row)) + ")", row)
                    n += 1
                    if st.get("state") == "pre" and ts.date() >= today:
                        con.execute("INSERT OR REPLACE INTO fixtures(league,date,time,home,away) VALUES(?,?,?,?,?)",
                                    (code, ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M"), h, a))
                        nq += bool(row[8])
                except Exception as e:
                    print(f"  [!] coppa {code} evento: {e}")
            time.sleep(0.1)
    con.commit()
    print(f"  coppe: {n} partite lette, {nq} in programma con quote")
    return {"partite": n, "con_quote": nq}


def export(con, df_all, days=10):
    """Pronostici delle partite di coppa in programma che hanno già le quote."""
    from export_json import _abs, _news, _ok, _semaforo
    manual = X.load_manual()
    today = dt.date.today().isoformat()
    horizon = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    rows = pd.read_sql("SELECT * FROM espn_soccer WHERE league IN (%s) AND state='pre' AND date>=? AND date<=?"
                       % ",".join("?" * len(C.COPPE)), con, params=(*C.COPPE, today, horizon))
    teams = set(df_all.home) | set(df_all.away)
    out = []
    for e in rows.itertuples():
        mp = market_probs(e.o_h, e.o_d, e.o_a, e.o_o25, e.o_u25) if all(_ok(x) for x in (e.o_h, e.o_d, e.o_a)) else None
        if not mp:
            continue      # senza quote non c'è un modello affidabile per le coppe: compare quando escono
        lb, mb = implied_lambdas(mp, RHO)
        mkey = f"{e.home}|{e.away}"
        ab_h, ab_a = X.absences(con, e.home, manual, mkey), X.absences(con, e.away, manual, mkey)
        att_h, leak_h = X.absence_multipliers(ab_h)
        att_a, leak_a = X.absence_multipliers(ab_a)
        lam, mu = lb * att_h * leak_a, mb * att_a * leak_h
        ev = X.match_event(con, e.home, e.away) or {}
        from news_ai import TURNOVER
        eff = []
        for side, team in (("casa", e.home), ("ospite", e.away)):
            to = (ev.get(side) or {}).get("turnover", "nessuno")
            a_, d_ = TURNOVER.get(to, (0, 0))
            if a_ or d_:
                eff.append(f"turnover {to} {team}")
                if side == "casa":
                    lam, mu = lam * (1 - a_), mu * (1 + d_)
                else:
                    mu, lam = mu * (1 - a_), lam * (1 + d_)
        k = C.NEWS_WITH_MARKET   # le notizie in parte sono già nelle quote: metà intensità
        lam, mu = lb * (lam / lb) ** k, mb * (mu / mb) ** k
        M = score_matrix(lam, mu, RHO)
        mk = goal_markets(M)
        mk.update(ht_ft(lam, mu, HT, HT))
        info = {"abs_h": ab_h, "abs_a": ab_a, "eventi": ev}
        drift = None
        if all(_ok(x) for x in (e.op_h, e.op_d, e.op_a)):
            po = market_probs(e.op_h, e.op_d, e.op_a)["1x2"]
            d = mp["1x2"] - po
            i = int(abs(d).argmax())
            drift = (["1", "X", "2"][i], float(d[i]))
        quote = {"1": e.o_h, "X": e.o_d, "2": e.o_a}
        if _ok(e.o_o25) and _ok(e.o_u25):
            quote.update({"Over 2.5": e.o_o25, "Under 2.5": e.o_u25})
        quote = {kk: round(float(v), 2) for kk, v in quote.items() if _ok(v)}
        news_h, news_a = X.news_for(con, e.home), X.news_for(con, e.away)
        out.append({
            "sport": "calcio", "lega": e.league, "coppa": True, "data": e.date, "ora": e.time,
            "casa": e.home, "ospite": e.away, "fonte_quote": "quote del bookmaker, aggiornate fino al fischio",
            "gol_casa": lam, "gol_ospite": mu, "gol_casa_base": lb, "gol_ospite_base": mb,
            "gol_casa_notizie": lam, "gol_ospite_notizie": mu,
            "classifica_casa": None, "classifica_ospite": None,
            "forma_casa": X.form(df_all, e.home) if e.home in teams else "",
            "forma_ospite": X.form(df_all, e.away) if e.away in teams else "",
            "quote": quote, "bookmaker": f"{e.book if isinstance(e.book, str) and e.book else 'DraftKings'} (USA)", "value": [], "mercati": mk,
            "quote_stimate": {}, "risultati": [{"r": s, "p": p} for p, s in top_scores(M)],
            "assenti_casa": [_abs(a) for a in ab_h], "assenti_ospite": [_abs(a) for a in ab_a],
            "eventi": ev, "effetti": eff, "con_mercato": True, "note_casa": [], "note_ospite": [],
            "news_casa": _news(news_h), "news_ospite": _news(news_a),
            "semaforo": _semaforo(info, e.home, e.away, None, None, drift),
        })
    return out
