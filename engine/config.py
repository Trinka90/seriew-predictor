import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "seriea.db"
MANUAL_PATH = BASE_DIR / "manual.json"
OUT_DIR = BASE_DIR / "output"

# --- Storico + quote (gratis, senza chiave) ---------------------------------
SEASONS = ["2122", "2223", "2324", "2425", "2526", "2627"]
CURRENT_SEASON = "2627"
HIST_URL = "https://www.football-data.co.uk/mmz4281/{season}/I1.csv"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
FIXTURES_FALLBACK_URL = "https://fixturedownload.com/feed/json/serie-a-2026"
DOCS_DATA = BASE_DIR.parent / "docs" / "data"   # JSON letti dall'app

# --- API-Football (opzionale: infortuni, squalifiche, allenatori, calendario)
# Chiave gratuita su https://dashboard.api-football.com  -> set API_FOOTBALL_KEY=...
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = 135          # Serie A
API_FOOTBALL_SEASON = 2026         # stagione 2026/27

# --- Modello -----------------------------------------------------------------
XI = 0.0019            # decadimento temporale (per giorno): ~ dimezza peso in 1 anno
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
