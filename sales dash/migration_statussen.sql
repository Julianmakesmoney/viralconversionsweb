-- ═══════════════════════════════════════════════════════════════════════════
--  Statussen-herziening + takensysteem
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. PROSPECTS ───────────────────────────────────────────────────────────
-- Van losse vlaggen (called / no_answer) naar één echte status.
alter table prospect_list add column if not exists status         text not null default 'nog_niet_benaderd';
alter table prospect_list add column if not exists attempt_count  int  not null default 0;
alter table prospect_list add column if not exists retry_after    timestamptz;
alter table prospect_list add column if not exists prev_status    text;
alter table prospect_list add column if not exists status_at      timestamptz;
alter table prospect_list add column if not exists status_by_id   text;
alter table prospect_list add column if not exists status_by_name text;

-- geldige waarden: nog_niet_benaderd | benaderd | niet_opgenomen |
--                  geen_geldig_nummer | geen_interesse | demo
alter table prospect_list drop constraint if exists prospect_status_check;
alter table prospect_list add  constraint prospect_status_check check (status in
  ('nog_niet_benaderd','benaderd','niet_opgenomen','geen_geldig_nummer','geen_interesse','demo'));

create index if not exists idx_prospect_status      on prospect_list (status);
create index if not exists idx_prospect_retry_after on prospect_list (retry_after);

-- de AI cold caller is verwijderd; deze kolommen zijn nergens meer voor
alter table prospect_list
  drop column if exists hermes_status,        drop column if exists hermes_outcome,
  drop column if exists hermes_call_id,       drop column if exists hermes_called_at,
  drop column if exists hermes_category,      drop column if exists hermes_ended_reason,
  drop column if exists hermes_recording_url, drop column if exists hermes_run_id,
  drop column if exists hermes_summary,       drop column if exists hermes_transcript,
  drop column if exists hermes_variant,       drop column if exists hermes_warm_lead_id,
  drop column if exists hermes_call_duration_sec,
  drop column if exists no_answer;

-- ── 2. LEADS ───────────────────────────────────────────────────────────────
-- Twee routes: wie de lead binnenhaalt bouwt zelf de demo (Timon, Levi), of
-- Julian bouwt hem (Bram). De route staat op het teamlid, niet op de lead.
alter table warm_leads add column if not exists lead_status    text not null default 'demo_bouwen';
alter table warm_leads add column if not exists demo_url       text;
alter table warm_leads add column if not exists prev_status    text;
alter table warm_leads add column if not exists status_at      timestamptz;
alter table warm_leads add column if not exists status_by_id   text;
alter table warm_leads add column if not exists status_by_name text;

alter table warm_leads drop constraint if exists lead_status_check;
alter table warm_leads add  constraint lead_status_check check (lead_status in
  ('demo_bouwen','demo_doorsturen','demo_aanleveren','demo_gezien','afgehaakt'));

create index if not exists idx_lead_status on warm_leads (lead_status);

-- ── 3. CLIENTS ─────────────────────────────────────────────────────────────
alter table clients add column if not exists sale_amount        int;
alter table clients add column if not exists paid_at            timestamptz;
alter table clients add column if not exists commission_amount  int;
alter table clients add column if not exists commission_paid_at timestamptz;
alter table clients add column if not exists prev_status        text;
alter table clients add column if not exists status_at          timestamptz;
alter table clients add column if not exists status_by_id       text;
alter table clients add column if not exists status_by_name     text;

-- ── 4. TEAMLEDEN: route + commissie ────────────────────────────────────────
-- demo_flow 'zelf'   → bouwt en stuurt de demo zelf door (Timon, Levi) → 75%
-- demo_flow 'julian' → Julian bouwt alles, verkoper haalt alleen binnen (Bram) → 40%
-- Commissie is een percentage van het verkoopbedrag, zodat €500 en €400
-- allebei automatisch goed uitkomen (75% van 500 = 375, 40% van 500 = 200).
alter table sales_members add column if not exists demo_flow      text    not null default 'zelf';
alter table sales_members add column if not exists commissie_pct  numeric not null default 0.75;

alter table sales_members drop constraint if exists demo_flow_check;
alter table sales_members add  constraint demo_flow_check check (demo_flow in ('zelf','julian'));
alter table sales_members drop constraint if exists commissie_pct_check;
alter table sales_members add  constraint commissie_pct_check check (commissie_pct >= 0 and commissie_pct <= 1);

-- Julian zelf krijgt geen commissie; hij is de eigenaar.
update sales_members set commissie_pct = 0, demo_flow = 'julian'
 where lower(email) = 'julian@viralconversions.io';

-- ── 5. STATUS-LOG ──────────────────────────────────────────────────────────
-- Elke statuswissel wordt gelogd, zodat terugdraaien zichtbaar is in de kalender.
create table if not exists status_log (
  id           bigserial primary key,
  entity_type  text not null check (entity_type in ('prospect','lead','client')),
  entity_id    text not null,
  entity_name  text,
  from_status  text,
  to_status    text not null,
  is_revert    boolean not null default false,
  by_id        text,
  by_name      text,
  created_at   timestamptz not null default now()
);
create index if not exists idx_status_log_entity  on status_log (entity_type, entity_id);
create index if not exists idx_status_log_created on status_log (created_at desc);

-- ── 6. DAGELIJKSE TAAK ─────────────────────────────────────────────────────
-- De enige taak die niet uit een status volgt: screenshot in de WhatsApp-groep.
create table if not exists daily_task_log (
  id         bigserial primary key,
  member_id  text not null,
  task_key   text not null default 'screenshot',
  task_date  date not null,
  done_at    timestamptz,
  unique (member_id, task_key, task_date)
);
create index if not exists idx_daily_task_date on daily_task_log (task_date desc);
