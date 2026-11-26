-- Emendamentopoli 0012: indice partial covering per il conteggio esatto (count=exact) della web app
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0011_webapp_list_indexes.sql
begin;

-- Con Prefer: count=exact, PostgREST 14 genera un CTE separato:
--   pgrst_source_count AS (SELECT 1 FROM valutazioni v WHERE EXISTS (
--     SELECT 1 FROM emendamenti e WHERE e.corrente AND e.id = v.emendamento_id
--     [AND e.tipo / e.camera / e.numero_padre ...]))
-- Il probe su emendamenti_pkey legge l'heap della riga (tabella larga, testo in TOAST)
-- solo per valutare corrente: 18k heap fetch -> ~4s. Il ruolo anon ha
-- statement_timeout=3s, quindi ogni caricamento pagina / stats va in 57014.
-- Indice partial covering: il probe diventa index-only, nessun heap fetch;
-- tipo/camera/numero_padre coperti per i filtri della lista e della ricerca.
create index if not exists idx_emendamenti_corrente_covering
  on emendamenti (id, tipo, camera, numero_padre) where corrente;

commit;
