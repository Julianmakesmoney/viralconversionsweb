-- ─────────────────────────────────────────────────────────────────────────────
-- Hermes (Vapi cold-call agent) migration
-- Run once in Supabase SQL editor. Idempotent — safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. prospect_list: per-prospect Hermes bookkeeping ────────────────────────
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_status        text;
-- enum-as-text: 'queued' | 'calling' | 'niet_opgenomen' | 'benaderd' | 'warm' | 'failed' | NULL
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_run_id        text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_call_id       text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_called_at     timestamptz;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_ended_reason  text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_outcome       text;   -- 'warm' | 'not_interested' | 'no_answer' | 'invalid_number'
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_summary       text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_transcript    text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_recording_url text;
ALTER TABLE prospect_list ADD COLUMN IF NOT EXISTS hermes_warm_lead_id  text;   -- FK-by-id to warm_leads.id when a warm lead is created

CREATE INDEX IF NOT EXISTS idx_prospect_list_hermes_status  ON prospect_list (hermes_status);
CREATE INDEX IF NOT EXISTS idx_prospect_list_hermes_call_id ON prospect_list (hermes_call_id);
CREATE INDEX IF NOT EXISTS idx_prospect_list_hermes_run_id  ON prospect_list (hermes_run_id);

-- ── 2. hermes_runs: één rij per "Start Hermes" sessie ───────────────────────
CREATE TABLE IF NOT EXISTS hermes_runs (
  id              text PRIMARY KEY,                  -- client-generated ms-epoch
  started_at      timestamptz NOT NULL DEFAULT now(),
  ended_at        timestamptz,
  started_by_id   text,                              -- sales_members.id, NULL for cron
  started_by_name text,
  trigger         text NOT NULL DEFAULT 'manual',    -- 'manual' | 'cron'
  status          text NOT NULL DEFAULT 'running',   -- 'running' | 'completed' | 'cancelled' | 'failed'
  num_prospects   int  NOT NULL DEFAULT 0,
  num_called      int  NOT NULL DEFAULT 0,
  num_warm        int  NOT NULL DEFAULT 0,
  num_no_answer   int  NOT NULL DEFAULT 0,
  num_not_interested int NOT NULL DEFAULT 0,
  num_failed      int  NOT NULL DEFAULT 0,
  max_calls       int  NOT NULL DEFAULT 50,
  max_parallel    int  NOT NULL DEFAULT 3,
  filter_summary  text,                              -- human-readable filter used (e.g. "no website + niche=Auto")
  notes           text
);
CREATE INDEX IF NOT EXISTS idx_hermes_runs_started_at ON hermes_runs (started_at DESC);

-- ── 3. hermes_settings: singleton config row (id=1) ─────────────────────────
CREATE TABLE IF NOT EXISTS hermes_settings (
  id                int PRIMARY KEY DEFAULT 1,
  assistant_id      text,                            -- Vapi assistantId (UUID)
  phone_number_id   text,                            -- Vapi phoneNumberId (UUID)
  system_prompt     text,                            -- Override of the Vapi assistant's prompt (optional)
  voice_id          text,                            -- ElevenLabs voice ID (display-only)
  first_message     text,                            -- Override of the opener line
  max_calls_default int  NOT NULL DEFAULT 50,
  max_parallel_default int NOT NULL DEFAULT 3,
  filter_no_website boolean NOT NULL DEFAULT true,   -- only call prospects without a website
  filter_uncalled_only boolean NOT NULL DEFAULT true, -- only call prospects with called=false
  cron_enabled      boolean NOT NULL DEFAULT false,
  cron_time         text NOT NULL DEFAULT '10:00',   -- HH:MM in NL local time
  cron_weekdays_only boolean NOT NULL DEFAULT true,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (id = 1)
);

-- Seed the singleton row if missing
INSERT INTO hermes_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ── Sanity check (manual) ────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name='prospect_list' AND column_name LIKE 'hermes_%';
-- SELECT * FROM hermes_settings WHERE id=1;
-- SELECT count(*) FROM hermes_runs;
