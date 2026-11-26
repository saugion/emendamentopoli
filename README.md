# emendamentopoli

Valutazione IA dell'utilita' pubblica degli emendamenti parlamentari italiani.

**Stato**: prototipo (Senato e Camera dei deputati, legislatura XIX).

## Come funziona

1. **Ingestione Senato**: metadati degli emendamenti e ordini del giorno dall'endpoint SPARQL
   di [dati.senato.it](https://dati.senato.it) (Open Data, licenza CC BY 3.0); il testo Akoma Ntoso
   di ogni atto si scarica da senato.it (data di presentazione, ddl, testo, firmatari).
   I subemendamenti (proposte di modifica, numero "X/N") vengono collegati all'emendamento padre:
   `numero_padre` = "X" (dal testo AKN o dal numero) e `emendamento_padre_id` = URI del padre
   risolto via SPARQL sullo stesso atto + sede, cosi' si puo' risalire al padre.
2. **Ingestione Camera**: metadati degli ordini del giorno e dei progetti di legge dall'endpoint SPARQL
   di [dati.camera.it](https://dati.camera.it) (licenza CC BY-SA 4.0); lo SPARQL fornisce solo i metadati,
   il testo (con tutte le versioni, firmatari, parere del Governo ed esito) si legge per singolo atto
   dalle schede di documenti.camera.it. Gli emendamenti della Camera non sono nello SPARQL: si leggono
   dagli elenchi e dalle schede delle proposte emendative su documenti.camera.it.
3. **Filtro di rilevanza finanziaria**: tutti i metadati vengono importati, ma l'LLM valuta solo gli atti
   che incidono sull'allocazione di denaro pubblico (regole lessicali + natura dell'atto: decreti-legge,
   leggi di bilancio, assegnazione alla V Commissione Bilancio).
4. **Versioni**: ogni riformulazione ("testo 2", "nuova formulazione", testo modificato in seduta) e' una
   riga separata; solo la versione corrente e' mostrata.
5. **Valutazione**: un LLM (Mistral) valuta ogni emendamento su una rubrica
   di interesse pubblico con punteggi 0-10 e motivazioni obbligatorie:
   - trasparenza (peso 0.3) - trasparenza democratica, legalita', accountability
   - salute (0.25) - impatto su salute pubblica
   - ambiente (0.25) - impatto su ambiente e clima
   - concretezza (0.2) - efficacia tecnica, non genericita'
6. **Score composito**: media pesata dei criteri.
7. **Persistenza** (opzionale): salva emendamenti, valutazioni e punteggi su
   Supabase via API REST.

Senza `MISTRAL_API_KEY` gira in modalita' euristica (lessicale), solo sviluppo.

## Uso

```bash
MISTRAL_API_KEY=... npm run demo -- --limit 10
npm run demo -- --limit 10 --offset 50

# con persistenza Supabase:
SUPABASE_URL=https://<project>.supabase.co \
SUPABASE_KEY=sb_publishable_... \
npm run demo -- --limit 10
```

Zero dipendenze runtime: richiede Node 22+ (TypeScript nativo).

## Setup Supabase

1. Crea un progetto su [supabase.com](https://supabase.com).
2. Esegui in ordine nel SQL Editor (oppure `npx supabase db push` con il repo collegato):
   - `supabase/migrations/0001_schema.sql` - schema iniziale (`emendamenti`, `valutazioni`, `punteggi`)
   - `supabase/migrations/0002_firmatari.sql` - colonna `firmatari` (cognomi dei senatori firmatari) con indice trigram per la ricerca sui nomi; senza di essa la web app funziona comunque (i nomi vengono mostrati solo dopo re-ingest)
   - `supabase/migrations/0003_firmatari_uri.sql` - colonna `firmatari_uri` (URL della scheda profilo su senato.it, allineati a `firmatari`), usato per il link al profilo di ciascun firmatario nel pannello dettaglio
   - `supabase/migrations/0004_hardening.sql` - chiavi naturali, versioni (`corrente`) e unicita' delle valutazioni
   - `supabase/migrations/0005_camera_odg_atti.sql` - colonne Camera, tabelle `atti` e `ingest_cursori`
   - `supabase/migrations/0006_rilevanza.sql` - filtro di rilevanza e campi finanziari della rubrica v2
   - `supabase/migrations/0007_camera_emendamenti.sql` - stato degli elenchi emendamenti della Camera (GET condizionale)
   - `supabase/migrations/0008_emendamento_padre.sql` - colonne `numero_padre` / `emendamento_padre_id` (link subemendamento -> emendamento padre) + backfill SQL dei record gia' esistenti (numero "X/N" -> padre "X", link via chiave_naturale); la web app mostra il badge "rif" e il link "scheda padre" nel dettaglio
   - `supabase/migrations/0009_emendamenti_fonte_uri.sql` - indice su `emendamenti(fonte_uri)`: senza, il filtro per lista Camera in `db_emendamenti_by_fonte` va in statement timeout (57014) a tabella piena
   - `supabase/migrations/0010_idx_numero_padre.sql` - indice partial su `numero_padre` (filtri su subemendamenti senza statement timeout)
   - `supabase/migrations/0011_webapp_list_indexes.sql` - indici per le query della web app: `valutazioni(score_totale desc nulls last)` per l'ordinamento di default della lista, `valutazioni(ts)` per le stats, partial su `emendamenti(tipo)`/`(data desc)`, trigram GIN su `testo`/`ddl`/`numero`/`riepilogo` per la ricerca `ilike` (senza, caricamento pagina e ricerca vanno in statement timeout 57014 a tabella piena)
   - `supabase/migrations/0012_count_covering_index.sql` - indice partial covering `emendamenti(id,tipo,camera,numero_padre) where corrente`: il conteggio `count=exact` di PostgREST (`pgrst_source_count` con `EXISTS`) fa probe index-only invece di heap fetch su `emendamenti_pkey`; con `statement_timeout=3s` del ruolo `anon` il count superava il timeout (57014) a ogni caricamento pagina
   - `supabase/migrations/0013_eval_queue_index.sql` - indice partial `emendamenti(data desc nulls last) include (id) where corrente and rilevante_spesa and testo is not null` per la coda dei worker `amendments_without_evaluation`: scan index-only dei soli candidati (senza, seq scan + 18k sonde laterali ~8s -> 57014 con `statement_timeout=3s` del ruolo `anon`)
   - `supabase/migrations/0014_queue_senza_valutazione_rpc.sql` - funzione RPC `queue_senza_valutazione(p_limit)` usata dai worker al posto della query REST con anti-join `valutazioni=is.null` (LEFT JOIN LATERAL + heap per ogni candidato -> 57014): prima scan index-only + NOT EXISTS, poi fetch solo delle righe selezionate; colonne con gli alias inglesi del modello `Amendment`
   - `supabase/migrations/0015_date_sort_covering_indexes.sql` - indici per l'ordinamento per data della lista: `emendamenti(data desc nulls last)` plain (early-stop senza filtri) + compositi `(camera, corrente, tipo, data asc/desc nulls last)` con INCLUDE delle colonne della vista lista (filtri index-only; senza, heap fetch per ~54k entry -> ~7s -> 57014 con `statement_timeout=3s` del ruolo `anon`)
3. Imposta le variabili d'ambiente `SUPABASE_URL` e `SUPABASE_KEY`
   (publishable/anon key; vedi `.env.example`).

Schema: `emendamenti` (upsert per id), `valutazioni` (una per valutazione),
`punteggi` (dettaglio per criterio). RLS attivo con policy anonime per il
prototipo: da raffinare prima di un uso reale in produzione.

## File

- `src/types.ts` - tipi + rubrica e pesi
- `src/senate.ts` - client SPARQL + fetch dei testi Akoma Ntoso
- `src/akn.ts` - parser Akoma Ntoso puro (`parseAkn`), senza fetch
- `src/evaluate.ts` - valutazione LLM (con fallback euristico)
- `src/db.ts` - persistenza Supabase (API REST, zero dipendenze)
- `src/main.ts` - CLI demo
- `tests/` - test Node (`npm test`, zero dipendenze): `akn.test.ts` guida `src/akn.ts` sulle fixture condivise `tests/fixtures/akn/*.xml` + `*.expected.json`, lette anche dalla suite Python (`workflows/tests/test_akn_fixtures.py`)
- `supabase/migrations/0001_schema.sql` - schema del database
- `supabase/migrations/0002_firmatari.sql` - colonna `firmatari` + indice trigram (pg_trgm)
- `supabase/migrations/0003_firmatari_uri.sql` - colonna `firmatari_uri` (link al profilo senato.it dei firmatari)
- `store.js` - modulo ES che possiede tutto l'accesso PostgREST della web app (`createStore({url, key})` -> `init`/`search`/`detail`/`stats`): headers, query building, sanitizzazione, content-range, mappatura errori, rilevamento colonne `firmatari`. Per gli ordinamenti per data (`data_asc`/`data_desc`) `search` interroga `emendamenti` come root con `valutazioni!inner` e `order=data.*` (colonna root -> indice + early stop; sulla colonna embedded faceva full scan ~11s -> 57014) e rimappa le righe nella forma `valutazione` attesa dalla UI
- `index.html` - web app statica zero-dipendenze (usa `store.js` come modulo ES): esplora migliaia di emendamenti con ricerca full-text (testo/ddl/numero/riepilogo/firmatari), filtri per tipo e score, ordinamento, scroll infinito e pannello dettaglio con testo completo, firmatari con link al profilo su senato.it; tema chiaro/scuro; stato in URL (hash)
- `tests/store.test.mjs` - test dello store con fetch stubbato (via `npm test`)

## Volumi misurati

Misurati su Supabase il 2026-11-26, a backfill completato:

- **709 elenchi** emendamenti della Camera tracciati in `camera_liste_emendamenti`,
  **98.343 righe di elenco** lette in totale (somma della colonna `righe` all'ultima risposta 200 di ciascun elenco)
- **55.564 emendamenti** in `emendamenti`: **54.368 Camera** (di cui **47.716** versioni correnti) e 1.196 Senato; 48.901 correnti in totale
- **3.225 atti** in `atti`
- **18.452 valutazioni** con rubrica v2 (tutte con `impatto_finanziario` valorizzato; 986 sono legacy v1) e **77.732 righe** in `punteggi`
- Backfill iniziale: ~20 run manuali di ingest in ~18h di loop; da allora ingest incrementale notturno
  (Senato 02:00 UTC, Camera 04:05 / 04:40 / 06:45 UTC, vedi `scripts/mistral_schedules.sh`)
- Campione stratificato per la validazione umana del filtro di rilevanza: `docs/validation/rilevanza_campione.csv` (200 righe) + `docs/validation/rilevanza_popolazione.json`

## Roadmap

- [x] Persistenza (Supabase/PostgreSQL)
- [ ] Coda e rate limiting per i batch (BullMQ)
- [x] Ingestione Camera: ODG e progetti di legge da dati.camera.it (SPARQL, solo metadati) + testi per singolo atto da documenti.camera.it; emendamenti dagli elenchi di documenti.camera.it
- [ ] API REST pubblica (Fastify) + web app Next.js
- [ ] Audit: campioni rivalutati con piu' modelli, feedback utenti
- [ ] Disclaimer e pesi pubblicati (mitigazione del bias politico)

## Note legali

Dati Senato: CC BY 3.0, fonte "Senato della Repubblica - dati.senato.it".
Dati Camera: CC BY-SA 4.0, fonte "Camera dei deputati - dati.camera.it"; testi da documenti.camera.it.
Le valutazioni pubblicate (score, punteggi, motivazioni, indicatori finanziari) sono opera derivata e
sono rilasciate con licenza CC BY-SA 4.0.
Gli score sono valutazioni algoritmiche di supporto, non giudizi istituzionali.
Accesso ai siti istituzionali: user-agent `emendamentopoli-research/0.1`, al massimo 1 richiesta al secondo
per host, nel rispetto di robots.txt.
