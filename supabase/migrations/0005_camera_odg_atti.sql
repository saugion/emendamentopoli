-- Emendamentopoli 0005: Camera dei deputati (ODG), atti, ingest cursors
-- Run after 0004_hardening.sql

begin;

-- emendamenti: source chamber and Camera-specific metadata
alter table emendamenti add column if not exists camera text not null default 'senato';
alter table emendamenti add constraint ck_emendamenti_camera check (camera in ('senato', 'camera'));
alter table emendamenti add column if not exists fonte_uri text;               -- ocd:aic URI (Camera ODG) / list URL (Camera emendamenti)
alter table emendamenti add column if not exists firmatari_ocd text;           -- "http://dati.camera.it/ocd/deputato.rdf/d307726_19, ..." aligned with firmatari ("" if unknown)
alter table emendamenti add column if not exists esito text;
alter table emendamenti add constraint ck_emendamenti_esito check (esito is null or esito in (
  'approvato', 'respinto', 'accolto', 'accolto_come_raccomandazione', 'ritirato', 'inammissibile',
  'decaduto', 'assorbito', 'precluso', 'irricevibile', 'riammesso', 'in_corso', 'altro'));
alter table emendamenti add column if not exists parere_governo text;
alter table emendamenti add constraint ck_emendamenti_parere check (parere_governo is null or parere_governo in (
  'accolto', 'non_accolto', 'accolto_come_raccomandazione', 'proposta_riformulazione'));
alter table emendamenti add column if not exists iter jsonb;                   -- [{"fase": "PARERE GOVERNO", "data": "2026-10-21"}, ...] in page order
alter table emendamenti add column if not exists eurovoc text[];               -- dcterms:subject URIs, sorted
alter table emendamenti add column if not exists fonte_modificata timestamptz; -- ods:modified
alter table emendamenti add column if not exists metadati_hash text;           -- sha256 of SPARQL-level fields (null = refetch on next run)
alter table emendamenti add column if not exists fase_testo text;              -- text stage of the bill: A, AR, B, ... (from the identifier)
alter table emendamenti add column if not exists versione_etichetta text;      -- e.g. "Testo Allegato A del 21 Ottobre 2026 (Testo modificato nel corso della seduta)"
alter table emendamenti add column if not exists gruppo_identici text;         -- Phase 4
alter table emendamenti add column if not exists aggiornato_at timestamptz not null default now();

comment on column emendamenti.camera is 'senato | camera';

create index if not exists idx_emendamenti_camera_corrente on emendamenti (camera, corrente);
create index if not exists idx_emendamenti_chiave on emendamenti (chiave_naturale);
create index if not exists idx_emendamenti_testo_hash on emendamenti (testo_hash);
create index if not exists idx_emendamenti_atto on emendamenti (atto_uri);

-- atti (Camera bills), Phase 3 fills it
create table if not exists atti (
  uri text primary key,                         -- http://dati.camera.it/ocd/attocamera.rdf/ac19_3133
  legislatura int not null,
  numero text not null,                         -- dc:identifier: "3133", "643-bis", "1038-B", "536-891-910-B"
  titolo text not null,                         -- cleaned (see camera_atti.clean_title)
  titolo_raw text not null,                     -- dc:title as published
  data_presentazione date,
  iniziativa text,                              -- ocd:iniziativa: Governo, Parlamentare, Regioni, CNEL, Popolare, Mista (...)
  natura text,                                  -- last segment of ocd:rif_natura, e.g. disegno_legge_ordinario
  organi_referenti text[] not null default '{}',-- ocd:rif_organo URIs of assegnazioni with ocd:sede "Referente"
  is_dl_conversione boolean not null default false,
  is_bilancio boolean not null default false,
  is_commissione_bilancio_referente boolean not null default false,
  url_scheda text not null,                     -- https://www.camera.it/leg19/126?tab=1&leg=19&idDocumento=<numero>
  fonte_modificata timestamptz,
  aggiornato_at timestamptz not null default now(),
  unique (legislatura, numero)
);
comment on table atti is 'Camera bills (ocd:atto) of the legislature; source dati.camera.it CC BY-SA 4.0';

-- ingest cursors (one row per cursor name)
create table if not exists ingest_cursori (
  nome text primary key,                        -- camera_odg_modified | camera_atti_modified | camera_eme_atti_modified
  valore text not null,                         -- ISO 8601 UTC, e.g. 2026-11-24T13:32:57Z
  aggiornato_at timestamptz not null default now()
);

alter table atti enable row level security;
alter table ingest_cursori enable row level security;
drop policy if exists "public read atti" on atti;
drop policy if exists "public write atti" on atti;
drop policy if exists "public read ingest_cursori" on ingest_cursori;
drop policy if exists "public write ingest_cursori" on ingest_cursori;
-- RLS: public prototype, anonymous read/write, same as 0001. REFINE BEFORE ANY REAL PRODUCTION USE.
create policy "public read atti" on atti for select using (true);
create policy "public write atti" on atti for all using (true) with check (true);
create policy "public read ingest_cursori" on ingest_cursori for select using (true);
create policy "public write ingest_cursori" on ingest_cursori for all using (true) with check (true);

commit;
