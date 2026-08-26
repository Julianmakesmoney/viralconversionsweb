-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 7: opvolgen, website-info en factuurgegevens
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: er verdwijnt geen data.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Opvolgen bij 'demo gezien?' ─────────────────────────────────────────
-- Gezien en geghost zijn geen eindpunt: de taak blijft staan, maar wel oranje
-- zodat je ziet dat er al een poging in zit. Hier staat welke van de twee het
-- was en wanneer, zodat je weet hoe lang het al loopt.
alter table warm_leads add column if not exists opvolg_status text;
alter table warm_leads add column if not exists opvolg_at     timestamptz;

alter table warm_leads drop constraint if exists lead_opvolg_check;
alter table warm_leads add  constraint lead_opvolg_check check (
  opvolg_status is null or opvolg_status in ('gezien', 'geghost'));

-- Zelfde voor klanten, bij 'factuur betaald?'.
alter table clients add column if not exists opvolg_status text;
alter table clients add column if not exists opvolg_at     timestamptz;

alter table clients drop constraint if exists client_opvolg_check;
alter table clients add  constraint client_opvolg_check check (
  opvolg_status is null or opvolg_status in ('nog_niet'));

-- ── 2. Info om de website af te maken ──────────────────────────────────────
-- Julian schrijft dit bij het uitbetalen van de commissie; degene die de site
-- live zet leest het bij de volgende stap.
alter table clients add column if not exists website_info text;

-- ── 3. Factuurgegevens ─────────────────────────────────────────────────────
-- Wat er op de factuur komt te staan, zodat je hem niet elke keer opnieuw
-- hoeft in te tikken en je hem later kunt terugvinden.
alter table clients add column if not exists factuur_nummer   text;
alter table clients add column if not exists factuur_datum    date;
alter table clients add column if not exists factuur_regels   jsonb;
alter table clients add column if not exists factuur_gegevens jsonb;
