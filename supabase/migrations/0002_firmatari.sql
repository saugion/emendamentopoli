-- Emendamentopoli: signatories column (senators' surnames, first = proposer)
-- Run in the Supabase SQL Editor after 0001_schema.sql

create extension if not exists pg_trgm;

alter table emendamenti
  add column if not exists firmatari text;  -- "Surname1, Surname2, ..."

-- trigram index for name search (ilike)
create index if not exists idx_emendamenti_firmatari_trgm
  on emendamenti using gin (firmatari gin_trgm_ops);
