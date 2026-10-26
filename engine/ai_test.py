"""Prova ogni fornitore IA configurato con una richiesta minima. Salva solo l'esito, mai le chiavi."""
import json
import time
from pathlib import Path

import news_ai as N

PROMPT = ('Partita: Napoli (casa) - Monza (ospite), Serie A.\n\nTITOLI SU Napoli:\n'
          '[1] (2026-10-26, Test) Napoli, Spinazzola ko: lesione al flessore, salta il Monza\n\n'
          'TITOLI SU Monza:\n[2] (2026-10-26, Test) Monza, allenamento regolare verso il Napoli\n')
N._deadline[0] = time.time() + 300
out = {}
for p in N.PROVIDERS:
    nome = p["nome"]
    if p not in N.ACTIVE:
        out[nome] = {"chiave": "assente"}
        continue
    t = time.time()
    try:
        N._refresh_models(p)
        res = N._sanitize(N._parse(N._call_provider(p, PROMPT)))
        out[nome] = {"chiave": "presente", "esito": "OK", "secondi": round(time.time() - t, 1),
                     "modelli_disponibili": p["modelli"][:4],
                     "assenti_trovati": [a["nome"] for a in res["casa"]["assenti"]]}
    except Exception as e:
        out[nome] = {"chiave": "presente", "esito": "ERRORE", "dettaglio": str(e)[:300],
                     "modelli_disponibili": p["modelli"][:4]}

path = Path(__file__).resolve().parent.parent / "experiments" / "ai_test.json"
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps(out, indent=1, ensure_ascii=False))
