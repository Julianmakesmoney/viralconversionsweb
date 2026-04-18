# Claude — Barber Website Instructies

Dit bestand wordt gelezen door Claude Code bij het bouwen van een klantwebsite.

## Context

Je bouwt een **complete barber/kapper website** voor een premium klant van Viral Conversions. De website is gebaseerd op de bestaande code in `website kapper/index.html`. De backend (`server.py`) gebruikt Supabase en Flask en draait op Render. De frontend wordt gehost op Cloudflare Pages.

## Design Basis

Gebruik `website kapper/index.html` als design basis:
- Aurora achtergrond effect (blobs)
- Bricolage Grotesque voor koppen, DM Sans voor body
- Paars/violet als standaard accent — vervang dit met de klantkleur
- Dark/light mode toggle
- Animated marquee voor video's/fotos
- Floating nav die scrollt

Pas de primaire kleur aan via de CSS tokens bovenaan:
```css
:root {
  --p:    [PRIMAIRE HEX];   /* hoofdkleur */
  --pd:   [DONKER HEX];    /* donker variant */
  --pl:   [LICHT HEX];     /* licht variant */
  --pglow: rgba([R],[G],[B],.22);
  --pdim:  rgba([R],[G],[B],.10);
}
```

## Verplichte Secties

Elke barber website bevat:
1. **Nav** — logo, links, boekknop
2. **Hero** — grote naam/headline, subtext, CTA knop
3. **Diensten** — kaarten met naam + prijs
4. **Over ons** — foto + tekst
5. **Video galerij** — marquee met resultaten
6. **Reviews** — 3-5 echte recensies
7. **Locatie** — adres, openingstijden, Google Maps embed
8. **Booking modal** — verbonden met `/api/booking`
9. **Footer**

## Booking Systeem

De boekknop opent een modal. De modal POST naar `/api/booking`:

```javascript
const res = await fetch('/api/booking', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name, email, phone, date, time, notes })
});
```

Geblokkeerde tijdsloten ophalen via `GET /api/booked-slots` — response is `{ "2024-04-20": ["10:00", "11:00"] }`.

Pre-warm de server als de booking modal opent:
```javascript
fetch('/api/ping').catch(() => {});
```

## Admin Dashboard

Het dashboard staat in `VC website dash/dashboard.html`. Dit hoef je NIET te wijzigen — het werkt automatisch via dezelfde `/api/bookings` endpoint.

Bereikbaar via `/admin` + wachtwoord (ingesteld als `ADMIN_PASSWORD` env var op Render).

## Wat je NIET aanpast

- `server.py` — werkt al, niet aanraken
- `requirements.txt` — werkt al
- `functions/` map — Cloudflare proxy, niet aanraken
- `render.yaml` — niet aanraken
- `VC website dash/dashboard.html` — werkt al
- `onboarding/` map — werkt al

## Bestandsstructuur (per klant repo)

```
/
├── [klantnaam] website/
│   └── index.html        ← de klantwebsite (jij bouwt dit)
├── VC website dash/
│   └── dashboard.html    ← admin dashboard (kopieer van hoofdrepo)
├── onboarding/
│   └── onboarding.html   ← onboarding form (kopieer van hoofdrepo)
├── onboarding dash/
│   └── onboardingVC.html ← onboarding dashboard (kopieer van hoofdrepo)
├── logo's/               ← klant logo's hier
├── server.py             ← kopieer van hoofdrepo
├── requirements.txt      ← kopieer van hoofdrepo
├── render.yaml           ← kopieer van hoofdrepo
└── functions/
    └── api/
        └── [[path]].js   ← kopieer van hoofdrepo
```

## Kwaliteitschecklist

Voor je klaar bent, verifieer:
- [ ] Mobiel responsive (test op 375px breedte)
- [ ] Alle teksten zijn in de taal van de klant
- [ ] Prijzen kloppen
- [ ] Boekknop werkt (test met echte POST)
- [ ] Dark/light mode werkt
- [ ] Geen placeholder tekst meer zichtbaar
- [ ] Favicon is het logo van de klant
- [ ] Paginatitel klopt (`<title>`)
