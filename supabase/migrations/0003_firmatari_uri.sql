-- Emendamentopoli: senato.it profile URIs of the signatories
-- (same order as the firmatari column, empty string if unknown)
-- Run in the Supabase SQL Editor after 0002_firmatari.sql

alter table emendamenti
  add column if not exists firmatari_uri text;  -- "https://www.senato.it/leg/19/BGT/Schede/Attsen/00029521.htm, , ..."
