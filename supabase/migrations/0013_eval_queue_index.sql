-- Emendamentopoli 0013: indice partial per la coda "emendamenti senza valutazione" dei worker
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0012_count_covering_index.sql
begin;

-- amendments_without_evaluation (workflows/src/amendments_worker/workflows/supabase.py)
-- filtra emendamenti con:
--   corrente IS TRUE AND NOT testo IS NULL AND rilevante_spesa IS TRUE
--   AND NOT EXISTS (SELECT 1 FROM valutazioni WHERE emendamento_id = emendamenti.id)
--   ORDER BY data DESC NULLS LAST LIMIT n
-- Senza indice il planner fa seq scan della tabella larga (testo in TOAST) + una
-- sonda laterale per ogni candidato (~8s su 18k candidati): con statement_timeout=3s
-- del ruolo anon la query va in 57014 e la coda di valutazione si blocca.
-- Indice partial sulle sole righe candidate, ordinate per data: scan index-only in
-- ordine (id in INCLUDE), stop a LIMIT; l'anti-join resta una sonda index-only
-- sull'indice di valutazioni. Serve a queue_senza_valutazione (migrazione 0014).
create index if not exists idx_emendamenti_eval_queue
  on emendamenti (data desc nulls last) include (id)
  where corrente and rilevante_spesa and testo is not null;

commit;
