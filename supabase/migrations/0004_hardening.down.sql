begin;
drop function if exists ricalcola_versioni(text[]);
alter table emendamenti drop constraint if exists uq_emendamenti_chiave_ordine;
drop index if exists idx_emendamenti_corrente;
alter table emendamenti drop column if exists testo_hash;
alter table emendamenti drop column if exists superato_da;
alter table emendamenti drop column if exists corrente;
alter table emendamenti drop column if exists versione;
alter table emendamenti drop column if exists ordine_versione;
alter table emendamenti drop column if exists chiave_naturale;
drop index if exists idx_valutazioni_testo_hash;
drop index if exists uq_valutazioni_emendamento_rubrica;
alter table valutazioni drop column if exists copiata_da;
alter table valutazioni drop column if exists testo_hash;
alter table valutazioni drop column if exists rubrica_versione;
commit;
-- deleted duplicate valutazioni are NOT restored (there were 0 on 2026-11-24)
