# Backtest tennis e NBA — migliorie proposte

Generato il 2026-10-29. Nessuna modifica all'app: solo simulazione sul passato.

LogLoss: misura quanto sono giuste le percentuali (più basso = meglio; differenze di 0,005 sono già concrete).

## 🏀 NBA

Taratura: 2025-02-01 → 2025-10-29 (1313 partite). Giudizio: dal 2025-10-29 (1406 partite).

| Variante | Vincente indovinato | LogLoss (più basso = meglio) | Brier |
|---|---|---|---|
| App di oggi | 67.5% | 0.6178 | 0.2124 |
| + correzione favoriti | 67.4% | 0.6128 | 0.2114 |
| + correzione + back-to-back | 66.6% | 0.6165 | 0.2130 |
| + correzione + riposo/back-to-back | 66.4% | 0.6162 | 0.2128 |
| (controllo) Elo SENZA margine di vittoria | 67.2% | 0.6182 | 0.2128 |

Coefficienti correzione: a=0.859, c=-0.130 (a<1 = l'app era troppo sicura).
Coefficienti riposo: back-to-back -0.342, giorni riposo +0.116, partite in 7gg -0.001 (in logit; 0,1 ≈ 2,5 punti percentuali vicino al 50%)

**Calibrazione — App di oggi** (favorito)

| Fascia | Partite | Detto | Uscito |
|---|---|---|---|
| 50-60% | 399 | 55% | 57% |
| 60-70% | 364 | 65% | 63% |
| 70-80% | 323 | 75% | 72% |
| 80-90% | 243 | 85% | 81% |
| 90-100% | 77 | 93% | 81% |

**Calibrazione — + correzione favoriti** (favorito)

| Fascia | Partite | Detto | Uscito |
|---|---|---|---|
| 50-60% | 477 | 55% | 58% |
| 60-70% | 402 | 65% | 66% |
| 70-80% | 329 | 75% | 75% |
| 80-90% | 175 | 84% | 81% |
| 90-100% | 23 | 92% | 83% |

Fatti grezzi (periodo di giudizio): casa in back-to-back vs ospite riposato → casa vince 49% (modello diceva 59%, 154 partite); ospite in back-to-back vs casa riposata → casa vince 53% (modello 59%, 176 partite).

**Favoriti sopra soglia (giudizio)**

| Soglia | Variante | Partite | Detto | Uscito |
|---|---|---|---|---|
| 70% | App di oggi | 643 | 81% | 77% |
| 70% | + correzione favoriti | 527 | 79% | 77% |
| 80% | App di oggi | 320 | 87% | 81% |
| 80% | + correzione favoriti | 198 | 85% | 81% |

**Contro il bookmaker (quote storiche ESPN)**

Partite con quote: 1192 — fonte: ESPN

| Chi | Vincente | LogLoss | ROI puntando il nostro favorito | ROI solo dove vediamo valore (>5%) |
|---|---|---|---|---|
| Bookmaker | 68.3% | 0.5802 | — | — |
| App di oggi | 67.2% | 0.6239 | -1.8% | -11.8% (746 giocate) |
| + correzione favoriti | 67.0% | 0.6180 | -2.1% | -15.6% (814 giocate) |
| + correzione + back-to-back | 66.4% | 0.6218 | -3.1% | -14.8% (788 giocate) |
| + correzione + riposo/back-to-back | 66.2% | 0.6211 | -3.3% | -15.0% (786 giocate) |
| (controllo) Elo SENZA margine di vittoria | 67.0% | 0.6230 | -2.3% | -10.3% (742 giocate) |
| Fusione 80% quote + modello migliorato | 67.8% | 0.5832 | — | — |
| Fusione 80% quote + modello di oggi (= app quando ci sono quote) | 68.0% | 0.5831 | — | — |

## 🎾 ATP

Taratura: 2025-02-01 → 2025-10-29 (2038 partite). Giudizio: dal 2025-10-29 (3032 partite).

| Variante | Partite | Vincente indovinato | LogLoss | Brier |
|---|---|---|---|---|
| App di oggi (Elo calibrato) | 3032 | 63.2% | 0.6375 | 0.2239 |
| + stanchezza / rientro / tipo torneo | 3032 | 63.1% | 0.6361 | 0.2229 |
| + peso torneo (quali×0.4, slam×1.5) e margine set +0.15 | 3032 | 63.4% | 0.6347 | 0.2227 |
| + peso torneo (quali×0.4, slam×1.5) e margine set +0.15 + stanchezza | 3032 | 63.5% | 0.6326 | 0.2214 |

Coefficienti (logit): fiducia ×0.84, extra Slam ×+1.19, extra qualificazioni ×-0.08; set giocati negli ultimi 3 giorni -0.060 per set di differenza, partite in 14 giorni -0.015, rientro dopo >3 settimane -0.545.

**Affidabilità per tipo di torneo (app di oggi)**

| Tipo | Partite | Vincente | Detto (fav.) | Uscito (fav.) |
|---|---|---|---|---|
| Qualificazioni | 634 | 58.2% | 60% | 58% |
| Slam | 473 | 68.3% | 65% | 68% |
| Tornei del circuito | 1925 | 63.6% | 64% | 64% |

## 🎾 WTA

Taratura: 2025-02-01 → 2025-10-29 (2885 partite). Giudizio: dal 2025-10-29 (4966 partite).

| Variante | Partite | Vincente indovinato | LogLoss | Brier |
|---|---|---|---|---|
| App di oggi (Elo calibrato) | 4966 | 62.0% | 0.6427 | 0.2261 |
| + stanchezza / rientro / tipo torneo | 4966 | 62.5% | 0.6420 | 0.2258 |
| + peso torneo (quali×0.4, slam×1.5) e margine set +0.15 | 4966 | 62.9% | 0.6408 | 0.2253 |
| + peso torneo (quali×0.4, slam×1.5) e margine set +0.15 + stanchezza | 4966 | 62.7% | 0.6402 | 0.2252 |

Coefficienti (logit): fiducia ×0.98, extra Slam ×+0.34, extra qualificazioni ×-0.34; set giocati negli ultimi 3 giorni -0.090 per set di differenza, partite in 14 giorni +0.015, rientro dopo >3 settimane -0.480.

**Affidabilità per tipo di torneo (app di oggi)**

| Tipo | Partite | Vincente | Detto (fav.) | Uscito (fav.) |
|---|---|---|---|---|
| Qualificazioni | 1147 | 60.8% | 61% | 61% |
| Slam | 485 | 73.2% | 66% | 73% |
| Tornei del circuito | 3334 | 60.8% | 62% | 61% |

_tennis-data.co.uk non raggiungibile in questo giro: confronto con Bet365 non fatto._

<details><summary>Diagnostica download</summary>

```
http://www.tennis-data.co.uk/2025/2025.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2025/2025.xls -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2025/2025.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2025/2025.xls -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2026/2026.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2026/2026.xls -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2026/2026.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2026/2026.xls -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2025w/2025.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2025w/2025.xls -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2025w/2025.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2025w/2025.xls -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2026w/2026.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
http://www.tennis-data.co.uk/2026w/2026.xls -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2026w/2026.xlsx -> 403, 4552 byte, text/html; charset=UTF-8
https://www.tennis-data.co.uk/2026w/2026.xls -> 403, 4552 byte, text/html; charset=UTF-8
```
</details>

_Tempo di calcolo: 34 s_