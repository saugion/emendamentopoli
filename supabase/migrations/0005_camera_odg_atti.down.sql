begin;
delete from emendamenti where camera = 'camera';       -- cascades to valutazioni/punteggi
drop table if exists ingest_cursori;
drop table if exists atti;
drop index if exists idx_emendamenti_atto;
drop index if exists idx_emendamenti_testo_hash;
drop index if exists idx_emendamenti_chiave;
drop index if exists idx_emendamenti_camera_corrente;
alter table emendamenti drop column if exists aggiornato_at, drop column if exists gruppo_identici,
  drop column if exists versione_etichetta, drop column if exists fase_testo, drop column if exists metadati_hash,
  drop column if exists fonte_modificata, drop column if exists eurovoc, drop column if exists iter,
  drop column if exists parere_governo, drop column if exists esito, drop column if exists firmatari_ocd,
  drop column if exists fonte_uri;
alter table emendamenti drop constraint if exists ck_emendamenti_camera;
alter table emendamenti drop column if exists camera;
commit;
