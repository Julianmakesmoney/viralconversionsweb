-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 5: commissie uitbetalen als eigen stap
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: alleen de check wordt verruimd, er verdwijnt geen data.
--
--  De klantflow wordt:
--    factuur_gestuurd -> factuur_betaald -> commissie_uitbetaald -> website_deployed
--
--  Commissie uitbetalen zat er eerst niet in, waardoor niet bij te houden was
--  aan wie er al betaald is. Nu is het een eigen stap tussen het binnenkomen
--  van het geld en het live zetten van de website.
-- ═══════════════════════════════════════════════════════════════════════════

alter table clients drop constraint if exists client_status_check;
alter table clients add  constraint client_status_check check (client_status in
  ('factuur_gestuurd','factuur_betaald','commissie_uitbetaald','website_deployed','afgehaakt'));

-- Klanten waarvan de commissie al is uitbetaald maar die nog op 'betaald'
-- staan meteen op de juiste stap zetten, zodat er geen taak verschijnt voor
-- iets dat al gebeurd is.
update clients set client_status = 'commissie_uitbetaald'
 where commission_paid_at is not null
   and client_status = 'factuur_betaald';
