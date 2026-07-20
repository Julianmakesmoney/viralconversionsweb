-- ─────────────────────────────────────────────────────────────────────────────
-- Demo-webshop funnel — additief op de bestaande demo-tabellen.
-- Eén machine, tweede funnel: type = 'website' | 'webshop'. Geen nieuwe tabellen.
-- Run één keer in de Supabase SQL editor. Idempotent — safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. type op de aanvraag (webshop-velden leven in answers jsonb: shop, shop_url,
--    wat_verkoop, kanalen, omzet, orders, producten, q)
ALTER TABLE demo_requests   ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'website';
CREATE INDEX IF NOT EXISTS idx_demo_requests_type ON demo_requests (type);

-- 2. type op de stijl-voorbeelden (STYLE_REF gefilterd op funnel-type)
ALTER TABLE style_examples  ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'website';
CREATE INDEX IF NOT EXISTS idx_style_examples_type ON style_examples (type);

-- 3. per-type kalender-instellingen op dezelfde beschikbaarheid (geen 2e kalender):
--    webshop = 60 min, 3-7 werkdagen vooruit; website blijft op de bestaande kolommen.
ALTER TABLE demo_settings   ADD COLUMN IF NOT EXISTS type_config jsonb NOT NULL
  DEFAULT '{"webshop":{"slot_minutes":60,"min_days_ahead":3,"max_days_ahead":7}}'::jsonb;

-- ── Sanity ───────────────────────────────────────────────────────────────────
-- SELECT type, count(*) FROM demo_requests GROUP BY type;
-- SELECT type_config FROM demo_settings WHERE id = 1;
