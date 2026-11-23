# workflows/

Worker [Mistral Workflows](https://docs.mistral.ai/studio/workflows/overview) che scarica
gli emendamenti del giorno prima e li salva su Supabase.

- **Workflow**: `emendamenti-giornalieri`
  - input: `data` (YYYY-MM-DD, default ieri in `Europe/Rome`), `legislatura` (default 19), `limite` (default 1000)
  - step: SPARQL su `dati.senato.it` (open data, CC BY 3.0) sugli emendamenti piu' recenti → testi Akoma Ntoso → filtro per data di presentazione → upsert su `rest/v1/emendamenti` (`on_conflict=id`)
- **Schedule**: ogni notte alle 03:00 `Europe/Rome` (cron `0 3 * * *`), creato via API/Studio sullo deployment `emendamentopoli`
- **Secret**: il worker legge `SUPABASE_URL` e `SUPABASE_KEY`, bindati dai workspace secret `supabase_url` e `supabase_key` (da creare in Studio, mai in chiaro nel repo)

## Sviluppo

```bash
cd workflows
uv sync --all-extras 2>/dev/null || uv sync
uv run pytest
```

## Deploy

Il managed deployment builda da questo repo (`build_directory=workflows`, `Dockerfile`)
al commit pushato del branch/PR, con i secret bindati. Vedi il PR di introduzione.
