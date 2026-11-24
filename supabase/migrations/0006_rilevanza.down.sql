begin;
alter table valutazioni drop column if exists motivazione_finanziaria, drop column if exists importo_stimato_eur,
  drop column if exists alloca_risorse, drop column if exists impatto_finanziario;
drop index if exists idx_emendamenti_rilevanti;
alter table emendamenti drop column if exists rilevanza_motivo, drop column if exists rilevante_spesa;
commit;
