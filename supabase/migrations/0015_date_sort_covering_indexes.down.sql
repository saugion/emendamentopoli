begin;
drop index if exists idx_emendamenti_camera_corrente_tipo_data_asc;
drop index if exists idx_emendamenti_camera_corrente_tipo_data_desc;
drop index if exists idx_emendamenti_data_desc;
create index if not exists idx_emendamenti_data_desc
  on emendamenti (data desc nulls last);
commit;
