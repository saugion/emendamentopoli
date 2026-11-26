-- Emendamentopoli 0015: indici per l'ordinamento per data della lista web app
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0014_queue_senza_valutazione_rpc.sql
begin;

-- Per sort data_asc/data_desc lo store interroga emendamenti come ROOT
-- (order=data.* sulla root, non sulla colonna embedded: cosi' il planner usa
-- l'indice ed early-stop a LIMIT). Senza filtri gli indici plain su data
-- bastano (scan in ordine, stop a LIMIT: ~30 heap fetch). Con filtri
-- (camera/tipo) pero' lo scan plain legge heap per ogni entry (~54k fetch
-- -> ~7s, sopra statement_timeout=3s del ruolo anon -> 57014): i due indici
-- compositi (camera, corrente, tipo, data) seekano i filtri e restano in
-- ordine per data, con le colonne della vista lista in INCLUDE (index-only).
drop index if exists idx_emendamenti_data_desc;

create index if not exists idx_emendamenti_data_desc
  on emendamenti (data desc nulls last);

create index if not exists idx_emendamenti_camera_corrente_tipo_data_desc
  on emendamenti (camera, corrente, tipo, data desc nulls last)
  include (id, numero, ddl, esito, numero_padre, url_testo, firmatari, firmatari_uri);

create index if not exists idx_emendamenti_camera_corrente_tipo_data_asc
  on emendamenti (camera, corrente, tipo, data asc nulls last)
  include (id, numero, ddl, esito, numero_padre, url_testo, firmatari, firmatari_uri);

commit;
