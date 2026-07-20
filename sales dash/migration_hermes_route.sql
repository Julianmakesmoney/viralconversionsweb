-- ─────────────────────────────────────────────────────────────────────────────
-- Hermes variant-aware status route migration
-- Voegt per-lead/-client variant-tagging toe + de high-volume demo-track kolom,
-- zodat "lower conversions - high volume" leads een eigen route krijgen.
-- Run één keer in Supabase SQL editor. Idempotent — safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Variant meelopen op elke laag ────────────────────────────────────────
-- 'high_conversion' (default / origineel) | 'high_volume' (nieuwe aanpak).
-- Bron van waarheid is hermes_runs.variant; die vloeit door naar de prospect,
-- de warm lead en (bij promotie) de client.
ALTER TABLE hermes_runs   ADD COLUMN IF NOT EXISTS variant        text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_variant text;
ALTER TABLE warm_leads    ADD COLUMN IF NOT EXISTS hermes_variant text;
ALTER TABLE clients       ADD COLUMN IF NOT EXISTS hermes_variant text;

-- ── 2. High-volume demo-track op de warm lead ───────────────────────────────
-- High-volume warm leads hebben in de Warm Leads tab twee parallelle tracks:
--   • demo-track (deze kolom):     'bouwen' → 'af'
--   • meeting-track (meeting_state): 'link_sent' (sturen) → 'scheduled' (bevestigd)
-- Pas als hv_demo_status='af' EN meeting bevestigd is, mag de lead → Client.
-- NULL/leeg voor high_conversion leads (die gebruiken de forum-flow).
ALTER TABLE warm_leads    ADD COLUMN IF NOT EXISTS hv_demo_status text;

CREATE INDEX IF NOT EXISTS idx_warm_leads_hermes_variant ON warm_leads (hermes_variant);
CREATE INDEX IF NOT EXISTS idx_clients_hermes_variant    ON clients    (hermes_variant);

-- ── 3. (Optioneel) Backfill variant voor bestaande runs ─────────────────────
-- De variant zat vroeger alleen als tekst in filter_summary ("variant=...").
-- Deze parse populeert de nieuwe hermes_runs.variant kolom eenmalig.
UPDATE hermes_runs
   SET variant = CASE
                   WHEN filter_summary LIKE '%variant=high_volume%'     THEN 'high_volume'
                   WHEN filter_summary LIKE '%variant=high_conversion%' THEN 'high_conversion'
                   ELSE variant
                 END
 WHERE variant IS NULL
   AND filter_summary IS NOT NULL;

-- ── Sanity checks ────────────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name='warm_leads' AND column_name IN ('hermes_variant','hv_demo_status');
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name='clients' AND column_name='hermes_variant';
-- SELECT variant, count(*) FROM hermes_runs GROUP BY variant;
