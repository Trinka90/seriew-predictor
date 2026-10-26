import sqlite3
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
  season TEXT, date TEXT, home TEXT, away TEXT,
  fthg INT, ftag INT, hthg INT, htag INT,
  hs INT, as_ INT, hst INT, ast INT, hc INT, ac INT,
  hy INT, ay INT, hr INT, ar INT,
  odd_h REAL, odd_d REAL, odd_a REAL, odd_o25 REAL, odd_u25 REAL,
  PRIMARY KEY(date, home, away));
CREATE TABLE IF NOT EXISTS fixtures(
  date TEXT, time TEXT, home TEXT, away TEXT,
  odd_h REAL, odd_d REAL, odd_a REAL, odd_o25 REAL, odd_u25 REAL,
  PRIMARY KEY(date, home, away));
CREATE TABLE IF NOT EXISTS news(
  link TEXT PRIMARY KEY, team TEXT, title TEXT, source TEXT,
  published TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS absences(
  team TEXT, player TEXT, reason TEXT, source TEXT, updated TEXT,
  PRIMARY KEY(team, player));
CREATE TABLE IF NOT EXISTS coaches(
  team TEXT PRIMARY KEY, coach TEXT, since TEXT, updated TEXT);
"""


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con
