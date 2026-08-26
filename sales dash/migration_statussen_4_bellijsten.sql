-- ═══════════════════════════════════════════════════════════════════════════
--  DEEL 4: eigen bellijst per teamlid
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Puur additief: er wordt niets verwijderd.
--
--  Tot nu toe deelde iedereen dezelfde prospectlijst. Vanaf nu uploadt Julian
--  per persoon een eigen CSV en ziet ieder alleen zijn eigen lijst.
-- ═══════════════════════════════════════════════════════════════════════════

alter table prospect_list add column if not exists toegewezen_aan_id   text;
alter table prospect_list add column if not exists toegewezen_aan_naam text;

-- De bellijst filtert hier bij elke pagina-load op, dus dit is de index die
-- het snel houdt. De tweede dekt de combinatie die de werklijst gebruikt.
create index if not exists idx_prospect_toegewezen
    on prospect_list (toegewezen_aan_id);
create index if not exists idx_prospect_toegewezen_status
    on prospect_list (toegewezen_aan_id, status);

-- De tabel is leeg (de oude 6438 rijen zijn geëxporteerd naar backups/ en
-- daarna gewist), dus er valt niets toe te wijzen aan bestaande rijen.
-- Staat er onverhoopt toch iets in zonder eigenaar, dan blijft dat zichtbaar
-- voor Julian en voor niemand anders. Dat is de bedoelde uitkomst.
