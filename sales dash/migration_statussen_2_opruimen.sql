-- ═══════════════════════════════════════════════════════════════════════════
--  Statussen-herziening  —  DEEL 2 van 2: OPRUIMEN
--
--  Draai dit PAS NADAT de nieuwe code live staat op Render en je hebt gezien
--  dat het portaal werkt. Dit deel verwijdert kolommen die de oude code nog
--  wel gebruikte, dus te vroeg draaien breekt het portaal tot de deploy klaar is.
--
--  Dit is onomkeerbaar: de gesprekshistorie van de AI-beller (transcripts,
--  opnames, samenvattingen) is daarna weg. De prospect-tabel is al geleegd en
--  de export staat lokaal in backups/, dus er gaat geen bruikbare data verloren.
-- ═══════════════════════════════════════════════════════════════════════════

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
