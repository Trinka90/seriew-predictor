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
    "I1":  {"nome": "Serie A",          "flag": "🇮🇹", "giornate": 38, "fd": "serie-a"},
    "I2":  {"nome": "Serie B",          "flag": "🇮🇹", "giornate": 38, "fd": "serie-b"},
    "E0":  {"nome": "Premier League",   "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 38, "fd": "epl"},
    "E1":  {"nome": "Championship",     "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "giornate": 46, "fd": "championship"},
    "SP1": {"nome": "La Liga",          "flag": "🇪🇸", "giornate": 38, "fd": "la-liga"},
    "SP2": {"nome": "Segunda División", "flag": "🇪🇸", "giornate": 42, "fd": None},
    "D1":  {"nome": "Bundesliga",       "flag": "🇩🇪", "giornate": 34, "fd": "bundesliga"},
    "D2":  {"nome": "2. Bundesliga",    "flag": "🇩🇪", "giornate": 34, "fd": "2-bundesliga"},
    "F1":  {"nome": "Ligue 1",          "flag": "🇫🇷", "giornate": 34, "fd": "ligue-1"},
    "F2":  {"nome": "Ligue 2",          "flag": "🇫🇷", "giornate": 34, "fd": None},
    "N1":  {"nome": "Eredivisie",       "flag": "🇳🇱", "giornate": 34, "fd": "eredivisie"},
    "P1":  {"nome": "Primeira Liga",    "flag": "🇵🇹", "giornate": 34, "fd": "primeira-liga"},
    "B1":  {"nome": "Pro League",       "flag": "🇧🇪", "giornate": 30, "fd": None},
    "T1":  {"nome": "Süper Lig",        "flag": "🇹🇷", "giornate": 38, "fd": None},
    "SC0": {"nome": "Premiership",      "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "giornate": 38, "fd": None},
}
NEWS_HORIZON_DAYS = 4   # notizie solo per squadre che giocano nei prossimi N giorni (Serie A sempre)
DOCS_DATA = BASE_DIR.parent / "docs" / "data"   # JSON letti dall'app

# --- API-Football (opzionale: infortuni, squalifiche, allenatori, calendario)
# Chiave gratuita su https://dashboard.api-football.com  -> set API_FOOTBALL_KEY=...
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = 135          # Serie A
API_FOOTBALL_SEASON = 2026         # stagione 2026/27

# --- Modello -----------------------------------------------------------------
XI = 0.0019            # decadimento temporale (per giorno): ~ dimezza peso in 1 anno
SOT_WEIGHT = 0.0       # quota tiri in porta nel bersaglio del modello (scelta dall'esperimento)
MARKET_WEIGHT = 0.0    # peso delle quote di mercato nella fusione (scelto dall'esperimento)
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
