-- Emendamentopoli 0014: RPC per la coda "emendamenti senza valutazione" dei worker
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0013_eval_queue_index.sql
begin;

-- La forma REST (select=...,valutazioni() + Resource=is.null) genera
-- LEFT JOIN LATERAL + filtro IS NULL con le colonne pesanti (testo) in output:
-- il planner legge l'heap di ogni candidato (~18k righe larghe) e fa una sonda
-- laterale per riga -> ~8s, sopra statement_timeout=3s del ruolo anon -> 57014.
-- La funzione invece fa prima lo scan index-only dei soli candidati (indice
-- partial 0013 con INCLUDE(id)) con NOT EXISTS index-only, e recupera l'heap
-- solo per le righe selezionate (<= p_limit). Le colonne sono rinominate con gli
-- alias inglesi del modello Amendment, cosi' il worker mappa le righe direttamente.
create or replace function public.queue_senza_valutazione(p_limit int default 500)
returns table (
  "id" text,
  "legislature" int,
  "number" text,
  "type" text,
  "committee_seat" boolean,
  "act_uri" text,
  "bill" text,
  "text_url" text,
  "text_xml_url" text,
  "date" date,
  "text" text,
  "chamber" text,
  "relevant_spending" boolean
)
language sql
stable
set search_path = public
as $$
  with q as materialized (
    select e.id
    from public.emendamenti e
    where e.corrente
      and e.rilevante_spesa
      and e.testo is not null
      and not exists (select 1 from public.valutazioni v where v.emendamento_id = e.id)
    order by e.data desc nulls last
    limit p_limit
  )
  select e.id, e.legislatura, e.numero, e.tipo, e.sede_commissione, e.atto_uri,
         e.ddl, e.url_testo, e.url_testo_xml, e.data, e.testo, e.camera, e.rilevante_spesa
  from public.emendamenti e
  join q on q.id = e.id
  order by e.data desc nulls last
$$;

grant execute on function public.queue_senza_valutazione(int) to anon, authenticated;

commit;
