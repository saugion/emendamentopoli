-- Emendamentopoli 0008: link subemendamenti (proposte di modifica) -> emendamento padre
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0007_camera_emendamenti.sql
begin;

alter table emendamenti add column if not exists numero_padre text;
alter table emendamenti add column if not exists emendamento_padre_id text references emendamenti(id) on delete set null;

comment on column emendamenti.numero_padre is 'Numero dell''emendamento padre per i subemendamenti (proposta di modifica, numero "X/N"): "X"; null per gli emendamenti base';
comment on column emendamenti.emendamento_padre_id is 'id (URI dati.senato.it) dell''emendamento padre, risolto via SPARQL dall''atto + sede + numero';

create index if not exists idx_emendamenti_padre on emendamenti(emendamento_padre_id);

-- backfill dei record esistenti (reconciliazione senza re-ingest):
-- 1. numero_padre dal numero "X/N" (solo Senado, tipo E; gli o.d.g. G/912/1/5 hanno slash nativi),
--    normalizzato come in chiave_naturale (upper, spazi collapsati), senza l'ultimo segmento "/N"
update emendamenti
set numero_padre = upper(regexp_replace(regexp_replace(btrim(numero), '\s+', ' ', 'g'), '/[^/]+$', ''))
where chiave_naturale like 'senato:%'
  and tipo = 'E'
  and numero like '%/%'
  and numero_padre is null;

-- 2. emendamento_padre_id via self-join sulla chiave_naturale del padre (stesso atto + tipo + sede +
--    numero_padre); a parita' di chiave si linka la versione 1 (ordine_versione minimo = originale)
with padre as (
  select chiave_naturale, id
  from (
    select chiave_naturale, id,
           row_number() over (partition by chiave_naturale order by ordine_versione, id) as rn
    from emendamenti
    where chiave_naturale like 'senato:%'
  ) t
  where rn = 1
)
update emendamenti e
set emendamento_padre_id = p.id
from padre p
where e.numero_padre is not null
  and e.emendamento_padre_id is null
  and p.chiave_naturale = 'senato:' || e.legislatura || ':' || regexp_replace(e.atto_uri, '^.*/', '')
        || ':' || coalesce(e.tipo, '?') || ':'
        || case when coalesce(e.sede_commissione, false) then 'C' else 'A' end
        || ':' || upper(regexp_replace(btrim(e.numero_padre), '\s+', ' ', 'g'));

-- 3. riepilogo: quanti subemendamenti sono stati reconciliati e quanti linkati
select
  count(*) filter (where numero_padre is not null) as subemendamenti,
  count(*) filter (where numero_padre is not null and emendamento_padre_id is not null) as con_padre_linkato,
  count(*) filter (where numero_padre is not null and emendamento_padre_id is null) as senza_padre
from emendamenti
where chiave_naturale like 'senato:%';

commit;
