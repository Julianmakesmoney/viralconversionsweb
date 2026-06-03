-- ─────────────────────────────────────────────────────────────────────────────
-- Meeting feature migration — adds meeting state machine to warm_leads + clients
-- Run in Supabase SQL editor. Idempotent: safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── warm_leads ───────────────────────────────────────────────────────────────
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_state               text;
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_calendly_url        text;
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_link_sent_at        timestamptz;
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_scheduled_at        timestamptz;
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_join_url            text;
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_outcome             text;  -- 'show' | 'no_show' | NULL
ALTER TABLE warm_leads ADD COLUMN IF NOT EXISTS meeting_no_show_followup_at timestamptz;

-- Backfill legacy rows: NULL meeting_state → 'pending_link'
UPDATE warm_leads
   SET meeting_state = 'pending_link'
 WHERE meeting_state IS NULL;

-- ── clients ──────────────────────────────────────────────────────────────────
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_state               text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_calendly_url        text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_link_sent_at        timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_scheduled_at        timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_join_url            text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_outcome             text;  -- 'show' | 'no_show' | NULL
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meeting_no_show_followup_at timestamptz;

-- Backfill legacy clients: rows that already have geleverd/gezien stay valid —
-- LEGACY_DS in server.py collapses them to 'show' at read-time. No data rewrite.
-- New clients without a meeting context get 'scheduled' as a sensible default
-- so they don't fall out of the meeting funnel (they came from forum_ingevuld
-- through the old to-client path which was less strict). Keep them visible.
UPDATE clients
   SET meeting_state = 'scheduled'
 WHERE meeting_state IS NULL;

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_warm_leads_meeting_state         ON warm_leads (meeting_state);
CREATE INDEX IF NOT EXISTS idx_warm_leads_meeting_scheduled_at  ON warm_leads (meeting_scheduled_at);
CREATE INDEX IF NOT EXISTS idx_clients_meeting_state            ON clients    (meeting_state);
CREATE INDEX IF NOT EXISTS idx_clients_meeting_scheduled_at     ON clients    (meeting_scheduled_at);

-- ── Sanity checks ────────────────────────────────────────────────────────────
-- Verify columns exist before deploying the new app code:
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='warm_leads' AND column_name LIKE 'meeting_%';
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='clients' AND column_name LIKE 'meeting_%';
