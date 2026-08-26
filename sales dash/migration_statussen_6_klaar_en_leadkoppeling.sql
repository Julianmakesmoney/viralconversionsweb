-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 6: koppeling lead -> klant, en de status 'klaar'
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: er verdwijnt geen data.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. lead_id op clients ──────────────────────────────────────────────────
-- Deze kolom ontbrak, waardoor het aanmaken van een klant bij 'demo gezien'
-- stilletjes mislukte: de code zocht op clients.lead_id, kreeg een fout, en
-- die werd opgevangen zonder melding. De lead bleef daardoor in de warm
-- leads-tab staan en er kwam nooit een klantkaart.
alter table clients add column if not exists lead_id text;
create index if not exists idx_client_lead on clients (lead_id);

-- ── 2. Nieuwe eindstatus 'klaar' ───────────────────────────────────────────
-- Geen taak, maar het eindpunt: deze klant is helemaal geregeld.
alter table clients drop constraint if exists client_status_check;
alter table clients add  constraint client_status_check check (client_status in
  ('factuur_gestuurd','factuur_betaald','commissie_uitbetaald',
   'website_deployed','klaar','afgehaakt'));

-- ── 3. Bestaande leads die al klant zijn losmaken ──────────────────────────
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
