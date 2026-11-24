-- Emendamentopoli 0006: money-relevance pre-filter, rubric v2 financial fields
begin;

alter table emendamenti add column if not exists rilevante_spesa boolean;
alter table emendamenti add column if not exists rilevanza_motivo text[] not null default '{}';
alter table emendamenti add constraint ck_emendamenti_rilevanza_motivo check (
  rilevanza_motivo <@ array['atto_dl', 'atto_bilancio', 'atto_bilancio_interno', 'atto_commissione_bilancio',
                            'testo_spesa_forte', 'testo_spesa_debole']::text[]);
comment on column emendamenti.rilevante_spesa is 'Pre-filter result (rule R1/R2/R3 of the spec); null = not computed yet';
comment on column emendamenti.rilevanza_motivo is 'Raw signals found (not only the ones that made the item relevant)';
create index if not exists idx_emendamenti_rilevanti on emendamenti (rilevante_spesa) where corrente;

alter table valutazioni add column if not exists impatto_finanziario numeric(4,2)
  check (impatto_finanziario is null or (impatto_finanziario >= 0 and impatto_finanziario <= 10));
alter table valutazioni add column if not exists alloca_risorse boolean;
alter table valutazioni add column if not exists importo_stimato_eur numeric(18,2)
  check (importo_stimato_eur is null or importo_stimato_eur >= 0);
alter table valutazioni add column if not exists motivazione_finanziaria text;
comment on column valutazioni.impatto_finanziario is 'Rubric v2 only, 0-10, NOT part of score_totale';

commit;
