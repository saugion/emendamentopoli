-- Emendamentopoli 0009: indice partial su numero_padre (filtri UI tipo "escludi subemendamenti")
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0008_emendamento_padre.sql
begin;

create index if not exists idx_emendamenti_numero_padre
  on emendamenti(numero_padre)
  where numero_padre is not null;

commit;
