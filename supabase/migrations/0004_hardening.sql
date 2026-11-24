-- Emendamentopoli 0004: hardening (idempotent evaluations, natural keys, versions)
-- Run in the Supabase SQL Editor after 0003_firmatari_uri.sql

begin;

-- 1. valutazioni: rubric version, text hash, provenance of copied evaluations
alter table valutazioni add column if not exists rubrica_versione smallint not null default 1;
alter table valutazioni add column if not exists testo_hash text;
alter table valutazioni add column if not exists copiata_da bigint references valutazioni(id) on delete set null;

-- 2. remove duplicate evaluations: keep the most recent ts (nulls last), then the highest id
delete from valutazioni v
using (
  select id,
         row_number() over (partition by emendamento_id, rubrica_versione
                            order by ts desc nulls last, id desc) as rn
  from valutazioni
) d
where v.id = d.id and d.rn > 1;

create unique index if not exists uq_valutazioni_emendamento_rubrica
  on valutazioni (emendamento_id, rubrica_versione);
create index if not exists idx_valutazioni_testo_hash on valutazioni (testo_hash);

-- 3. emendamenti: natural key and versions
alter table emendamenti add column if not exists chiave_naturale text;
alter table emendamenti add column if not exists ordine_versione bigint not null default 0;
alter table emendamenti add column if not exists versione int not null default 1;
alter table emendamenti add column if not exists corrente boolean not null default false;
alter table emendamenti add column if not exists superato_da text references emendamenti(id) on delete set null;
alter table emendamenti add column if not exists testo_hash text;

comment on column emendamenti.chiave_naturale is 'Stable identity across versions, e.g. senato:19:1521998:G:A:G4.300 or camera:19:odg:9/03076/003';
comment on column emendamenti.ordine_versione is 'Ordering key inside chiave_naturale: Senate numeric URI id, Camera ODG tab version, Camera emendamento position YYYYMMDD';
comment on column emendamenti.versione is '1..n inside chiave_naturale, computed by ricalcola_versioni()';
comment on column emendamenti.corrente is 'true only for the highest ordine_versione of each chiave_naturale, computed by ricalcola_versioni()';
comment on column emendamenti.superato_da is 'id of the immediately following version, null for the current one';

-- 4. backfill Senate natural keys
update emendamenti set
  chiave_naturale = 'senato:' || legislatura || ':' || regexp_replace(atto_uri, '^.*/', '') || ':'
                    || coalesce(tipo, '?') || ':' || case when coalesce(sede_commissione, false) then 'C' else 'A' end || ':'
                    || upper(regexp_replace(btrim(numero), '\s+', ' ', 'g')),
  ordine_versione = regexp_replace(id, '^.*/', '')::bigint
where id like 'http://dati.senato.it/emendamento/%' and chiave_naturale is null;

alter table emendamenti add constraint uq_emendamenti_chiave_ordine unique (chiave_naturale, ordine_versione);
create index if not exists idx_emendamenti_corrente on emendamenti (corrente);

-- 5. version computation (called by the workers through PostgREST RPC after every save)
create or replace function ricalcola_versioni(p_chiavi text[])
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  n integer;
begin
  with r as (
    select id,
           row_number() over w as v,
           count(*) over (partition by chiave_naturale) as tot,
           lead(id) over w as nxt
    from emendamenti
    where chiave_naturale = any (p_chiavi)
    window w as (partition by chiave_naturale order by ordine_versione, id)
  )
  update emendamenti e
     set versione = r.v,
         corrente = (r.v = r.tot),
         superato_da = r.nxt
    from r
   where e.id = r.id
     and (e.versione is distinct from r.v
          or e.corrente is distinct from (r.v = r.tot)
          or e.superato_da is distinct from r.nxt);
  get diagnostics n = row_count;
  return n;
end
$$;

grant execute on function ricalcola_versioni(text[]) to anon, authenticated;

select ricalcola_versioni(array(
  select distinct chiave_naturale from emendamenti where chiave_naturale like 'senato:%'
));

commit;
