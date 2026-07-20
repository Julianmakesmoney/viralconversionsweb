-- migration_perf_indexes.sql
-- Performance indexes for the sales portal + Hermes. All are additive (read-path
-- only); they do NOT change any query result, only how fast rows are located/sorted.
--
-- WHY: the hot endpoints filter/order warm_leads, clients, prospect_list and
-- status_history on columns that currently have no index, so every request does a
-- sequential scan that grows linearly with the database. Adding these turns those
-- scans into index lookups.
--
-- DEPLOY NOTES:
--  * Plain CREATE INDEX (no CONCURRENTLY) so it runs in the Supabase SQL editor,
--    which wraps statements in a transaction (CONCURRENTLY is not allowed there).
--    Each build briefly write-locks its table — at current sizes this is seconds.
--    For a very large table you can instead run a single statement with
--    CONCURRENTLY outside a transaction (psql / \set AUTOCOMMIT on).
--  * IF NOT EXISTS makes each statement safe to re-run.
--  * pg_trgm ships with Supabase; CREATE EXTENSION is idempotent.
--
-- Suggested order (highest leverage first) if you apply incrementally:
--   1) idx_prospect_list_hermes_run_id  (Hermes recount/compute — verify it exists!)
--   2) warm_leads status/closed_at + added_by_id + created_at
--   3) prospect_list called/called_at + uncalled
--   4) clients + prospect_list.called_by_id/import_batch
--   5) pg_trgm + niche trigram (Hermes niche ILIKE)
--   6) status_history + warm_leads pipeline/company/followup

-- === prospect_list: CRITICAL — Hermes run counting/recount joins on hermes_run_id ===
-- Referenced by the original hermes migration, but VERIFY it is actually present in
-- production. Without it, every _hermes_compute_counts / _hermes_recount_run is a
-- full-table scan, which makes all Hermes slowness dramatically worse.
CREATE INDEX IF NOT EXISTS idx_prospect_list_hermes_run_id
  ON prospect_list (hermes_run_id);

-- === warm_leads: status='closed' + closed_at range (15+ revenue/earnings endpoints) ===
-- A single (status, closed_at) btree serves the equality, the range AND the sort.
CREATE INDEX IF NOT EXISTS idx_warm_leads_status_closed_at
  ON warm_leads (status, closed_at);

-- === warm_leads: per-member scoping (my-stats, meeting-reminders 60s poll, whatsapp-stats, kpi) ===
CREATE INDEX IF NOT EXISTS idx_warm_leads_added_by_id
  ON warm_leads (added_by_id);

-- === warm_leads: date-window + list ORDER BY created_at DESC (kpi-stats, leaderboard, leads tab) ===
CREATE INDEX IF NOT EXISTS idx_warm_leads_created_at
  ON warm_leads (created_at DESC);

-- === warm_leads: pipeline / promotion / followup predicates ===
CREATE INDEX IF NOT EXISTS idx_warm_leads_pipeline_status
  ON warm_leads (pipeline_status);
CREATE INDEX IF NOT EXISTS idx_warm_leads_company_name
  ON warm_leads (company_name);
CREATE INDEX IF NOT EXISTS idx_warm_leads_followup_date
  ON warm_leads (followup_date);

-- === clients: per-member + date-window / ORDER BY created_at DESC ===
CREATE INDEX IF NOT EXISTS idx_clients_added_by_id
  ON clients (added_by_id);
CREATE INDEX IF NOT EXISTS idx_clients_created_at
  ON clients (created_at DESC);

-- === prospect_list: leaderboard (30s poll) + my-stats + top-earners (called=true, date range) ===
CREATE INDEX IF NOT EXISTS idx_prospect_list_called_called_at
  ON prospect_list (called, called_at);

-- === prospect_list: Hermes selection scan (called=false) ===
CREATE INDEX IF NOT EXISTS idx_prospect_list_uncalled
  ON prospect_list (id) WHERE called = false;

-- === prospect_list: my-stats per-member + batch delete ===
CREATE INDEX IF NOT EXISTS idx_prospect_list_called_by_id
  ON prospect_list (called_by_id);
CREATE INDEX IF NOT EXISTS idx_prospect_list_import_batch
  ON prospect_list (import_batch);

-- === prospect_list: Hermes niche ILIKE('%term%') leading-wildcard -> needs trigram GIN ===
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_prospect_list_niche_trgm
  ON prospect_list USING gin (niche gin_trgm_ops);

-- === wa_outreach_log: whatsapp-stats per-member scan (30s dashboard load) + leads/clients wa_count ===
-- _compute_whatsapp_state filters (member_id, source, created_at); the leads/clients
-- tabs count attempts per lead via .eq('source','lead'). This log grows unbounded, so
-- these two composites keep those scans cheap as it accumulates.
CREATE INDEX IF NOT EXISTS idx_wa_outreach_member_created
  ON wa_outreach_log (member_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wa_outreach_source_lead
  ON wa_outreach_log (source, lead_id);

-- === status_history: callback-funnel KPI lookups ===
CREATE INDEX IF NOT EXISTS idx_status_history_entity
  ON status_history (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_status_history_new_status
  ON status_history (new_status);

-- === Sanity check (manual) ===
-- SELECT indexname FROM pg_indexes
--  WHERE tablename IN ('warm_leads','clients','prospect_list','status_history')
--  ORDER BY tablename, indexname;
