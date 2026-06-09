# Hermes setup — Vapi cold-call agent

Stappenplan om Hermes vanaf nul live te krijgen in de Bellijst tab van het
sales portaal. Doorloop dit één keer; daarna is het 🚀 Start Hermes (of cron).

---

## 0. SQL migratie draaien (1×)

In Supabase SQL editor → plak en run:
[migration_hermes.sql](migration_hermes.sql)

Voegt `hermes_*` kolommen toe aan `prospect_list` en maakt
`hermes_runs` + `hermes_settings`.

---

## 1. Env vars op de server (Render → Environment)

| Variabele | Verplicht? | Waarvoor |
|---|---|---|
| `VAPI_API_KEY` | **ja** | API key uit Vapi dashboard → Account → API Keys |
| `VAPI_WEBHOOK_SECRET` | aanbevolen | Random string. Zet dezelfde in Vapi assistant → Advanced → Server URL Secret. Zorgt dat alleen Vapi het webhook endpoint mag aanroepen |
| `CRON_SECRET` | alleen voor auto-run | Random string. Headers `x-cron-secret: <waarde>` moet meekomen met cron-tick aanroepen |

Na het toevoegen: Render redeployt automatisch.

---

## 2. Vapi assistant aanmaken (1×)

In Vapi dashboard → Assistants → Create:

**Model**
- Provider: `anthropic` of `openai`
- Model: `claude-sonnet-4-6` (of `gpt-4o`) — Sonnet 4.6 is een goede default
- Temperature: 0.6
- Max tokens: 250

**Voice**
- Provider: `11labs` (ElevenLabs)
- Voice: kies een Nederlandse stem (test eerst met je eigen nummer)
- Optimize streaming latency: 3

**Transcriber**
- Provider: `deepgram`
- Model: `nova-2`
- Language: `nl`

**Tools** — voeg deze twee custom tools toe (Type: Function):

```json
{
  "name": "mark_warm_lead",
  "description": "Markeer dit gesprek als WARM LEAD. Gebruik dit als de prospect interesse toont, een afspraak wil, of vraagt om gebeld te worden door een mens. Gebruik dit NOOIT zomaar; alleen bij duidelijke interesse.",
  "parameters": {
    "type": "object",
    "properties": {
      "reason": { "type": "string", "description": "Korte reden waarom dit een warme lead is" }
    },
    "required": ["reason"]
  }
}
```

```json
{
  "name": "mark_not_interested",
  "description": "Markeer dit gesprek als BENADERD MAAR NIET WARM. Gebruik dit als de prospect duidelijk geen interesse heeft, al een goede website heeft, geen tijd heeft of beleefd afwijst.",
  "parameters": {
    "type": "object",
    "properties": {
      "reason": { "type": "string", "description": "Korte reden waarom de prospect niet geïnteresseerd was" }
    },
    "required": ["reason"]
  }
}
```

**System prompt** — de volledige productie-versie staat in
[hermes_system_prompt.md](hermes_system_prompt.md). Plak alles tussen
`--- BEGIN ---` en `--- END ---` in **Vapi → assistant → Model → System
Message**. Bevat:
- "Julian Verboom" persona (geen aparte Hermes-naam)
- Volledige 4-staps flow (Opening / Aanleiding / Pitch / Close)
- Alle bezwaarafhandeling + FAQ
- Expliciete instructies wanneer `mark_warm_lead` / `mark_not_interested`
  af te vuren
- Voicemail handling + AI-disclosure regel
- Variabelen `{{company_name}}`, `{{city}}`, `{{niche}}`

**First message**: `Goeiemiddag, je spreekt met Julian Verboom... spreek ik met de eigenaar van {{company_name}}?`

**Server URL (webhook)**:
```
https://viralconversionsweb.onrender.com/api/vapi/webhook
```
Bij "Server URL Secret" → plak dezelfde string als `VAPI_WEBHOOK_SECRET`.

Klik **Create** en **kopieer de Assistant ID** (UUID bovenaan).

---

## 3. Telefoonnummer kopen

In Vapi dashboard → Phone Numbers → Buy → kies NL of EU nummer
(`+31...` werkt het beste voor NL outreach). Kost ±$1.50/maand.

Of: koppel een bestaand Twilio sub-account.

**Kopieer de Phone Number ID** (UUID).

---

## 4. Hermes Settings invullen in het sales portaal

`https://viralconversionsweb.onrender.com/sales` → tab **Bel Lijst** →
**⚙️ Settings** knop in het Hermes paneel:

- **Vapi Assistant ID** → plak de UUID uit stap 2
- **Vapi Phone Number ID** → plak de UUID uit stap 3
- (Optioneel) System prompt + first message override
- ElevenLabs voice ID → puur als referentie
- Max calls / parallel — voorbeeld: 50 / 3
- Default filters → ✓ "alleen prospects zonder website" + ✓ "alleen nog-niet-benaderde"

Klik **Opslaan**. De badge in het Hermes paneel springt naar groen
**"✓ Klaar voor gebruik"**.

---

## 5. Test-call

In de Settings modal staat onderaan een **📞 Test bel** knop.
Vul je eigen nummer in (`+316...`) en klik. Vapi belt je binnen 10s.

Controleer:
- Stem klinkt natuurlijk
- Hermes opent met de juiste zin
- Het script voelt natuurlijk
- Webhook landt in de server logs (`[VAPI-WEBHOOK]`)

Iteratie hier is normaal — pas system prompt + voice aan tot het goed klinkt.

---

## 6. Live gaan

1. Klik **🚀 Start Hermes** in het Hermes paneel
2. Kies aantal calls (start met **20** voor de eerste echte ronde)
3. Parallel: **3**
4. Niche filter: leeg = alles
5. ✓ Alleen prospects zonder website
6. ✓ Alleen nog-niet-benaderde
7. Klik **🚀 Start**

De live status bar verschijnt onderin het paneel. Polling elke 8s,
counts worden bijgewerkt zodra calls binnenkomen.

**Resultaat per prospect** (zie 📋 Laatste run modal):
- 🔥 **Warm** → er staat een nieuwe rij in **Warm Leads** met `added_by_name = "Hermes (AI)"`
- ✅ **Benaderd** → prospect.called=true; bel je niet nog een keer
- 🚫 **Niet opgenomen** → prospect.called blijft false; iemand kan later proberen
- ❌ **Failed / ongeldig nummer** → loggegevens in de run-detail; eventueel handmatig opschonen

---

## 7. Cron (dagelijkse auto-run)

In **Settings** modal:
- ✓ **Hermes automatisch elke werkdag draaien**
- Tijdstip: `10:00`
- ✓ Alleen werkdagen
- Opslaan

In Render dashboard → **Cron Jobs** → **New Cron Job**:
- Schedule: `*/10 * * * *` (elke 10 minuten)
- Command:
  ```
  curl -s -X POST https://viralconversionsweb.onrender.com/api/sales/hermes/cron-tick \
       -H "x-cron-secret: $CRON_SECRET"
  ```

De endpoint checkt zelf of NU binnen het ±10min tijdvenster valt; alleen
binnen het venster wordt een run getriggerd. Buiten dat venster: noop +
`{ok:true, skipped:...}` response.

Alternatief zonder Render Cron: gebruik [cron-job.org](https://cron-job.org)
of een GitHub Actions schedule met `curl` workflow.

---

## 8. Veelvoorkomende issues

| Symptoom | Oorzaak | Fix |
|---|---|---|
| "VAPI_API_KEY ontbreekt" toast | env var staat niet op server | Voeg toe in Render → Environment → redeploy |
| Calls starten maar niemand pakt op | mogelijk anti-spam blocking | Probeer ander Vapi nummer / wacht 24u |
| Webhook komt niet binnen | Vapi assistant heeft geen Server URL | Vapi → assistant → Advanced → vul webhook URL in |
| `invalid secret` 401 in logs | `x-vapi-secret` header klopt niet | Match `VAPI_WEBHOOK_SECRET` env met Server URL Secret in Vapi |
| Warm leads worden niet aangemaakt | AI roept `mark_warm_lead` niet aan | Sleutel de system prompt — vermeld expliciet wanneer de tool gebruikt moet worden |
| Te veel parallelle calls drukken op je Vapi credits | te hoge `max_parallel` | Verlaag naar 2-3 in Settings |

---

## 9. Kosten-richtlijn

Indicatief voor NL B2B cold calls:
- Vapi LLM + voice + telefoon = **±$0.10–0.20 per minuut**
- Gemiddelde gespreksduur cold call: 1.5-2 min
- 50 calls × 1.7 min × $0.15 ≈ **$13 per ronde**
- 1 ronde/dag × 22 werkdagen × $13 = **±$285/maand**

Bij 5% warm rate → 50 calls = 2.5 warme leads/ronde → 55/maand.

Schaal langzaam op naar 200 calls/dag wanneer je het script vertrouwt.
