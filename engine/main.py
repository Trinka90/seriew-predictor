"""Pronostici Serie A.

  python main.py update            # scarica storico, calendario+quote, infortuni, allenatori, notizie
  python main.py predict           # pronostici prossimi 10 giorni (+ CSV in output/)
  python main.py classifica        # classifica + rating attacco/difesa del modello
  python main.py news Inter        # ultime notizie classificate per una squadra
  python main.py backtest --stagione 2526
"""
import argparse
import datetime as dt

import config as C
import context as X
import data_sources as D
from db import connect


def cmd_update(a):
    con = connect()
    print("Storico + quote (football-data.co.uk)...")
    D.update_history([C.CURRENT_SEASON] if a.solo_corrente else C.SEASONS, con)
    print("Calendario...")
    D.update_fixtures(con)
    if C.API_FOOTBALL_KEY:
        print("API-Football...")
        try:
            D.update_injuries(con)
            if not a.no_coach:
                D.update_coaches(con)
        except Exception as e:
            print(f"  [!] API-Football: {e}")
    else:
        print("API-Football: chiave non impostata (infortuni solo da manual.json)")
    if not a.no_news:
        print("Notizie...")
        D.update_news(con)


def cmd_predict(a):
    import predict
    predict.run(days=a.giorni)


def cmd_classifica(a):
    from model import DixonColes
    con = connect()
    df = X.load_matches(con)
    t = X.standings(df)
    r = DixonColes().fit(df, dt.date.today()).ratings()
    t = t.join(r.round(3), how="left")
    t["forma"] = [X.form(df, tm) for tm in t.index]
    print(t[["pos", "pg", "pt", "gf", "gs", "dr", "forma", "attacco", "difesa", "forza"]].to_string())


def cmd_news(a):
    con = connect()
    teams = [a.squadra] if a.squadra else [r[0] for r in con.execute("SELECT DISTINCT team FROM news ORDER BY team")]
    for t in teams:
        n = X.news_for(con, t)
        print(f"\n## {t} ({len(n)})")
        for r in n.head(15).itertuples():
            print(f"  {r.published} [{r.tags or '-'}] {r.title[:110]}")


def cmd_backtest(a):
    import backtest
    backtest.run(a.stagione, a.edge)


def main():
    p = argparse.ArgumentParser(description="Pronostici Serie A")
    s = p.add_subparsers(dest="cmd", required=True)
    u = s.add_parser("update")
    u.add_argument("--no-news", action="store_true")
    u.add_argument("--no-coach", action="store_true", help="risparmia ~20 chiamate API")
    u.add_argument("--solo-corrente", action="store_true", help="scarica solo la stagione in corso")
    u.set_defaults(f=cmd_update)
    pr = s.add_parser("predict")
    pr.add_argument("--giorni", type=int, default=10)
    pr.set_defaults(f=cmd_predict)
    s.add_parser("classifica").set_defaults(f=cmd_classifica)
    n = s.add_parser("news")
    n.add_argument("squadra", nargs="?")
    n.set_defaults(f=cmd_news)
    b = s.add_parser("backtest")
    b.add_argument("--stagione", default="2526")
    b.add_argument("--edge", type=float, default=C.VALUE_EDGE)
    b.set_defaults(f=cmd_backtest)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
