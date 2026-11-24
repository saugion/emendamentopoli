-- Emendamentopoli: colonna firmatari (cognomi dei senatori, primo = proponente)
-- Eseguire nel SQL Editor di Supabase dopo 0001_schema.sql

create extension if not exists pg_trgm;

alter table emendamenti
  add column if not exists firmatari text;  -- "Cognome1, Cognome2, ..."

-- indice trigram per ricerca full-text sui nomi (ilike)
create index if not exists idx_emendamenti_firmatari_trgm
  on emendamenti using gin (firmatari gin_trgm_ops);
