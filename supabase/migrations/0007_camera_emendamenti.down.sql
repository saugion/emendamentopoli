begin;
drop table if exists camera_liste_emendamenti;
delete from emendamenti where camera='camera' and tipo='E';
commit;
