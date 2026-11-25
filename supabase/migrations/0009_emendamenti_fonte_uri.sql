-- Emendamentopoli 0009: indice su emendamenti.fonte_uri (filtro per lista Camera in db_emendamenti_by_fonte)
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0008_emendamento_padre.sql
begin;

-- senza indice la query con fonte_uri=eq.<url> + embed valutazioni(id) va in statement
-- timeout (57014) quando la tabella cresce: seq scan su tutte le righe per ogni lista
create index if not exists idx_emendamenti_fonte_uri on emendamenti (fonte_uri);

commit;
