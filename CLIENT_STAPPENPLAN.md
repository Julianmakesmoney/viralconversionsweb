# Premium Klant Stappenplan — Viral Conversions

Gebruik dit document voor elke nieuwe premium barber klant. Van intake tot oplevering.

---

## Fase 1 — Intake (voor het bouwen)

**Verzamel van de klant:**
- [ ] Bedrijfsnaam
- [ ] Logo (donker + licht versie, PNG/SVG)
- [ ] Primaire kleur (hex code, of laat hen kiezen uit voorbeelden)
- [ ] Diensten lijst + prijzen (bijv. Knipbeurt €25, Baard €15)
- [ ] Over-ons tekst / verhaal
- [ ] Foto van de barber / winkel
- [ ] Adres + telefoonnummer
- [ ] Instagram handle
- [ ] Werkdagen + openingstijden
- [ ] Gewenste domeinnaam

**Stuur dit naar de klant:**
- Formuliertje of WhatsApp checklist met bovenstaande vragen
- Vraag om minimaal 3-5 foto's/videos voor de galerij

---

## Fase 2 — Website bouwen (Claude Code)

1. **Start een nieuwe Claude Code sessie**
2. **Zeg dit aan Claude:**

```
Lees de CLAUDE_BARBER.md in deze repo. Bouw een complete barber website voor:

Naam: [NAAM]
Kleur: [HEX]
Diensten: [LIJST]
Adres: [ADRES]
Telefoon: [TELEFOON]
Instagram: @[HANDLE]
Openingstijden: [TIJDEN]

Gebruik de website kapper/index.html als design basis.
Verbind de boekingsknop met /api/booking.
```

3. **Review de website** — check op mobiel en desktop
4. **Pas aan** tot klant tevreden is (max 2 revisieronden)

---

## Fase 3 — Technische setup (~30 min)

### Supabase (database)
- [ ] Ga naar supabase.com → New project
- [ ] Noteer: Project URL + Service Role Key
- [ ] Ga naar SQL Editor → plak en run:

```sql
CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT DEFAULT '', date TEXT NOT NULL, time TEXT NOT NULL, notes TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS onboarding (id TEXT PRIMARY KEY, data TEXT NOT NULL, submitted_at TIMESTAMPTZ DEFAULT NOW());
```

### GitHub
- [ ] Maak nieuwe repo aan: `[klantnaam]-website`
- [ ] Push de klant-code naar die repo

### Render (backend)
- [ ] render.com → New Web Service → koppel GitHub repo
- [ ] Build command: `pip install -r requirements.txt`
- [ ] Start command: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- [ ] Environment variables toevoegen:
  - `SUPABASE_URL` = `https://[project-ref].supabase.co`
  - `SUPABASE_KEY` = service role key
  - `ADMIN_PASSWORD` = sterk wachtwoord voor klant

### Cloudflare Pages (hosting)
- [ ] pages.cloudflare.com → New project → koppel GitHub repo
- [ ] Geen build command nodig
- [ ] Deploy → wacht tot groen

---

## Fase 4 — Domein koppelen (~15 min)

- [ ] Klant koopt domein (of jij doet het via Cloudflare)
- [ ] Cloudflare Pages → Custom domain → voer domein in
- [ ] Bij Spaceship/andere registrar: verander nameservers naar Cloudflare
- [ ] Wacht 5-30 min op propagatie

---

## Fase 5 — Testen (voor oplevering)

- [ ] Website laadt correct op desktop
- [ ] Website laadt correct op mobiel
- [ ] Afspraak boeken werkt → verschijnt in dashboard
- [ ] Admin dashboard bereikbaar via `/admin` + wachtwoord
- [ ] Onboarding form werkt → verschijnt in onboarding dashboard
- [ ] Favicon klopt
- [ ] Alle teksten/prijzen kloppen

---

## Fase 6 — Oplevering

**Geef de klant:**
- Website URL (domeinnaam)
- Admin dashboard URL: `[domein]/admin`
- Admin wachtwoord
- Korte uitleg hoe het dashboard werkt (screenrecording of WhatsApp voice)

**Interne notitie bewaren:**
- GitHub repo naam
- Render service naam
- Supabase project naam
- Admin wachtwoord

---

## Kosten per klant (maandelijks)

| Service | Kosten |
|---------|--------|
| Cloudflare Pages | Gratis |
| Supabase (free tier) | Gratis (max 2 projecten) |
| Supabase (paid) | €25/maand per project |
| Render (free) | Gratis maar slaapt na 15 min |
| Render (Starter) | $7/maand — geen slaap |
| Domein | ~€10-15/jaar |

**Aanbeveling:** Render Starter ($7/maand) voor klanten — geen cold starts.

---

## Tijdsinschatting

| Fase | Tijd |
|------|------|
| Intake | 15 min |
| Website bouwen | 60-90 min |
| Technische setup | 30 min |
| Domein + testen | 20 min |
| **Totaal** | **~2-2.5 uur** |
