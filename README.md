# Pronostici Serie A

Modello statistico (Dixon-Coles con decadimento temporale) + contesto attuale (classifica, forma, assenti, allenatori, notizie) → probabilità e quote eque su 1X2, doppia chance, Over/Under, GG/NG, multigol, combo, primo tempo, parziale/finale, risultati esatti, angoli e cartellini.

## Come funziona
```
GitHub Actions (3 volte al giorno)
  engine/export_json.py → scarica dati → calcola → docs/data/*.json
  → pubblica docs/ su GitHub Pages
App Android (WebView) → apre https://trinka90.github.io/seriew-predictor/
```
I pronostici e l'interfaccia si aggiornano senza reinstallare l'app. Una nuova APK si genera da sola (sezione *Releases*) solo quando si modifica `android/`.

## Struttura
| cartella | contenuto |
|---|---|
| `engine/` | Python: dati, modello, mercati, backtest (`python main.py --help`) |
| `docs/` | web app + `data/*.json` (GitHub Pages) |
| `android/` | guscio Android |
| `.github/workflows/` | `update.yml` (dati), `apk.yml` (APK) |

## Uso locale
```
cd engine
pip install -r requirements.txt
python main.py update
python main.py predict
python main.py backtest --stagione 2526
```

## Fonti
- Risultati, statistiche, quote: [football-data.co.uk](https://www.football-data.co.uk)
- Calendario: football-data.co.uk, fixturedownload.com
- Infortuni, squalifiche, allenatori: API-Football (secret `API_FOOTBALL_KEY`, opzionale)
- Notizie: Google News, Gazzetta, Corriere dello Sport (RSS)

## Correzioni manuali
`engine/manual.json`: assenze pesanti con impatto, aggiustamenti per cambio allenatore/crisi. Modificalo anche da web su GitHub: al salvataggio i pronostici si ricalcolano.

> Strumento statistico personale. Nessun pronostico è sicuro. Gioco vietato ai minori.
