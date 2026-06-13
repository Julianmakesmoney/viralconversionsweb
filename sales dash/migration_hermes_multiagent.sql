-- ─────────────────────────────────────────────────────────────────────────────
-- Hermes multi-agent migration
-- Voegt per-categorie assistant_id slots toe + website_status tagging per prospect.
-- Run één keer in Supabase SQL editor. Idempotent.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. hermes_settings: per-categorie assistant_id ──────────────────────────
-- De bestaande `assistant_id` kolom blijft de fallback / "zonder website" agent.
-- Deze 2 nieuwe kolommen zijn voor de extra agents die Julian heeft gemaakt:
ALTER TABLE hermes_settings ADD COLUMN IF NOT EXISTS assistant_id_broken_website   text;
ALTER TABLE hermes_settings ADD COLUMN IF NOT EXISTS assistant_id_outdated_website text;
-- Optioneel: aparte slot voor "zonder website" (default = de oude assistant_id)
ALTER TABLE hermes_settings ADD COLUMN IF NOT EXISTS assistant_id_no_website       text;

-- ── 2. prospect_list: handmatige website-status tagging ─────────────────────
-- NULL of leeg  = website is goed / niet geclassificeerd
-- 'broken'      = website is kapot (404, SSL-fout, geen response)
-- 'outdated'    = website is verouderd (oude design, niet responsive, etc.)
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS website_status text;
CREATE INDEX IF NOT EXISTS idx_prospect_list_website_status ON prospect_list (website_status);

-- ── 3. hermes_runs: welke categorieën heeft deze run gebeld? ────────────────
ALTER TABLE hermes_runs ADD COLUMN IF NOT EXISTS categories text;
-- Comma-separated, bv: "no_website,broken_website"

-- ── 4. prospect_list: welke categorie deze call gebruikt heeft ──────────────
-- (handig voor analytics: warm rate per categorie)
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_category text;

-- ── Sanity check ────────────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name='hermes_settings' AND column_name LIKE 'assistant_id%';
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name='prospect_list'  AND column_name IN ('website_status','hermes_category');
