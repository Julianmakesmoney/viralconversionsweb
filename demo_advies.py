"""
bereken_advies — het hart van de demo-aanvraag funnel.

Pure functie (geen DB, geen I/O, geen side-effects): input van stap 1-3 → advies.
Wordt server-side aangeroepen via POST /api/demo/advies en het resultaat wordt
opgeslagen op de lead, zodat de admin/onboarding-dash exact ziet wat de prospect
zag.

AANNAMES (bevestigd/geflagd in het plan):
- Prijs is per MAAND (retainer): €250 eerste module + €50 per extra + €40 hosting.
  Website eenmalig (€600 visitekaartje / €850 met boeking&chatbot), €0 bij 2+ modules.
- Niche-gedrag (triage / no-show / gat-opvulling / offerte-opvolging) zijn
  sub-modi/flavours van de Telefoon-/Offertes-modules, niet losse geprijsde modules.
- Waardeschatting (indicatie) alleen bij GENEREER-framing (capacity == 'ja').
- Bucket-middens CALLS: 0-5→3, 5-15→10, 15-30→22, 30+→35.
  VALUE-ondergrenzen: <50→25, 50-150→50, 150-500→150, 500-2000→500, 2000+→2000.
"""

# ── Constanten ────────────────────────────────────────────────────────────────
BRANCHES = ('loodgieter', 'stukadoor', 'kapper', 'tandarts', 'restaurant', 'b2b', 'anders')
PIJN_CODES = ('TEL', 'MSG', 'OFF', 'NOS', 'FAC', 'LEAD')

CALLS_RANK = {'0-5': 0, '5-15': 1, '15-30': 2, '30+': 3}
CALLS_MID = {'0-5': 3, '5-15': 10, '15-30': 22, '30+': 35}
VALUE_RANK = {'<50': 0, '50-150': 1, '150-500': 2, '500-2000': 3, '2000+': 4}
VALUE_LOW = {'<50': 25, '50-150': 50, '150-500': 150, '500-2000': 500, '2000+': 2000}

# ── Kwantificeer-vragen (stap 3b) → middens voor de uren-berekening ──────────
# Per gekozen pijn één extra bucket-vraag; alleen wat een getal oplevert.
QUANT_MID = {
    'TEL': {'bijna_geen': 0, '1-2': 1.5, '3-5': 4, 'meer_dan_5': 6},   # gemiste calls/dag
    'MSG': {'<10': 5, '10-25': 17, '25-50': 37, '50+': 60},            # per dag
    'OFF': {'<5': 3, '5-15': 10, '15-40': 27, '40+': 55},              # per maand
    'NOS': {'<5': 3, '5-15': 10, '15-30': 22, '30+': 40},              # per maand
    'FAC': {'<10': 5, '10-30': 20, '30-75': 52, '75+': 100},           # per maand
}
# code → (categorie, label, minuten(mid), oplossende module)
_UREN_SPEC = {
    'TEL': ('telefoon',  'telefoon',      lambda m: m * 26 * 2,   'telefoon'),    # 2 min per gemiste call
    'MSG': ('berichten', 'appjes & mail', lambda m: m * 26 * 1.5, 'whatsapp'),    # 1,5 min per bericht (was 3, onrealistisch hoog)
    'OFF': ('offertes',  'offertes',      lambda m: m * 15,       'offertes'),    # 15 min per offerte (was 20)
    'NOS': ('noshows',   'no-shows',      lambda m: m * 12,       'telefoon'),    # 12 min per no-show (was 15)
    'FAC': ('facturen',  'facturen',      lambda m: m * 5,        'facturatie'),  # 5 min per factuur (was 6)
}


def compute_uren(pijn_set, q):
    """Harde uren/maand uit zijn eigen antwoorden. Alleen gekozen pijn met een
    ingevuld kwantificeer-antwoord. Returns {total, breakdown[]} of None."""
    q = q or {}
    breakdown = []
    for code, (cat, label, fn, module) in _UREN_SPEC.items():
        if code not in pijn_set:
            continue
        mid = QUANT_MID.get(code, {}).get(q.get(code))
        if mid is None:
            continue
        uren = int(round(fn(mid) / 60.0))
        if uren >= 1:
            breakdown.append({'cat': cat, 'label': label, 'uren': uren, 'module': module})
    if not breakdown:
        return None
    return {'total': sum(b['uren'] for b in breakdown), 'breakdown': breakdown}

# Branche-sets voor module-/niche-/website-regels
OFFERTE_BRANCHES = {'loodgieter', 'stukadoor', 'b2b'}
REVIEW_BRANCHES = {'kapper', 'tandarts', 'restaurant', 'loodgieter'}
TRIAGE_BRANCHES = {'loodgieter', 'stukadoor'}          # spoed-triage aan, no-show uit
NOSHOW_BRANCHES = {'kapper', 'tandarts', 'restaurant'}  # no-show aan
GAPFILL_BRANCHES = {'kapper', 'tandarts', 'restaurant'}  # gat-opvulling aan
OFFERTE_OPVOLG_BRANCHES = {'b2b'}                        # offerte-opvolging aan

MODULE_ORDER = ('telefoon', 'whatsapp', 'offertes', 'facturatie', 'reactivatie', 'reviews')
MODULE_NAAM = {
    'telefoon':   'Telefoon-assistent',
    'whatsapp':   'WhatsApp & mail-opvolging',
    'offertes':   'Offertes & opvolging',
    'facturatie': 'Facturatie & aanmaningen',
    'reactivatie':'Klant-reactivatie',
    'reviews':    'Reviews & reputatie',
}

# Per-module maandwaarde: uren bespaard (default) + extra klanten/mnd (× waarde).
_UREN_DEFAULT = {'telefoon': 5, 'whatsapp': 4, 'offertes': 4, 'facturatie': 3, 'reactivatie': 1, 'reviews': 1}
_KLANTEN_PM   = {'whatsapp': 1, 'offertes': 1.5, 'reactivatie': 1.5, 'reviews': 0.5, 'facturatie': 0}


def module_waarde(pijn_set, quant, value, calls):
    """Per module de geschatte maandwaarde: uren bespaard + extra omzet — voor ÁLLE
    modules, zodat het scherm live optelt op basis van wat de klant aanvinkt.
    Conservatief: harde uren uit zijn antwoorden + VALUE_LOW × gecapte extra klanten."""
    uren_map = {}
    u = compute_uren(pijn_set, quant)
    if u:
        for b in u['breakdown']:
            uren_map[b['module']] = uren_map.get(b['module'], 0) + b['uren']
    value_eur = VALUE_LOW.get(value, 25)
    gemiste = CALLS_MID.get(calls, 3) * 26 * 0.25                # gemiste calls/mnd
    out = {}
    for k in MODULE_ORDER:
        uren = uren_map.get(k, _UREN_DEFAULT.get(k, 0))
        klanten = min(gemiste * 0.30, 6) if k == 'telefoon' else _KLANTEN_PM.get(k, 0)
        out[k] = {'uren': int(round(uren)), 'omzet': int(round((klanten * value_eur) / 50.0) * 50)}
    return out

# Prijs-model komt uit prijzen.py (bron van waarheid).
from prijzen import (LADDER, LADDER_MIDDLE, ladder_price, BELMINUTEN, VOORRANG_TICKETS,
                     PRIJS_SETUP, PRIJS_SETUP_PER_MODULE, PRIJS_VISITEKAARTJE, PRIJS_BOEKING,
                     PRIJS_BOEKING_MND, BOEKING_BRANCHES, WEBSITE_GRATIS_VANAF_TAKEN, PRIJS_WEBSITE_MND,
                     PRIJS_ADS_SETUP)
REVSHARE_TEKST = '15% van aantoonbaar gegenereerde omzet'
INDICATIE_DISCLAIMER = 'Indicatie op basis van je eigen cijfers — geen garantie.'


def _norm(v):
    return (v or '').strip().lower()


def _module_regel(key, branche, niche, framing):
    """Eén situatie-specifieke regel per module (niet generiek)."""
    if key == 'telefoon':
        if niche['triage']:
            return 'Neemt op terwijl je werkt. Spoed komt bij jou, de rest wordt ingepland.'
        if niche['noShow']:
            return 'Neemt afspraken aan en stuurt reminders. Minder no-shows.'
        if branche == 'restaurant':
            return 'Neemt reserveringen aan tijdens de drukte.'
        if branche == 'b2b':
            return 'Vangt elk gemist gesprek op als terugbel-taak.'
        return 'Vangt elk gemist telefoontje op. Nooit meer een klant mislopen.'
    if key == 'whatsapp':
        return 'Beantwoordt appjes en mail automatisch. Inbox leeg, ook ’s avonds.'
    if key == 'offertes':
        if niche['offerteOpvolging']:
            return 'Maakt offertes en belt automatisch na tot er een ja of nee is.'
        return 'Maakt offertes en zit er automatisch achteraan.'
    if key == 'facturatie':
        return 'Stuurt facturen en aanmaningen automatisch. Jij zit er niet achteraan.'
    if key == 'reactivatie':
        return 'Haalt oude en slapende klanten terug.'
    if key == 'reviews':
        if niche['gapFill']:
            return 'Vraagt reviews na elke klant en vult gaten in je agenda.'
        return 'Vraagt automatisch reviews na elke klant.'
    return ''


def bepaal_ticket(input):
    """Leid low/middle/high af uit VALUE (+ CAPACITY/CALLS). GEEN nieuwe vraag —
    puur afgeleid. Startpunt, in admin overschrijfbaar. Returns (ticket, reason)."""
    value = input.get('value') if input.get('value') in VALUE_RANK else '<50'
    capacity = _norm(input.get('capacity'))
    calls = input.get('calls') if input.get('calls') in CALLS_RANK else '0-5'
    calls_rank = CALLS_RANK[calls]
    if value == '<50':
        ticket = 'low'
    elif value == '50-150':
        ticket = 'middle' if (capacity == 'ja' and calls_rank >= CALLS_RANK['15-30']) else 'low'
    elif value == '150-500':
        ticket = 'middle'
    elif value in ('500-2000', '2000+'):
        ticket = 'high'
    else:
        ticket = 'middle'
    reason = f'klantwaarde {value}' + (f' + capaciteit {capacity}' if capacity else '')
    return ticket, reason


def bereken_advies(input):
    """input dict → advies dict. Zie module-docstring voor de aannames."""
    branche = _norm(input.get('branche'))
    if branche not in BRANCHES:
        branche = 'anders'
    website_huidig = _norm(input.get('website'))
    pijn = [p for p in (input.get('pijn') or []) if p in PIJN_CODES]
    pijn_set = set(pijn)
    capacity = _norm(input.get('capacity'))            # 'ja' | 'beetje' | 'nee'
    calls = input.get('calls') if input.get('calls') in CALLS_RANK else '0-5'
    value = input.get('value') if input.get('value') in VALUE_RANK else '<50'
    quant = input.get('q') or {}                       # {TEL:'meer_dan_5', MSG:'10-25', ...}

    calls_rank = CALLS_RANK[calls]
    value_rank = VALUE_RANK[value]

    # ── Framing (bepaalt de hele toon) ───────────────────────────────────────
    if capacity == 'ja':
        framing = 'genereer'
    elif capacity == 'nee':
        framing = 'ontlast'
    else:
        framing = 'bescheiden'   # 'beetje' of onbekend

    vol = (capacity == 'nee')

    # ── Modules: PUUR op de gekozen pijn (kritisch & specifiek) ───────────────
    # Elke pijn mapt op precies de module die 'm oplost. Branche is GEEN
    # auto-aanbeveling meer — die vult alleen aan tot we op minimaal 2 zitten.
    PIJN_MODULE = {
        'TEL': 'telefoon', 'NOS': 'telefoon', 'MSG': 'whatsapp',
        'OFF': 'offertes', 'FAC': 'facturatie', 'LEAD': 'reactivatie',
    }
    enabled = set()
    for code in pijn:
        m = PIJN_MODULE.get(code)
        if not m:
            continue
        if m == 'reactivatie' and vol:            # vol? geen extra-omzet-module opdringen
            continue
        enabled.add(m)
    # Hard signaal: structureel veel gemiste calls → telefoon is dan wél terecht
    if calls_rank >= CALLS_RANK['15-30']:
        enabled.add('telefoon')

    # ── Minimaal 2 aanbevelingen: kritisch aanvullen (branche-fit eerst, dan vangnet) ──
    _fill = []
    if branche in OFFERTE_BRANCHES:
        _fill.append('offertes')
    if branche in REVIEW_BRANCHES:
        _fill.append('reviews')
    _fill += ['telefoon', 'whatsapp']
    if not vol:
        _fill.append('reactivatie')
    _fill += ['facturatie', 'reviews', 'offertes']
    for cand in _fill:
        if len(enabled) >= 2:
            break
        enabled.add(cand)

    modules_keys = [k for k in MODULE_ORDER if k in enabled]
    modules_count = len(modules_keys)

    # ── Niche-gedrag (klant kiest dit niet) ──────────────────────────────────
    niche = {
        'template': branche,
        'triage':   branche in TRIAGE_BRANCHES,
        'noShow':   branche in NOSHOW_BRANCHES,
        'gapFill':  branche in GAPFILL_BRANCHES,
        'offerteOpvolging': branche in OFFERTE_OPVOLG_BRANCHES,
    }

    modules = [{'key': k, 'naam': MODULE_NAAM[k], 'regel': _module_regel(k, branche, niche, framing)}
               for k in modules_keys]

    # ── Demo-type: chatbot + boekingssysteem alleen bij passende branche ──────
    demo_type = 'met_boeking' if branche in BOEKING_BRANCHES else 'simpel'

    # ── Website (visitekaartje, gratis bij 2+) + optionele boeking-add-on ────
    website_variant = 'met_boeking_chatbot' if demo_type == 'met_boeking' else 'visitekaartje'
    website_gratis = modules_count >= WEBSITE_GRATIS_VANAF_TAKEN
    website_base_eenmalig = 0 if website_gratis else PRIJS_VISITEKAARTJE   # 600 of gratis
    boeking_default = (demo_type == 'met_boeking')
    website_eenmalig = website_base_eenmalig + (PRIJS_BOEKING if boeking_default else 0)

    # ── Ticket-inschatting + medewerker-ladder (verpakking, geen losse optelsom) ──
    ticket, ticket_reason = bepaal_ticket(input)
    maandtotaal = ladder_price(ticket, modules_count)

    prijs = {
        'modulesCount':   modules_count,
        'ladder':         LADDER[ticket],          # ticket-geschaald, 7 waarden
        'ladderMiddle':   LADDER_MIDDLE,           # neutrale midden-ladder (voor 'alle prijzen')
        'maandtotaal':    maandtotaal,             # aanbevolen setup
        'allIn':          ladder_price(ticket, 7),
        'instap':         ladder_price(ticket, 1),
        'opzet':          PRIJS_SETUP,               # legacy
        'opzetPerModule': PRIJS_SETUP_PER_MODULE,    # €100 per taak, altijd, geen korting
        'websiteMaand':   PRIJS_WEBSITE_MND,         # €30/mnd bij <2 modules (anders gratis)
        'adsSetup':       PRIJS_ADS_SETUP,           # marktonderzoek/Google Ads: €350 opzet + 15% nieuwe omzet
        'demoType':       demo_type,
        'websiteVariant': website_variant,
        'websiteBase':    PRIJS_VISITEKAARTJE,   # 600 (visitekaartje, vóór gratis)
        'websiteBaseEenmalig': website_base_eenmalig,    # 600 of 0
        'websiteEenmalig':website_eenmalig,              # incl. default add-on (record)
        'websiteGratis':  website_gratis,
        'boekingPrijs':   PRIJS_BOEKING,           # 250 eenmalig (nooit gratis)
        'boekingMaand':   PRIJS_BOEKING_MND,       # + €30/mnd
        'boekingDefault': boeking_default,
        'gratisMaanden':  12,                            # gratis geldt bij 12 mnd afname
        'revshare':       REVSHARE_TEKST,
        'revshareProminent': (framing == 'genereer'),   # groot bij genereer, klein bij ontlast
    }

    # ── Waardeschatting (ALLEEN bij genereer-framing) ────────────────────────
    indicatie = None
    if framing == 'genereer':
        calls_gem = CALLS_MID[calls]
        value_low = VALUE_LOW[value]
        gemiste_calls = calls_gem * 26 * 0.25
        laag = gemiste_calls * 0.30 * value_low
        hoog = gemiste_calls * 0.50 * value_low
        indicatie = {
            'laag': int(round(laag / 50.0) * 50),
            'hoog': int(round(hoog / 50.0) * 50),
            'disclaimer': INDICATIE_DISCLAIMER,
        }

    # ── Koppen (framing-afhankelijk) ─────────────────────────────────────────
    if framing == 'genereer':
        koppen = {
            'titel': 'Je loopt werk mis dat je wél aankan',
            'sub':   'Elk gemist telefoontje en contact buiten je uren is een klant die naar de concurrent belt. Dit zetten we voor je aan.',
        }
    elif framing == 'ontlast':
        koppen = {
            'titel': 'Je zit vol — dan is méér werk niet je probleem',
            'sub':   'We halen de ballast weg: minder telefoon, geen no-shows, sneller betaald, en we pikken alleen de winstgevende spoedklus eruit.',
        }
    else:
        koppen = {
            'titel': 'Zo haal je meer uit de klanten die je al hebt',
            'sub':   'Een paar dingen die nu blijven liggen, lopen straks vanzelf — zonder dat je omvalt.',
        }

    # ── Uren-getal (hard, uit zijn eigen antwoorden) ─────────────────────────
    uren = compute_uren(pijn_set, quant)

    return {
        'framing':  framing,
        'ticket':   ticket,
        'ticketReason': ticket_reason,
        'belminuten': BELMINUTEN[ticket],
        'voorrang': (ticket in VOORRANG_TICKETS),
        'koppen':   koppen,
        'modules':  modules,
        'moduleWaarde': module_waarde(pijn_set, quant, value, calls),   # per module: uren + omzet (live optellen)
        'niche':    niche,
        'demo_type': demo_type,
        'website':  {'variant': website_variant, 'prijs': website_base_eenmalig, 'losPrijs': PRIJS_VISITEKAARTJE},
        'prijs':    prijs,
        'uren':     uren,
        'indicatie': indicatie,
        'input': {
            'branche': branche, 'website_huidig': website_huidig, 'pijn': pijn,
            'capacity': capacity, 'calls': calls, 'value': value, 'q': quant,
        },
    }
