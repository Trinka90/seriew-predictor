"""Sonda OddsPapi: quota del piano, tornei di calcio, quote Sisal di un campionato. Non salva la chiave."""
import json
import os
from pathlib import Path

import requests

K = os.environ.get("ODDSPAPI_KEY", "")
B = "https://api.oddspapi.io/v4"
out = {"chiave_presente": bool(K)}


def get(path, **p):
    r = requests.get(f"{B}/{path}", params={**p, "apiKey": K}, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = r.text[:500]
    return r.status_code, j


def cut(x, n=3, d=0):
    if isinstance(x, dict):
        return {k: cut(v, n, d + 1) for k, v in list(x.items())[:25]}
    if isinstance(x, list):
        return [cut(v, n, d + 1) for v in x[:n]]
    return x


def quota():
    """Solo il consumo: mai salvare chiave o email."""
    st, a = get("account")
    try:
        s = a["subscriptions"][0]
        return {"limite": s.get("request_limit"), "usate": s.get("request_count"), "sisal": "sisal.it" in (s.get("bookmakers") or {})}
    except Exception:
        return {"status": st}


out["account_prima"] = quota()
st, tours = get("tournaments", sportId=10)
out["tornei_status"] = st
if isinstance(tours, list):
    out["n_tornei"] = len(tours)
    want = ["Serie A", "Serie B", "Premier League", "Championship", "LaLiga", "Bundesliga", "2. Bundesliga", "Ligue 1",
            "Ligue 2", "Eredivisie", "Liga Portugal", "Pro League", "Super Lig", "Premiership", "League One", "League Two",
            "National League", "Super League", "Segunda"]
    out["tornei"] = [t for t in tours if any(w.lower() in (t.get("tournamentName") or "").lower() for w in want)
                     and (t.get("categoryName") or "") in ("Italy", "England", "Spain", "Germany", "France", "Netherlands",
                                                            "Portugal", "Belgium", "Turkey", "Turkiye", "Scotland", "Greece")]
    ita = next((t for t in tours if t.get("tournamentName") == "Serie A" and t.get("categoryName") == "Italy"), None)
    eng = next((t for t in tours if t.get("tournamentName") == "Premier League" and t.get("categoryName") == "England"), None)
    ids = ",".join(str(t["tournamentId"]) for t in (ita, eng) if t)
    for bk in ("sisal.it", "sisal-it"):
        st, od = get("odds-by-tournaments", bookmaker=bk, tournamentIds=ids, oddsFormat="decimal")
        out[f"odds_{bk}_status"] = st
        if isinstance(od, list):
            out[f"odds_{bk}_n"] = len(od)
            out[f"odds_{bk}_con_quote"] = sum(1 for f in od if f.get("bookmakerOdds"))
            ex = next((f for f in od if f.get("bookmakerOdds")), od[0] if od else None)
            if ex:
                mk = list((ex.get("bookmakerOdds") or {}).values())
                out[f"odds_{bk}_esempio"] = {k: v for k, v in ex.items() if k != "bookmakerOdds"}
                if mk:
                    out[f"odds_{bk}_mercati"] = {mid: {o: (v.get("players") or {}).get("0", {}).get("price")
                                                       for o, v in (m.get("outcomes") or {}).items()}
                                                 for mid, m in (mk[0].get("markets") or {}).items()}
            break
        out[f"odds_{bk}_risposta"] = cut(od)
st, mk = get("markets")
out["markets_status"] = st
if isinstance(mk, list):
    out["markets_calcio"] = [{"id": m["marketId"], "nome": m.get("marketName"), "h": m.get("handicap"),
                              "out": [(o["outcomeId"], o.get("outcomeName")) for o in m.get("outcomes", [])]}
                             for m in mk if m.get("sportId") == 10 and m.get("period") in (None, "result", "fulltime", "FT")][:120]
out["account_dopo"] = quota()
Path(__file__).with_name("probe_oddspapi.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
print(json.dumps({k: v for k, v in out.items() if k != "markets_calcio"}, indent=1, ensure_ascii=False, default=str)[:4000])
