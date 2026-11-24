# workflows/

Worker [Mistral Workflows](https://docs.mistral.ai/studio/workflows/overview) che scarica
gli emendamenti del giorno prima e li salva su Supabase.

- **Workflow**: `emendamenti-giornalieri`
  - input: `data` (inizio periodo, YYYY-MM-DD, default ieri in `Europe/Rome`), `data_fine` (fine periodo inclusivo, default = `data`), `legislatura` (default 19), `limite` (default 1000, max 20000)
  - step: SPARQL su `dati.senato.it` (open data, CC BY 3.0) sugli emendamenti piu' recenti → testi Akoma Ntoso → filtro per data di presentazione nel periodo `data..data_fine` → upsert su `rest/v1/emendamenti` (`on_conflict=id`, a chunk da 500)
  - valuta anche con Mistral (via service account del deployment, nessuna API key) e scrive `valutazioni` + `punteggi`
  - backfill multi-giorno: `data_fine` diverso da `data` e `limite` alto a sufficienza a coprire gli emendamenti del periodo (es. 20000); la query SPARQL e' paginata (LIMIT 1000 + OFFSET, early stop a copertura del periodo) perche' l'endpoint risponde 500 con LIMIT molto alti; l'arricchimento procede con concorrenza limitata (8) e retry con backoff su 403/429, per non farsi bloccare da CloudFront
- **Workflow**: `valuta-emendamenti-mancanti` (backfill: emendamenti in Supabase senza valutazione → valutazione LLM)
  - input: `limite` (default 500, max 5000)
- **Schedule**: ogni notte alle 03:00 `Europe/Rome` (cron `0 3 * * *`), creato via API/Studio sullo deployment `emendamentopoli`
- **Secret**: il worker legge `SUPABASE_URL` e `SUPABASE_KEY`, bindati dai workspace secret `supabase_url` e `supabase_key` (da creare in Studio, mai in chiaro nel repo)

## Ingest manuale da GitHub Actions

L'action `.github/workflows/ingest-emendamenti.yml` (`workflow_dispatch`) lancia a mano
`emendamenti-giornalieri` via REST API Mistral (`POST /v1/workflows/emendamenti-giornalieri/execute`,
poi polling su `GET /v1/workflows/executions/{id}` fino al completamento; il job fallisce se
l'esecuzione termina in errore):

- `data_inizio` (obbligatoria, YYYY-MM-DD): inizio del periodo da ingerire
- `data_fine` (opzionale; default oggi in `Europe/Rome`): fine del periodo
- `legislatura` (default 19), `limite` (default 20000: deve coprire gli emendamenti di tutto il periodo)

Richiede il secret di repository `MISTRAL_API_KEY` (chiave API Mistral con accesso ai workflows
 dello workspace). Esempio backfill di novembre: `data_inizio=2026-11-01`, `data_fine=2026-11-30`.
Il job notturno continua a coprire i giorni recenti: l'action serve per i backfill a mano.

## Sviluppo

```bash
cd workflows
uv sync --all-extras 2>/dev/null || uv sync
uv run pytest
```

## Deploy

Il managed deployment builda da questo repo (`build_directory=workflows`, `Dockerfile`)
al commit pushato del branch/PR, con i secret bindati. Vedi il PR di introduzione.
