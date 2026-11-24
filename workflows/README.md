# workflows/

Worker [Mistral Workflows](https://docs.mistral.ai/studio/workflows/overview) che scarica
gli emendamenti del giorno prima e li salva su Supabase.

- **Workflow**: `emendamenti-giornalieri`
  - input: `date` (inizio periodo, YYYY-MM-DD, default ieri in `Europe/Rome`), `end_date` (fine periodo inclusivo, default = `date`), `legislature` (default 19), `limit` (default 1000, max 20000)
  - step: SPARQL su `dati.senato.it` (open data, CC BY 3.0) sugli emendamenti piu' recenti → testi Akoma Ntoso → filtro per data di presentazione nel periodo `date..end_date` → upsert su `rest/v1/emendamenti` (`on_conflict=id`, a chunk da 500) man mano, batch SPARQL per batch
  - valuta anche con Mistral (via service account del deployment, nessuna API key) e scrive `valutazioni` + `punteggi`
  - backfill multi-giorno: `end_date` diverso da `date` e `limit` alto a sufficienza a coprire gli emendamenti del periodo (default 5000); la query SPARQL e' paginata a batch da 100 (LIMIT 100 + OFFSET, early stop a copertura del periodo) perche' l'endpoint risponde 500 con LIMIT molto alti; gli emendamenti gia' presenti su Supabase (mappa id->data letta all'inizio) non vengono ri-scaricati, quindi i rilanci sono economici; l'arricchimento procede con concorrenza limitata (8) e retry con backoff su 403/429, per non farsi bloccare da CloudFront
- **Workflow**: `valuta-emendamenti-mancanti` (backfill: emendamenti in Supabase senza valutazione → valutazione LLM)
  - input: `limit` (default 500, max 5000)
- **Workflow**: `camera-atti` (progetti di legge della Camera da dati.camera.it → tabella `atti`, con flag decreto-legge / bilancio / V Commissione)
  - input: `legislature` (solo 19), `full` (default false: cursore `ods:modified` con 2 giorni di sovrapposizione)
- **Workflow**: `camera-odg-giornalieri` (ordini del giorno della Camera)
  - input: `date`/`end_date` (backfill su `dc:date`; vuoti = modalita' incrementale col cursore), `legislature` (19), `max_items` (default 1500, max 5000), `evaluate`
  - SPARQL su dati.camera.it (solo metadati, paginazione keyset: OFFSET oltre 10000 righe non e' supportato da Virtuoso) → scheda sicross su documenti.camera.it per gli atti nuovi o con metadati cambiati (tutte le versioni del testo) → upsert per versione → valutazione solo se rilevanti per la spesa
- **Workflow**: `camera-emendamenti-giornalieri` (emendamenti della Camera)
  - input: `atti` (pilota), `max_atti`, `max_liste`, `max_detail`, `evaluate`
  - scoperta degli elenchi dalle pagine atto di camera.it → elenchi con GET condizionale (If-Modified-Since) → scheda di ogni proposta nuova o cambiata (tutte le formulazioni)
- **Workflow**: `manutenzione-emendamenti` (ricalcolo `testo_hash` e filtro di rilevanza)
- **Schedule Camera**: `camera-atti` `5 4 * * *` UTC, `camera-odg-giornalieri` `40 4 * * *` UTC, `camera-emendamenti-giornalieri` `45 6 * * *` UTC (dopo la fine massima possibile del job Senato delle 03:00 Europe/Rome, senza sovrapposizioni), create con `scripts/mistral_schedules.sh` (dopo i backfill); lo schedule Senato resta invariato
- **Cortesia verso le fonti Camera**: user-agent `emendamentopoli-research/0.1`, 1 richiesta/s per host, concorrenza 2, retry 1/2/4/8 s su 403/429/5xx
- **Volumi misurati**: <fill in with the numbers of §G.9 step 3: liste, righe>
- **Schedule**: ogni notte alle 03:00 `Europe/Rome` (cron `0 3 * * *`), creato via API/Studio sullo deployment `emendamentopoli`
- **Secret**: il worker legge `SUPABASE_URL` e `SUPABASE_KEY`, bindati dai workspace secret `supabase_url` e `supabase_key` (da creare in Studio, mai in chiaro nel repo)

## Ingest manuale da GitHub Actions

L'action `.github/workflows/ingest-amendments.yml` (`workflow_dispatch`) lancia a mano
`emendamenti-giornalieri` via REST API Mistral (`POST /v1/workflows/emendamenti-giornalieri/execute`,
poi polling su `GET /v1/workflows/executions/{id}` fino al completamento; il job fallisce se
l'esecuzione termina in errore):

- `start_date` (obbligatoria, YYYY-MM-DD): inizio del periodo da ingerire
- `end_date` (opzionale; default oggi in `Europe/Rome`): fine del periodo
- `legislature` (default 19), `limit` (default 5000: deve coprire gli emendamenti di tutto il periodo)
- `source` (senato | camera_odg | camera_atti | camera_emendamenti) sceglie il workflow; backfill Camera ODG per mese con `scripts/backfill_camera_odg.sh 2022-10 2026-11`

Richiede il secret di repository `MISTRAL_API_KEY` (chiave API Mistral con accesso ai workflows
 dello workspace). Esempio backfill di novembre: `start_date=2026-11-01`, `end_date=2026-11-30`.
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
