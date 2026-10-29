# Backtest semaforo e pronostico robusto — stagione 25/26
_Generato il 2026-10-29 · 7634 partite in 22 campionati · quote di chiusura trovate per 7634 partite_

Colonne: **Indicato** = probabilità media data dall'app · **Uscito** = quante volte è uscito davvero · **Scarto** = Uscito − Indicato in punti (negativo = l'app era troppo sicura) · **Resa** = giocando 1 € a quote Bet365.

## 1. Nelle partite segnalate il pronostico esce meno?

| Gruppo | Partite | Indicato | Uscito | Scarto | Resa |
|---|---|---|---|---|---|
| Tutte le partite | 7634 | 74% | **74%** | +0.2 | -6.7% |
| Calendario fitto (≤3 giorni di riposo) — SÌ | 1009 | 74% | **75%** | +1.2 | -6.0% |
| Calendario fitto (≤3 giorni di riposo) — no | 6625 | 74% | **74%** | +0.1 | -6.8% |
| Modello e mercato in disaccordo (≥10 punti) — SÌ | 829 | 74% | **75%** | +1.5 | -5.6% |
| Modello e mercato in disaccordo (≥10 punti) — no | 6805 | 74% | **74%** | +0.1 | -6.9% |
| Pochi dati su una squadra — SÌ | 703 | 74% | **75%** | +0.7 | -6.1% |
| Pochi dati su una squadra — no | 6931 | 74% | **74%** | +0.2 | -6.8% |
| Quote mosse ≥5 punti (proxy notizie) — SÌ | 769 | 74% | **74%** | -0.5 | -7.8% |
| Quote mosse ≥5 punti (proxy notizie) — no | 6865 | 74% | **74%** | +0.3 | -6.6% |

## 2a. Semaforo (solo segnali noti prima della partita)

| Semaforo | Partite | Indicato | Uscito | Scarto | Resa |
|---|---|---|---|---|---|
| 🟢 | 5472 | 74% | **74%** | -0.0 | -6.9% |
| 🟡 | 2162 | 74% | **75%** | +0.8 | -6.3% |
| 🔴 | 0 | – | – | – | – |

## 2b. Semaforo (con le quote mosse come proxy delle notizie)

| Semaforo | Partite | Indicato | Uscito | Scarto | Resa |
|---|---|---|---|---|---|
| 🟢 | 4933 | 74% | **74%** | +0.1 | -6.7% |
| 🟡 | 2701 | 74% | **74%** | +0.4 | -6.8% |
| 🔴 | 0 | – | – | – | – |

## 3. Pronostico robusto contro pronostico standard

| Partite | Pronostico | Volte | Indicato | Uscito | Scarto | Resa |
|---|---|---|---|---|---|---|
| Tutte | standard | 7634 | 74% | **74%** | +0.2 | -6.7% |
| Tutte | robusto | 7634 | 74% | **73%** | -0.3 | -7.2% |
| 🟡+🔴 (con notizie) | standard | 2701 | 74% | **74%** | +0.4 | -6.8% |
| 🟡+🔴 (con notizie) | robusto | 2701 | 73% | **74%** | +0.7 | -6.0% |

Il pronostico robusto è diverso da quello standard in 2023 partite su 7634. In quelle partite: standard uscito 75%, robusto uscito 71%.

Cambi più frequenti: 1X → 12 (301), Over 1.5 → 12 (247), Over 1.5 → Under 3.5 (214), 12 → Under 3.5 (187), 1 → Multigol 2-4 (162), 1X → Multigol 1-3 (140)

## 4. Quote di apertura contro quote di chiusura

Su 7634 partite con entrambe le quote:

| Quote usate | LogLoss 1X2 (più basso = meglio) | Pronostico uscito | Indicato | Resa |
|---|---|---|---|---|
| Apertura (come oggi) | 1.0046 | **74%** | 74% | -6.7% |
| Chiusura (aggiornate al fischio) | 1.0017 | **75%** | 74% | -5.8% |

## 5. Effetto sulla schedina del giorno da 4

| Regola | Giorni | Uscita | Attese | Quota media | Resa |
|---|---|---|---|---|---|
| Come oggi | 214 | **37%** | 36% | 2.21 | -17.9% |
| Senza le 🔴 (solo segnali noti) | 214 | **37%** | 36% | 2.21 | -17.9% |
| Senza le 🔴 (con notizie) | 214 | **37%** | 36% | 2.21 | -17.9% |
| Pronostico robusto | 212 | **35%** | 35% | 2.22 | -22.6% |
| Robusto + senza 🔴 (con notizie) | 212 | **35%** | 35% | 2.22 | -22.6% |
