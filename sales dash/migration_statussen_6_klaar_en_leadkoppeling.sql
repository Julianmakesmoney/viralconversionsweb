-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 6: koppeling lead -> klant, en de status 'klaar'
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Er verdwijnt geen data; één kolom krijgt een ander type omdat hij leeg is.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. added_by_id rechttrekken ────────────────────────────────────────────
-- clients.added_by_id stond op uuid, maar de id's die erin horen komen uit
-- sales_members en zien eruit als 1777063849963. Die passen daar niet in, dus
-- de kolom bleef altijd leeg en viel de omzet bij niemand toe. Alle vier de
-- bestaande rijen zijn leeg, dus het type omzetten kost niets.
alter table clients
  alter column added_by_id type text using added_by_id::text;

-- ── 2. lead_id op clients ──────────────────────────────────────────────────
-- Deze kolom ontbrak, waardoor het aanmaken van een klant bij 'demo gezien'
-- stilletjes mislukte: de code zocht op clients.lead_id, kreeg een fout, en
-- die werd opgevangen zonder melding. De lead bleef daardoor in de warm
-- leads-tab staan en er kwam nooit een klantkaart.
alter table clients add column if not exists lead_id text;
create index if not exists idx_client_lead on clients (lead_id);

-- ── 3. Nieuwe eindstatus 'klaar' ───────────────────────────────────────────
-- Geen taak, maar het eindpunt: deze klant is helemaal geregeld.
alter table clients drop constraint if exists client_status_check;
alter table clients add  constraint client_status_check check (client_status in
  ('factuur_gestuurd','factuur_betaald','commissie_uitbetaald',
   'website_deployed','klaar','afgehaakt'));

-- ── 4. Bestaande leads die al klant zijn losmaken ──────────────────────────
-- Leads op 'demo gezien' horen in de klantentab thuis, niet meer in warm
-- leads. Voor rijen die nog geen klantkaart hebben maken we die alsnog aan.
-- clients.id is een uuid met een eigen default, dus die laten we met rust.
insert into clients (lead_id, name, phone, maps_url, added_by_id, added_by_name,
                     client_status, notes, status_at, created_at)
select
  w.id,
  w.company_name,
  coalesce(w.phone, ''),
  coalesce(w.maps_url, ''),
  w.added_by_id,
  w.added_by_name,
  'factuur_gestuurd',
  coalesce(w.notes, ''),
  now(),
  now()
from warm_leads w
where w.lead_status = 'demo_gezien'
  and not exists (select 1 from clients c where c.lead_id = w.id);

-- En ze daarna markeren als verhuisd, zodat ze uit de warm leads-lijst vallen.
update warm_leads
   set status = 'closed'
 where lead_status = 'demo_gezien'
   and coalesce(status, '') <> 'closed';

-- ── 5. Oude klanten alsnog aan hun verkoper hangen ─────────────────────────
-- De vier bestaande klanten hebben wel een added_by_name maar geen id, dus
-- hun omzet viel bij niemand. Op naam terugzoeken lost dat op.
update clients c
   set added_by_id = m.id
  from sales_members m
 where c.added_by_id is null
   and c.added_by_name is not null
   and lower(trim(c.added_by_name)) = lower(trim(m.name));
