-- ─────────────────────────────────────────────────────────────────────────────
-- Demo-aanvraag & onboarding funnel
-- Nieuwe tabellen voor de demo-flow (aanvraag → advies → meeting → content).
-- Run één keer in de Supabase SQL editor. Idempotent — safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. demo_requests: één rij per aanvraag (ook partieel) ───────────────────
CREATE TABLE IF NOT EXISTS demo_requests (
  id              text PRIMARY KEY,                 -- client-generated ms-epoch
  status          text NOT NULL DEFAULT 'partieel', -- partieel | aangevraagd | content_compleet | demo_gebouwd | meeting_gehad | gesloten | verloren
  last_step       int  NOT NULL DEFAULT 1,          -- hoe ver de prospect kwam (1..4)

  company_name    text,
  city            text,
  branche         text,
  website_huidig  text,
  contact_name    text,
  email           text,
  phone           text,

  answers         jsonb,   -- {pijn:[], capacity, calls, value, hours}
  advies          jsonb,   -- output van bereken_advies (wat de prospect zag)
  places          jsonb,   -- Google Places data (adres, tel, tijden, categorie, foto's)
  content         jsonb,   -- stap 4: diensten/prijzen/logo/foto's/style_ref/features/over/inspiratie
  meeting         jsonb,   -- {slot_start, slot_end, booked_at, confirmed, reminders:{}}

  meeting_start   timestamptz,   -- top-level kopie voor agenda-query + vrije-slot-check
  meeting_end     timestamptz,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_demo_requests_status        ON demo_requests (status);
CREATE INDEX IF NOT EXISTS idx_demo_requests_created_at    ON demo_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_demo_requests_meeting_start ON demo_requests (meeting_start);

-- ── 2. demo_settings: singleton config (beschikbaarheid agenda) ─────────────
CREATE TABLE IF NOT EXISTS demo_settings (
  id              int PRIMARY KEY DEFAULT 1,
  -- Werktijden per weekdag (0=ma .. 6=zo), lijst van [open,dicht] blokken in NL-tijd.
  weekday_hours   jsonb NOT NULL DEFAULT '{"0":[["09:00","17:00"]],"1":[["09:00","17:00"]],"2":[["09:00","17:00"]],"3":[["09:00","17:00"]],"4":[["09:00","17:00"]],"5":[],"6":[]}'::jsonb,
  slot_minutes    int  NOT NULL DEFAULT 30,
  min_days_ahead  int  NOT NULL DEFAULT 2,   -- vroegste slot = 2 werkdagen vooruit (demo bouwen)
  max_days_ahead  int  NOT NULL DEFAULT 5,   -- laatste slot = 5 werkdagen vooruit
  buffer_min      int  NOT NULL DEFAULT 0,   -- ruimte tussen meetings
  notify_email    text,                      -- waar Julian de seintjes krijgt
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (id = 1)
);
INSERT INTO demo_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── 3. style_examples: STYLE_REF voorbeelden (beheerbaar in de dash) ────────
CREATE TABLE IF NOT EXISTS style_examples (
  id          text PRIMARY KEY,
  branche     text,               -- filter; leeg/NULL = toon bij elke branche
  label       text,
  image       text,               -- URL of data-URL van de screenshot
  active      boolean NOT NULL DEFAULT true,
  sort        int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_style_examples_branche ON style_examples (branche);

-- ── Sanity checks ────────────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns WHERE table_name='demo_requests';
-- SELECT * FROM demo_settings WHERE id=1;
