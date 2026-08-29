-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 8: per teamlid instellen welke taken hij krijgt
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: er verdwijnt geen data.
--
--  Tot nu toe lag de verdeling vast in de code: demo_flow 'zelf' betekende
--  dat iemand zijn eigen demo bouwde en de site live zette, 'julian' dat
--  Julian alles deed. Verder viel er niets te schuiven. Naarmate mensen meer
--  ervaring krijgen moet je ze stap voor stap meer kunnen geven, dus staat
--  het nu per status in een eigen veld.
--
--  Vorm: {"demo_bouwen": true, "factuur_gestuurd": false, ...}
--  true  = dit teamlid doet die stap zelf
--  false = Julian doet hem
--  ontbreekt = terugvallen op demo_flow, zodat bestaande leden niets merken
-- ═══════════════════════════════════════════════════════════════════════════

alter table sales_members add column if not exists taken_rechten jsonb not null default '{}'::jsonb;

-- De huidige verdeling vastleggen, zodat wat er nu geldt ook zichtbaar wordt
-- in het portaal in plaats van verstopt te zitten in demo_flow.
update sales_members
   set taken_rechten = jsonb_build_object(
         'demo_bouwen',          (coalesce(demo_flow, 'zelf') = 'zelf'),
         'demo_aanleveren',      false,
         'demo_gezien',          false,
         'factuur_gestuurd',     false,
         'factuur_betaald',      false,
         'commissie_uitbetaald', false,
         'website_deployed',     (coalesce(demo_flow, 'zelf') = 'zelf'),
         'klaar',                false)
 where taken_rechten = '{}'::jsonb;

-- Julian doet per definitie alles; bij hem valt er niets te delegeren.
update sales_members
   set taken_rechten = jsonb_build_object(
         'demo_bouwen', true, 'demo_aanleveren', true, 'demo_gezien', true,
         'factuur_gestuurd', true, 'factuur_betaald', true,
         'commissie_uitbetaald', true, 'website_deployed', true, 'klaar', true)
 where lower(email) = 'julian@viralconversions.io';
