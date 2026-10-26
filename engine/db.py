import sqlite3
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
  league TEXT, season TEXT, date TEXT, home TEXT, away TEXT,
  fthg INT, ftag INT, hthg INT, htag INT,
  hs INT, as_ INT, hst INT, ast INT, hc INT, ac INT,
  hy INT, ay INT, hr INT, ar INT,
  odd_h REAL, odd_d REAL, odd_a REAL, odd_o25 REAL, odd_u25 REAL,
  PRIMARY KEY(date, home, away));
CREATE TABLE IF NOT EXISTS fixtures(
  league TEXT, date TEXT, time TEXT, home TEXT, away TEXT,
  odd_h REAL, odd_d REAL, odd_a REAL, odd_o25 REAL, odd_u25 REAL,
  PRIMARY KEY(date, home, away));
CREATE TABLE IF NOT EXISTS news(
  link TEXT PRIMARY KEY, team TEXT, title TEXT, source TEXT,
  published TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS absences(
  team TEXT, player TEXT, reason TEXT, source TEXT, updated TEXT,
  impact_att REAL, impact_def REAL, info TEXT, partita TEXT DEFAULT '',
  PRIMARY KEY(team, player, partita));
CREATE TABLE IF NOT EXISTS match_events(
  home TEXT, away TEXT, data TEXT, PRIMARY KEY(home, away));
CREATE TABLE IF NOT EXISTS coaches(
  team TEXT PRIMARY KEY, coach TEXT, since TEXT, updated TEXT);
"""


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con
