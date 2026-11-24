-- Emendamentopoli 0007: Camera emendamenti (change detection state of the list pages)
begin;
create table if not exists camera_liste_emendamenti (
  url text primary key,
  legislatura int not null,
  atto_numero text not null,
  sede text not null,                    -- ass | com08ref | ...
  last_modified text,                    -- raw Last-Modified header of the last 200 response
  righe int,                             -- rows parsed at the last 200 response
  controllato_at timestamptz,            -- last request (200 or 304)
  scoperto_at timestamptz not null default now()
);
create index if not exists idx_camera_liste_atto on camera_liste_emendamenti (atto_numero);
alter table camera_liste_emendamenti enable row level security;
drop policy if exists "public read camera_liste_emendamenti" on camera_liste_emendamenti;
drop policy if exists "public write camera_liste_emendamenti" on camera_liste_emendamenti;
create policy "public read camera_liste_emendamenti" on camera_liste_emendamenti for select using (true);
create policy "public write camera_liste_emendamenti" on camera_liste_emendamenti for all using (true) with check (true);
commit;
