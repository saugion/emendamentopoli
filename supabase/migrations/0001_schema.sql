-- Emendamentopoli: schema Supabase
-- Eseguire nel SQL Editor di Supabase (o: npx supabase db push)

create table if not exists emendamenti (
  id text primary key,                -- URI dati.senato.it
  legislatura int not null,
  numero text,                        -- es. "1.30"
  tipo text,                         -- E = emendamento, G = ordine del giorno
  sede_commissione boolean,
  atto_uri text,                     -- oggetto della trattazione (ddl)
  ddl text,
  url_testo text,
  url_testo_xml text,
  data date,                        -- data di presentazione
  testo text,
  created_at timestamptz not null default now()
);

create table if not exists valutazioni (
  id bigint generated always as identity primary key,
  emendamento_id text not null references emendamenti(id) on delete cascade,
  score_totale numeric(4,2),
  riepilogo text,
  modello text,                      -- modello LLM usato (o "euristica")
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

-- RLS: prototipo pubblico, lettura/scrittura anonima.
-- DA RAFFINARE PRIMA DI UNA PRODUZIONE REALE.
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
create policy "public write emendamenti" on emendamenti for all with check (true);
create policy "public read valutazioni" on valutazioni for select using (true);
create policy "public write valutazioni" on valutazioni for all with check (true);
create policy "public read punteggi" on punteggi for select using (true);
create policy "public write punteggi" on punteggi for all with check (true);
