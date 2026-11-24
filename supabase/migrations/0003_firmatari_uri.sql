-- Emendamentopoli: URI del profilo senato.it dei firmatari
-- (stesso ordine della colonna firmatari, stringa vuota se ignoto)
-- Eseguire nel SQL Editor di Supabase dopo 0002_firmatari.sql

alter table emendamenti
  add column if not exists firmatari_uri text;  -- "https://www.senato.it/leg/19/BGT/Schede/Attsen/00029521.htm, , ..."
