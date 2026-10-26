"""Normalizzazione nomi squadra tra fonti diverse + alias per le notizie."""
import difflib
import re
import unicodedata

# Query Google News per squadre con nome ambiguo
NEWS_QUERY = {
    "Milan": "Milan calcio", "Roma": "AS Roma", "Verona": "Hellas Verona",
    "Como": "Como 1907", "Parma": "Parma calcio", "Pisa": "Pisa calcio",
    "Lecce": "Lecce calcio", "Torino": "Torino FC", "Napoli": "SSC Napoli",
    "Venezia": "Venezia FC", "Palermo": "Palermo calcio", "Genoa": "Genoa CFC",
    "Inter": "Inter calcio", "Bologna": "Bologna FC", "Cagliari": "Cagliari calcio",
}

# Parole che identificano la squadra in un titolo
ALIASES = {
    "Inter": ["inter", "nerazzurri"],
    "Milan": ["milan", "rossoneri"],
    "Juventus": ["juventus", "juve", "bianconeri"],
    "Napoli": ["napoli", "partenopei"],
    "Roma": ["roma", "giallorossi"],
    "Lazio": ["lazio", "biancocelesti"],
    "Atalanta": ["atalanta", "nerazzurri bergamaschi"],
    "Fiorentina": ["fiorentina", "viola"],
    "Bologna": ["bologna", "rossoblù"],
    "Torino": ["torino", "granata"],
    "Verona": ["verona", "hellas", "scaligeri"],
    "Genoa": ["genoa", "grifone"],
    "Udinese": ["udinese", "friulani"],
    "Sassuolo": ["sassuolo", "neroverdi"],
    "Cagliari": ["cagliari"],
    "Lecce": ["lecce", "giallorossi salentini"],
    "Como": ["como"],
    "Parma": ["parma", "crociati"],
    "Cremonese": ["cremonese", "grigiorossi"],
    "Pisa": ["pisa"],
}


NOISE = {"ac", "as", "ssc", "fc", "us", "cfc", "bc", "afc", "cf", "sc", "rc", "rcd", "sv", "calcio", "hellas", "losc",
         "estac", "ogc", "fsv"}

# nomi che non si riconoscono da soli (fonte esterna -> nome football-data, già normalizzati)
SYNONYMS = {
    "internazionale": "inter", "inter milan": "inter", "fc internazionale milano": "inter",
    "spurs": "tottenham", "tottenham hotspur": "tottenham", "wolverhampton wanderers": "wolves",
    "queens park rangers": "qpr", "manchester united": "man united", "manchester city": "man city",
    "nottingham forest": "nott m forest", "sheffield united": "sheffield united",
    "sheffield wednesday": "sheffield weds", "west bromwich albion": "west brom",
    "paris saint germain": "paris sg", "psg": "paris sg", "stade rennais": "rennes", "havre athletic club": "le havre",
    "athletic club": "ath bilbao", "athletic bilbao": "ath bilbao", "atletico de madrid": "ath madrid",
    "atletico madrid": "ath madrid", "espanyol de barcelona": "espanol", "espanyol": "espanol",
    "r racing club": "santander", "racing club": "santander", "racing santander": "santander",
    "deportivo": "la coruna", "deportivo la coruna": "la coruna", "real sociedad": "sociedad",
    "celta de vigo": "celta", "celta vigo": "celta", "rayo vallecano": "vallecano",
    "borussia monchengladbach": "m gladbach", "monchengladbach": "m gladbach", "1 koln": "koln",
    "sporting cp": "sp lisbon", "sporting": "sp lisbon", "vitoria": "guimaraes", "vitoria guimaraes": "guimaraes",
    "sporting braga": "sp braga", "braga": "sp braga", "fc porto": "porto",
    "n e c nijmegen": "nijmegen", "nec": "nijmegen", "az": "az alkmaar", "psv": "psv eindhoven",
    "go ahead eagles": "go ahead eagles", "sparta rotterdam": "sparta rotterdam",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [t for t in s.split() if t not in NOISE and not t.isdigit()]
    return " ".join(toks)


def _prefix_cover(short: str, long: str) -> bool:
    """Ogni parola di `short` è l'inizio di una parola di `long` (es. 'west brom' ~ 'west bromwich albion')."""
    st, lt = short.split(), long.split()
    return bool(st) and all(any(l.startswith(w) for l in lt) for w in st)


def canonical(name: str, known: list[str]) -> str:
    """Mappa un nome di un'altra fonte sul nome usato nel DB (football-data)."""
    n = _norm(name)
    n = SYNONYMS.get(n, n)
    normed = [_norm(k) for k in known]
    if n in normed:
        return known[normed.index(n)]
    cands = [(len(k), known[i]) for i, k in enumerate(normed) if _prefix_cover(k, n) or _prefix_cover(n, k)]
    if len(cands) == 1 or (cands and sorted(cands)[-1][0] > sorted(cands)[-2][0]):
        return max(cands)[1]
    m = difflib.get_close_matches(n, normed, n=1, cutoff=0.8)
    return known[normed.index(m[0])] if m else name


def aliases(team: str) -> list[str]:
    return ALIASES.get(team, [_norm(team)])


def news_query(team: str) -> str:
    return NEWS_QUERY.get(team, f"{team} calcio")


def teams_in_text(text: str, teams: list[str]) -> list[str]:
    t = text.lower()
    return [tm for tm in teams
            if any(re.search(rf"\b{re.escape(a)}\b", t) for a in aliases(tm))]
