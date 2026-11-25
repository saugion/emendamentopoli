begin;
drop index if exists idx_emendamenti_padre;
alter table emendamenti drop column if exists emendamento_padre_id, drop column if exists numero_padre;
commit;
