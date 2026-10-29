import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "seriea.db"
MANUAL_PATH = BASE_DIR / "manual.json"
OUT_DIR = BASE_DIR / "output"

# --- Storico + quote (gratis, senza chiave) ---------------------------------
SEASONS = ["2223", "2324", "2425", "2526", "2627"]
CURRENT_SEASON = "2627"
HIST_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
FIXTURES_FALLBACK_URL = "https://fixturedownload.com/feed/json/{slug}-2026"

# Campionati: codice football-data.co.uk -> info. "fd" = slug fixturedownload (calendario completo)
LEAGUES = {
    "I1":  {"nome": "Serie A",          "flag": "🇮🇹", "giornate": 38, "fd": "serie-a", "espn": "ita.1"},
    "I2":  {"nome": "Serie B",          "flag": "🇮🇹", "giornate": 38, "fd": "serie-b", "espn": "ita.2"},
    "E0":  {"nome": "Premier League",   "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 38, "fd": "epl", "espn": "eng.1"},
    "E1":  {"nome": "Championship",     "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 46, "fd": "championship", "espn": "eng.2"},
    "SP1": {"nome": "La Liga",          "flag": "🇪🇸", "giornate": 38, "fd": "la-liga", "espn": "esp.1"},
    "SP2": {"nome": "Segunda División", "flag": "🇪🇸", "giornate": 42, "fd": None, "espn": "esp.2"},
    "D1":  {"nome": "Bundesliga",       "flag": "🇩🇪", "giornate": 34, "fd": "bundesliga", "espn": "ger.1"},
    "D2":  {"nome": "2. Bundesliga",    "flag": "🇩🇪", "giornate": 34, "fd": "2-bundesliga", "espn": "ger.2"},
    "F1":  {"nome": "Ligue 1",          "flag": "🇫🇷", "giornate": 34, "fd": "ligue-1", "espn": "fra.1"},
    "F2":  {"nome": "Ligue 2",          "flag": "🇫🇷", "giornate": 34, "fd": None, "espn": "fra.2"},
    "N1":  {"nome": "Eredivisie",       "flag": "🇳🇱", "giornate": 34, "fd": "eredivisie", "espn": "ned.1"},
    "P1":  {"nome": "Primeira Liga",    "flag": "🇵🇹", "giornate": 34, "fd": "primeira-liga", "espn": "por.1"},
    "B1":  {"nome": "Pro League",       "flag": "🇧🇪", "giornate": 30, "fd": None, "espn": "bel.1"},
    "T1":  {"nome": "Süper Lig",        "flag": "🇹🇷", "giornate": 38, "fd": None, "espn": "tur.1"},
    "SC0": {"nome": "Premiership",      "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "giornate": 38, "fd": None, "espn": "sco.1"},
    "E2":  {"nome": "League One",       "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 46, "fd": None, "espn": "eng.3"},
    "E3":  {"nome": "League Two",       "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 46, "fd": None, "espn": "eng.4"},
    "EC":  {"nome": "National League",  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 46, "fd": None, "espn": "eng.5"},
    "SC1": {"nome": "Championship scozzese", "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "giornate": 36, "fd": None, "espn": "sco.2"},
    "SC2": {"nome": "League One scozzese",  "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "giornate": 36, "fd": None, "espn": "sco.3"},
    "SC3": {"nome": "League Two scozzese",  "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "giornate": 36, "fd": None, "espn": "sco.4"},
    "G1":  {"nome": "Super League Grecia",  "flag": "🇬🇷", "giornate": 26, "fd": None, "espn": "gre.1"},
}
# Quote mostrate e usate per schedina/value: un solo bookmaker (la schedina si gioca su uno).
# La media dei bookmaker resta solo per stimare le probabilità (è il dato più preciso).
BOOKMAKER = "Bet365"
# Quote ESPN (DraftKings) aggiornate fino al fischio d'inizio: servono SOLO alle probabilità, insieme alla media
# dei bookmaker di football-data (le quote vicine alla partita sono più precise: backtest_semaforo.md, punto 4).
ESPN_SOCCER = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
ESPN_WEIGHT = 0.5      # peso delle quote ESPN quando ci sono anche quelle di football-data
NEWS_HORIZON_DAYS = 4   # notizie solo per squadre che giocano nei prossimi N giorni (Serie A sempre)
DOCS_DATA = BASE_DIR.parent / "docs" / "data"   # JSON letti dall'app

# --- API-Football (opzionale: infortuni, squalifiche, allenatori, calendario)
# Chiave gratuita su https://dashboard.api-football.com  -> set API_FOOTBALL_KEY=...
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = 135          # Serie A
API_FOOTBALL_SEASON = 2026         # stagione 2026/27

# --- Modello -----------------------------------------------------------------
# Valori scelti con experiment.py (stagione 2025/26, 5214 partite, 15 campionati - vedi experiments/result.md)
XI = 0.003             # decadimento temporale (per giorno): peso dimezzato in ~8 mesi
SOT_WEIGHT = 0.5       # metà del bersaglio dai tiri in porta (più stabili dei gol): LogLoss 1.0187 -> 1.0148
MARKET_WEIGHT = 0.9    # fusione con le quote quando disponibili: LogLoss -> 0.9997 (bookmaker 0.9980)
NEWS_WITH_MARKET = 0.5 # con le quote, le notizie pesano la metà (in parte già prezzate dal mercato)
TRAIN_DAYS = 3 * 365   # finestra di training
MAX_GOALS = 10
VALUE_EDGE = 0.05      # segnala "value" se prob*quota - 1 > 5%

# --- Notizie -------------------------------------------------------------------
NEWS_DAYS = 7
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=it&gl=IT&ceid=IT:it"
EXTRA_RSS = [
    "https://www.gazzetta.it/rss/calcio.xml",
    "https://www.corrieredellosport.it/rss/calcio/serie-a",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (SerieA-Predictor)"}
