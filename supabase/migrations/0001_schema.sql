-- Emendamentopoli: Supabase schema
-- Run in the Supabase SQL Editor (or: npx supabase db push)

create table if not exists emendamenti (
  id text primary key,                -- dati.senato.it URI
  legislatura int not null,
  numero text,                        -- e.g. "1.30"
  tipo text,                         -- E = amendment, G = order of the day
  sede_commissione boolean,
  atto_uri text,                     -- item under discussion (bill)
  ddl text,
  url_testo text,
  url_testo_xml text,
  data date,                         -- submission date
  testo text,
  created_at timestamptz not null default now()
);

create table if not exists valutazioni (
  id bigint generated always as identity primary key,
  emendamento_id text not null references emendamenti(id) on delete cascade,
  score_totale numeric(4,2),
  riepilogo text,
  modello text,                      -- LLM model used (or "heuristic")
  ts timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists punteggi (
  id bigint generated always as identity primary key,
  valutazione_id bigint not null references valutazioni(id) on delete cascade,
  criterio text not null,
  punteggio numeric(4,2),
  motivazione text
);

create index if not exists idx_valutazioni_emendamento on valutazioni(emendamento_id);
create index if not exists idx_punteggi_valutazione on punteggi(valutazione_id);
create index if not exists idx_emendamenti_data on emendamenti(data);

-- RLS: public prototype, anonymous read/write.
-- REFINE BEFORE ANY REAL PRODUCTION USE.
alter table emendamenti enable row level security;
alter table valutazioni enable row level security;
alter table punteggi enable row level security;

drop policy if exists "public read emendamenti" on emendamenti;
drop policy if exists "public write emendamenti" on emendamenti;
drop policy if exists "public read valutazioni" on valutazioni;
drop policy if exists "public write valutazioni" on valutazioni;
drop policy if exists "public read punteggi" on punteggi;
drop policy if exists "public write punteggi" on punteggi;

create policy "public read emendamenti" on emendamenti for select using (true);
create policy "public write emendamenti" on emendamenti for all using (true) with check (true);
create policy "public read valutazioni" on valutazioni for select using (true);
create policy "public write valutazioni" on valutazioni for all using (true) with check (true);
create policy "public read punteggi" on punteggi for select using (true);
create policy "public write punteggi" on punteggi for all using (true) with check (true);
