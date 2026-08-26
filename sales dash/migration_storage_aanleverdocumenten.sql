-- ═══════════════════════════════════════════════════════════════════════════
--  STORAGE: bucket voor aanleverdocumenten
--
--  Draai dit in de Supabase SQL-editor. Veilig opnieuw uit te voeren.
--  Dit vervangt het klikken in Storage -> New bucket; alles staat hier.
--
--  Waarom een policy voor 'anon': het portaal praat niet rechtstreeks met
--  Supabase. Alles loopt via de Flask-backend op Render, en die gebruikt de
--  anon key uit .env. De key staat nergens in de frontend, dus deze policy
--  geeft toegang aan de server, niet aan bezoekers van de site.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. De bucket ───────────────────────────────────────────────────────────
-- private: bestanden zijn niet via een gokbare URL op te vragen.
-- 25 MB per bestand is ruim voor een aanleverdocument en houdt de rekening
-- klein als er ooit iemand een video probeert te uploaden.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'aanleverdocumenten',
  'aanleverdocumenten',
  false,
  26214400,
  array[
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/plain',
    'text/markdown',
    'image/png',
    'image/jpeg',
    'image/webp'
  ]
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- ── 2. Toegangsregels ──────────────────────────────────────────────────────
-- Alleen deze ene bucket, alleen uploaden en lezen. Verwijderen en
-- overschrijven laten we bewust dicht: een aanleverdocument hoort te blijven
-- staan zodra het er is.
drop policy if exists "aanleverdocumenten uploaden" on storage.objects;
create policy "aanleverdocumenten uploaden"
  on storage.objects for insert to anon
  with check (bucket_id = 'aanleverdocumenten');

drop policy if exists "aanleverdocumenten lezen" on storage.objects;
create policy "aanleverdocumenten lezen"
  on storage.objects for select to anon
  using (bucket_id = 'aanleverdocumenten');

-- ── 3. Controle ────────────────────────────────────────────────────────────
-- Na het draaien zou dit één rij moeten geven met public = false.
select id, public, file_size_limit
  from storage.buckets
 where id = 'aanleverdocumenten';

-- En dit twee policies:
select policyname, cmd
  from pg_policies
 where schemaname = 'storage'
   and tablename  = 'objects'
   and policyname like 'aanleverdocumenten%';
