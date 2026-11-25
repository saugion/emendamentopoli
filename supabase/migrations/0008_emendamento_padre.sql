-- Emendamentopoli 0008: link subemendamenti (proposte di modifica) -> emendamento padre
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0007_camera_emendamenti.sql
begin;

alter table emendamenti add column if not exists numero_padre text;
alter table emendamenti add column if not exists emendamento_padre_id text references emendamenti(id) on delete set null;

comment on column emendamenti.numero_padre is 'Numero dell''emendamento padre per i subemendamenti (proposta di modifica, numero "X/N"): "X"; null per gli emendamenti base';
comment on column emendamenti.emendamento_padre_id is 'id (URI dati.senato.it) dell''emendamento padre, risolto via SPARQL dall''atto + sede + numero';

create index if not exists idx_emendamenti_padre on emendamenti(emendamento_padre_id);

commit;
