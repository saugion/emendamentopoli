# emendamentopoli

Valutazione IA dell'utilita' pubblica degli emendamenti parlamentari italiani.

**Stato**: prototipo (solo Senato; Camera in roadmap).

## Come funziona

1. **Ingestione**: scarica i metadati degli emendamenti dall'endpoint SPARQL
   di [dati.senato.it](https://dati.senato.it) (Open Data, licenza CC BY 3.0).
2. **Testi**: recupera il testo Akoma Ntoso di ogni emendamento da senato.it
   e ne estrae data di presentazione, atto di riferimento e testo plain-text.
3. **Valutazione**: un LLM (Mistral) valuta ogni emendamento su una rubrica
   di interesse pubblico con punteggi 0-10 e motivazioni obbligatorie:
   - trasparenza (peso 0.3) - trasparenza democratica, legalita', accountability
   - salute (0.25) - impatto su salute pubblica
   - ambiente (0.25) - impatto su ambiente e clima
   - concretezza (0.2) - efficacia tecnica, non genericita'
4. **Score composito**: media pesata dei criteri.
5. **Persistenza** (opzionale): salva emendamenti, valutazioni e punteggi su
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
2. Esegui in ordine `supabase/migrations/0001_schema.sql`,
   `supabase/migrations/0002_firmatari.sql` e
   `supabase/migrations/0003_firmatari_uri.sql` nel SQL Editor
   (oppure `npx supabase db push` con il repo collegato).
   La 0002 aggiunge la colonna `firmatari` (cognomi dei senatori firmatari)
   con indice trigram per la ricerca sui nomi; senza di essa la web app
   funziona comunque (i nomi vengono mostrati solo dopo re-ingest).
   La 0003 aggiunge `firmatari_uri` (URL della scheda profilo su senato.it,
   allineati a `firmatari`), usato per il link al profilo di ciascun
   firmatario nel pannello dettaglio.
3. Imposta le variabili d'ambiente `SUPABASE_URL` e `SUPABASE_KEY`
   (publishable/anon key; vedi `.env.example`).

Schema: `emendamenti` (upsert per id), `valutazioni` (una per valutazione),
`punteggi` (dettaglio per criterio). RLS attivo con policy anonime per il
prototipo: da raffinare prima di un uso reale in produzione.

## File

- `src/types.ts` - tipi + rubrica e pesi
- `src/senato.ts` - client SPARQL + parser Akoma Ntoso
- `src/evaluate.ts` - valutazione LLM (con fallback euristico)
- `src/db.ts` - persistenza Supabase (API REST, zero dipendenze)
- `src/main.ts` - CLI demo
- `supabase/migrations/0001_schema.sql` - schema del database
- `supabase/migrations/0002_firmatari.sql` - colonna `firmatari` + indice trigram (pg_trgm)
- `supabase/migrations/0003_firmatari_uri.sql` - colonna `firmatari_uri` (link al profilo senato.it dei firmatari)
- `index.html` - web app statica zero-dipendenze: esplora migliaia di emendamenti con ricerca full-text (testo/ddl/numero/riepilogo/firmatari), filtri per tipo e score, ordinamento, scroll infinito e pannello dettaglio con testo completo, firmatari con link al profilo su senato.it; tema chiaro/scuro; stato in URL (hash)

## Roadmap

- [x] Persistenza (Supabase/PostgreSQL)
- [ ] Coda e rate limiting per i batch (BullMQ)
- [ ] Ingestione Camera (scraping fascicoli; nessun dataset equivalente)
- [ ] API REST pubblica (Fastify) + web app Next.js
- [ ] Audit: campioni rivalutati con piu' modelli, feedback utenti
- [ ] Disclaimer e pesi pubblicati (mitigazione del bias politico)

## Note legali

Dati Senato: CC BY 3.0, citare la fonte "Senato della Repubblica - dati.senato.it".
Gli score sono valutazioni algoritmiche di supporto, non giudizi istituzionali.
Verificare i termini per lo scraping dei contenuti di camera.it prima di estendere.
