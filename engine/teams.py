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


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(ac|as|ssc|fc|us|cfc|bc|calcio|1907|1909|1913|1919|hellas)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


SYNONYMS = {"internazionale": "inter", "inter milan": "inter", "fc internazionale milano": "inter",
            "milano": "milan", "juve": "juventus", "verona": "verona", "spal": "spal",
            "atalanta bergamasca": "atalanta", "acf fiorentina": "fiorentina"}


def canonical(name: str, known: list[str]) -> str:
    """Mappa un nome di un'altra fonte sul nome usato nel DB (football-data)."""
    n = _norm(name)
    n = SYNONYMS.get(n, n)
    normed = [_norm(k) for k in known]
    if n in normed:
        return known[normed.index(n)]
    m = difflib.get_close_matches(n, normed, n=1, cutoff=0.75)
    return known[normed.index(m[0])] if m else name


def aliases(team: str) -> list[str]:
    return ALIASES.get(team, [_norm(team)])


def news_query(team: str) -> str:
    return NEWS_QUERY.get(team, f"{team} calcio")


def teams_in_text(text: str, teams: list[str]) -> list[str]:
    t = text.lower()
    return [tm for tm in teams
            if any(re.search(rf"\b{re.escape(a)}\b", t) for a in aliases(tm))]
