-- Emendamentopoli 0011: indici per le query della web app (caricamento lista + ricerca)
-- Run in the Supabase SQL Editor (or: npx supabase db push) after 0010_idx_numero_padre.sql
begin;

create extension if not exists pg_trgm;

-- 1. caricamento pagina (store.search senza q): order=score_totale.desc.nullslast con
--    count=exact su valutazioni!inner(emendamenti.corrente) -> senza indice il planner
--    ordina l'intero join e va in statement timeout (57014) a tabella piena.
--    L'indice serve anche al filtro score_totale=gte.X (minScore, count stats "hi")
create index if not exists idx_valutazioni_score
  on valutazioni (score_totale desc nulls last);

-- 2. stats: ultima valutazione (order=ts.desc.nullslast limit=1)
create index if not exists idx_valutazioni_ts
  on valutazioni (ts desc nulls last);

-- 3. filtro tipo (lista e count degli stats) ristretto alle sole versioni correnti
create index if not exists idx_emendamenti_tipo_corrente
  on emendamenti (tipo) where corrente;

-- 4. ordinamento "Recenti" (order=emendamenti(data).desc.nullslast);
--    idx_emendamenti_data (0001) copre gia' data.asc.nullslast
create index if not exists idx_emendamenti_data_desc
  on emendamenti (data desc nulls last);

-- 5. ricerca testuale (store.searchOrClause): ilike *q* su testo/ddl/numero/riepilogo.
--    Senza indici trigram e' seq scan sul testo completo (TOAST) -> statement timeout
--    (57014). firmatari e' gia' indicizzato in 0002. testo e' la colonna pesante:
--    indice partial sulle sole versioni correnti (la ricerca filtra sempre
--    corrente=eq.true quando la migrazione 0004 e' applicata)
create index if not exists idx_emendamenti_testo_trgm
  on emendamenti using gin (testo gin_trgm_ops) where corrente;
create index if not exists idx_emendamenti_ddl_trgm
  on emendamenti using gin (ddl gin_trgm_ops);
create index if not exists idx_emendamenti_numero_trgm
  on emendamenti using gin (numero gin_trgm_ops);
create index if not exists idx_valutazioni_riepilogo_trgm
  on valutazioni using gin (riepilogo gin_trgm_ops);

commit;
