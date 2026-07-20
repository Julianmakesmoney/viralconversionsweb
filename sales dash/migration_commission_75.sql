-- migration_commission_75.sql
-- Globaal commissietarief 75% + Hermes-lead attributie (terugwerkend).
--
-- Draai dit ÉÉN keer in de Supabase SQL-editor, in DEZE volgorde (1 → 2 → 3).
-- ⚠️ Dit wijzigt ECHTE, historische commissiebedragen. Maak eerst een backup /
--    export van warm_leads (en clients) voordat je dit draait.
--
-- Context:
--  * De code gebruikt nu GLOBAL_COMMISSION_RATE = 0.75 voor elke sales-member.
--    Julian Verboom (owner) blijft 0%. Een handmatige commission_override per
--    member wint nog steeds (server-side).
--  * Hermes-warm-leads worden voortaan bij aanmaak al aan de starter gekoppeld
--    (added_by_id). Bestaande Hermes-leads hebben added_by_id NULL en alleen de
--    naam in added_by_name ("Hermes (AI) - <naam>"); stap 1 koppelt die alsnog.
--
-- Member-ids (geverifieerd): Julian Verboom = '1777063849963'.

-- ─────────────────────────────────────────────────────────────────────────────
-- STAP 1 — Backfill Hermes-lead attributie: koppel bestaande Hermes-leads aan de
--          starter door de naam in added_by_name te matchen op sales_members.name.
--          (Cron-runs zonder naam — added_by_name = 'Hermes (AI)' — blijven NULL.)
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE warm_leads wl
SET added_by_id = sm.id
FROM sales_members sm
WHERE wl.added_by_id IS NULL
  AND wl.added_by_name LIKE 'Hermes (AI) - %'
  AND sm.name = substring(wl.added_by_name FROM 'Hermes \(AI\) - (.*)');

-- Controle (optioneel): welke Hermes-leads bleven onkoppelbaar?
-- SELECT added_by_name, count(*) FROM warm_leads
--  WHERE added_by_id IS NULL AND added_by_name ILIKE 'Hermes (AI)%'
--  GROUP BY added_by_name;

-- ─────────────────────────────────────────────────────────────────────────────
-- STAP 2 — Terugwerkende herberekening commissie → 75% van closed_amount voor
--          ALLE gesloten warm_leads, BEHALVE die van Julian (owner → 0%).
--          warm_leads.commission_amount is de bron voor payout + stats.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE warm_leads
SET commission_amount = ROUND(closed_amount::numeric * 0.75, 2)
WHERE status = 'closed'
  AND closed_amount IS NOT NULL
  AND (added_by_id IS DISTINCT FROM '1777063849963');   -- iedereen behalve Julian

UPDATE warm_leads
SET commission_amount = 0
WHERE status = 'closed'
  AND added_by_id = '1777063849963';                    -- Julian (owner) → 0%

-- ─────────────────────────────────────────────────────────────────────────────
-- STAP 3 — (optioneel, alleen weergave) clients.commission_amount gelijktrekken.
--          Payout/stats lezen warm_leads, niet clients — dit is puur voor de
--          Clients-tab weergave. clients.added_by_id is een UUID-kolom die NIET
--          matcht met sales-member-ids, dus Julian kan hier niet betrouwbaar
--          worden uitgesloten. Julian heeft doorgaans geen eigen client-rijen;
--          controleer dat vóór je dit draait als dat wél zo is.
-- ─────────────────────────────────────────────────────────────────────────────
-- UPDATE clients
-- SET commission_amount = ROUND(total_amount::numeric * 0.75, 2)
-- WHERE demo_status IN ('geclosed','aanbetaling','volledig_betaald','getekend','afbouwen','contract_gestuurd','vragenlijst_gestuurd')
--   AND total_amount IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Controle achteraf:
-- SELECT added_by_name, count(*), sum(commission_amount)
--   FROM warm_leads WHERE status='closed' GROUP BY added_by_name ORDER BY 3 DESC;
