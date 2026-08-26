-- ═══════════════════════════════════════════════════════════════════════════
--  Statussen-herziening  —  DEEL 3: de definitieve flows
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: er wordt niets verwijderd, alleen kolommen en checks
--  bijgewerkt. De huidige live versie blijft er gewoon naast werken.
--
--  Leads:   demo_bouwen -> demo_aanleveren -> demo_gezien
--  Klanten: factuur_gestuurd -> factuur_betaald -> website_deployed
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. LEADS ───────────────────────────────────────────────────────────────
-- 'demo_doorsturen' verdwijnt: Timon en Levi bouwen de demo en Julian levert
-- hem aan, dus een aparte doorstuurstap voegde niets toe.
alter table warm_leads add column if not exists aanlever_doc text;

update warm_leads set lead_status = 'demo_aanleveren'
 where lead_status = 'demo_doorsturen';

alter table warm_leads drop constraint if exists lead_status_check;
alter table warm_leads add  constraint lead_status_check check (lead_status in
  ('demo_bouwen','demo_aanleveren','demo_gezien','afgehaakt'));

-- ── 2. KLANTEN ─────────────────────────────────────────────────────────────
-- Eigen kolom naast demo_status, zodat de oude waarden blijven staan voor de
-- historie en de nieuwe flow schoon begint.
alter table clients add column if not exists client_status text not null default 'factuur_gestuurd';

alter table clients drop constraint if exists client_status_check;
alter table clients add  constraint client_status_check check (client_status in
  ('factuur_gestuurd','factuur_betaald','website_deployed','afgehaakt'));

create index if not exists idx_client_status on clients (client_status);

-- Bestaande klanten die al betaald hebben meteen op de juiste stap zetten,
-- zodat er geen taak verschijnt voor iets dat allang gebeurd is.
update clients set client_status = 'factuur_betaald'
 where paid_at is not null and client_status = 'factuur_gestuurd';

-- ── 3. STATUS-LOG ──────────────────────────────────────────────────────────
-- De log kende alleen prospect, lead en client; dat dekt de nieuwe flows al.
-- Niets te doen, staat hier alleen zodat het overzicht compleet is.
