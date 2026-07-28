"""
bereken_webshop_advies — routing voor de demo-WEBSHOP funnel.

Parallelle pure functie naast bereken_advies (website): zelfde output-shape,
andere vragen / routing / framing / prijzen. Geen gratis bouw, geen afschrijving,
geen lock — alles maandelijks opzegbaar.

AANNAMES (bevestigd/geflagd):
- Prijs: €2.500 eenmalig + €150/mnd (altijd betaald) + modules-ladder (€250 eerste,
  +€50 per extra). +15% van aantoonbaar teruggehaalde omzet.
- Framing op OMZET: 'nog_niks' → BOUW (niks terug te halen: geen cart/reactivatie/
  indicatie), anders → HERSTEL (verlaten winkelwagen is het verhaal).
- KANALEN >= 3 → consolidatie is de hoofdpitch ("alles uit één overzicht").
- Uren-term 'ORDERS × 2min' is gekoppeld aan de VERSNIPPERD-pijn (versnipperde
  systemen = handmatig orders verwerken). [judgment call — makkelijk te wijzigen]
"""

# ── Buckets / ranks ──────────────────────────────────────────────────────────
OMZET_RANK       = {'nog_niks': 0, '<5000': 1, '5000-15000': 2, '15000-50000': 3, '50000+': 4}
OMZET_ONDERGRENS = {'nog_niks': 0, '<5000': 1000, '5000-15000': 5000, '15000-50000': 15000, '50000+': 50000}
ORDERS_RANK      = {'<25': 0, '25-100': 1, '100-500': 2, '500+': 3}
ORDERS_MID       = {'<25': 12, '25-100': 60, '100-500': 300, '500+': 800}
PRODUCTEN_RANK   = {'<25': 0, '25-100': 1, '100-500': 2, '500+': 3}

PIJN_CODES   = ('CART', 'SERVICE', 'VOORRAAD', 'REACTIVATIE', 'REVIEWS', 'ADMIN')
KANAAL_CODES = ('eigen', 'bol', 'amazon', 'marktplaats', 'fysiek', 'social')

# Kwantificeer-vragen (stap 3b) — alleen bij de betreffende pijn
QUANT_MID = {
    'SERVICE':     {'<5': 3, '5-15': 10, '15-40': 27, '40+': 55},                 # klantvragen/dag
    'VOORRAAD':    {'dagelijks': 22, 'paar_week': 10, 'wekelijks': 4, 'misgaat': 1},  # keer/maand
}

# ── Prijs: alles uit prijzen.py (bron van waarheid) ─────────────────────────
from prijzen import (LADDER, LADDER_MIDDLE, ladder_price,
                     PRIJS_WEBSHOP_BOUW, PRIJS_WEBSHOP_MND, WEBSHOP_AFSCHRIJF_PER_MND, PRIJS_ADS_SETUP)
REVSHARE_TEKST       = ''   # +15%-revshare-pitch verwijderd uit de demo (niet meer tonen)
INDICATIE_DISCLAIMER = 'Indicatie op basis van je eigen cijfers — geen garantie.'


def bepaal_webshop_ticket(omzet):
    """Webshop schaalt op OMZET, niet op ticketgrootte — een webshop is nooit low.
    €15k+/mnd → 'schaal' (1,3×); anders 'draaiend' (middle)."""
    return 'schaal' if OMZET_RANK.get(omzet, 0) >= OMZET_RANK['15000-50000'] else 'draaiend'

# Losse-tools-vergelijking: aantal regels volgt uit 'in hoeveel systemen werk je'
# (VERSNIPPERD). Eerste 4 = het vaste €145-lijstje uit de spec.
LOSSE_TOOLS = [
    ('Shopwinkel', 30), ('Winkelwagen-app', 40), ('Reviews-tool', 25),
    ('Voorraad-app', 50), ('Klantenservice-tool', 35), ('E-mail & marketing', 45),
]

# ── Modules ──────────────────────────────────────────────────────────────────
WEBSHOP_MODULE_ORDER = ('cart', 'service', 'voorraad', 'reactivatie', 'reviews', 'facturatie')
MODULE_NAAM = {
    'cart':        'Verlaten winkelwagen',
    'service':     'AI Klantenservice',
    'voorraad':    'Voorraad & kanalen',
    'reactivatie': 'Reactivatie',
    'reviews':     'Reviews',
    'facturatie':  'Facturatie',
}


def _norm(v):
    return (v or '').strip().lower()


def _module_regel(key, n_kanalen, framing):
    if key == 'cart':
        return 'Haalt mensen terug die hun mandje lieten staan.'
    if key == 'service':
        return '"Waar blijft mijn pakket?" — automatisch beantwoord.'
    if key == 'voorraad':
        return 'Al je kanalen uit één voorraad en één overzicht.' if n_kanalen >= 2 else 'Houdt je voorraad automatisch bij.'
    if key == 'reactivatie':
        return 'Mailt oude klanten met een reden om terug te komen.'
    if key == 'reviews':
        return 'Vraagt automatisch reviews na elke bestelling.'
    if key == 'facturatie':
        return 'Facturen en administratie automatisch geregeld.'
    return ''


def compute_uren(pijn_set, orders, kanalen, q):
    """Harde uren/maand uit zijn eigen antwoorden. Alleen gekozen pijn.
    Per regel een module zodat de toggle de uren kan laten bewegen."""
    q = q or {}
    n_kan = max(1, len(kanalen))
    bd = []

    if 'SERVICE' in pijn_set:
        m = QUANT_MID['SERVICE'].get(q.get('SERVICE'))
        if m is not None:
            u = int(round(m * 26 * 2 / 60.0))    # 2 min per klantvraag (was 4, onrealistisch hoog)
            if u >= 1:
                bd.append({'label': 'klantvragen', 'uren': u, 'module': 'service'})

    if 'VOORRAAD' in pijn_set:
        f = QUANT_MID['VOORRAAD'].get(q.get('VOORRAAD'))
        if f is not None:
            u = int(round(f * n_kan * 15 / 60.0))    # 15 min per voorraad-update (was 20)
            if u >= 1:
                bd.append({'label': 'voorraad bijwerken', 'uren': u, 'module': 'voorraad'})

    # (Geen los 'orders verwerken'-uren: geen module neemt het weg → dat zou de
    #  toggle verzwakken. VERSNIPPERD wordt beantwoord door de vergelijkingstabel.)
    if 'ADMIN' in pijn_set and orders in ORDERS_MID:
        u = int(round(ORDERS_MID[orders] * 1.5 / 60.0))    # 1,5 min per order-admin (was 2)
        if u >= 1:
            bd.append({'label': 'facturen', 'uren': u, 'module': 'facturatie'})

    if not bd:
        return None
    return {'total': sum(b['uren'] for b in bd), 'breakdown': bd}


_UREN_DEFAULT_SHOP = {'service': 6, 'voorraad': 5, 'facturatie': 3, 'cart': 0, 'reactivatie': 1, 'reviews': 0}


def module_waarde(pijn_set, orders, kanalen, quant, omzet):
    """Per webshop-module de geschatte maandwaarde: uren bespaard + extra omzet —
    voor ÁLLE modules, zodat het scherm live optelt op basis van de selectie.
    Omzet-modules (cart/reactivatie/reviews) schalen op de maandomzet-ondergrens."""
    uren_map = {}
    u = compute_uren(pijn_set, orders, kanalen, quant)
    if u:
        for b in u['breakdown']:
            uren_map[b['module']] = uren_map.get(b['module'], 0) + b['uren']
    basis = OMZET_ONDERGRENS.get(omzet, 0)                        # maandomzet-ondergrens
    omzet_pm = {'cart': basis * 2 * 0.06, 'reactivatie': basis * 0.03, 'reviews': basis * 0.015}
    out = {}
    for k in WEBSHOP_MODULE_ORDER:
        uren = uren_map.get(k, _UREN_DEFAULT_SHOP.get(k, 0))
        out[k] = {'uren': int(round(uren)), 'omzet': int(round(omzet_pm.get(k, 0) / 10.0) * 10)}
    return out


def bereken_webshop_advies(input):
    """input dict → advies dict (zelfde shape als bereken_advies, type='webshop')."""
    pijn = [p for p in (input.get('pijn') or []) if p in PIJN_CODES]
    pijn_set = set(pijn)
    kanalen = [k for k in (input.get('kanalen') or []) if k in KANAAL_CODES]
    omzet = input.get('omzet') if input.get('omzet') in OMZET_RANK else 'nog_niks'
    orders = input.get('orders') if input.get('orders') in ORDERS_RANK else '<25'
    producten = input.get('producten') if input.get('producten') in PRODUCTEN_RANK else '<25'
    quant = input.get('q') or {}

    omzet_rank = OMZET_RANK[omzet]
    orders_rank = ORDERS_RANK[orders]
    n_kanalen = len(kanalen)

    # ── Framing ──────────────────────────────────────────────────────────────
    framing = 'bouw' if omzet == 'nog_niks' else 'herstel'
    kanaal_pitch = n_kanalen >= 3

    # ── Modules: PUUR op de gekozen pijn (kritisch & specifiek), net als de
    #    website-funnel. Geen volume-auto-adds; alleen aanvullen tot minimaal 2. ──
    PIJN_MODULE = {
        'CART': 'cart', 'SERVICE': 'service', 'VOORRAAD': 'voorraad',
        'REACTIVATIE': 'reactivatie', 'REVIEWS': 'reviews', 'ADMIN': 'facturatie',
    }
    enabled = set()
    for code in pijn:
        m = PIJN_MODULE.get(code)
        if m:
            enabled.add(m)
    # Minimaal 2 aanbevelingen zodra er iets speelt (pijn gekozen of draaiende shop).
    if pijn or omzet != 'nog_niks':
        for cand in ('service', 'reviews', 'reactivatie', 'voorraad', 'cart', 'facturatie'):
            if len(enabled) >= 2:
                break
            enabled.add(cand)

    modules_keys = [k for k in WEBSHOP_MODULE_ORDER if k in enabled]
    modules_count = len(modules_keys)
    modules = [{'key': k, 'naam': MODULE_NAAM[k], 'regel': _module_regel(k, n_kanalen, framing)}
               for k in modules_keys]

    # ── Koppen ───────────────────────────────────────────────────────────────
    if kanaal_pitch:
        koppen = {'titel': 'Al je kanalen uit één overzicht',
                  'sub': 'Nu leeft alles los. We brengen het samen in één shop — precies wat Shopify je niet geeft.'}
    elif framing == 'bouw':
        koppen = {'titel': 'Eerst een shop die verkoopt',
                  'sub': 'Daarna het slimme spul. Zonder verkeer werkt de rest nog niet — we bouwen je shop, en zodra er omzet draait gaat de rest vanzelf werken.'}
    else:
        koppen = {'titel': 'Je verliest nu omzet die al binnen was',
                  'sub': 'Mensen vullen hun mandje en haken af. Dat halen we terug.'}

    # ── Uren ─────────────────────────────────────────────────────────────────
    uren = compute_uren(pijn_set, orders, kanalen, quant)

    # ── Indicatie (ALLEEN bij HERSTEL — er is niks terug te halen bij BOUW) ──
    indicatie = None
    if framing == 'herstel':
        verlaten_waarde = OMZET_ONDERGRENS[omzet] * 2
        laag = verlaten_waarde * 0.04
        hoog = verlaten_waarde * 0.08
        indicatie = {
            'laag': int(round(laag / 10.0) * 10),
            'hoog': int(round(hoog / 10.0) * 10),
            'disclaimer': INDICATIE_DISCLAIMER,
        }

    # Hoofdgetal: bij HERSTEL met hoge omzet is de verlaten winkelwagen het sterkst
    if indicatie and (omzet_rank >= OMZET_RANK['15000-50000'] or uren is None):
        hoofdgetal = 'indicatie'
    else:
        hoofdgetal = 'uren'

    # ── Prijs: ladder op OMZET-schaal (draaiend/schaal) + €2.500 eenmalig, nooit gratis ──
    webshop_ticket = bepaal_webshop_ticket(omzet)
    maandtotaal = ladder_price(webshop_ticket, modules_count) or PRIJS_WEBSHOP_MND   # 0 taken → kale shop €150
    prijs = {
        'type':           'webshop',
        'eenmalig':       PRIJS_WEBSHOP_BOUW,
        'shopMaand':      PRIJS_WEBSHOP_MND,
        'modulesCount':   modules_count,
        'ladder':         LADDER[webshop_ticket],   # draaiend (middle) of schaal (1,3×)
        'ladderMiddle':   LADDER_MIDDLE,             # neutraal (voor 'alle prijzen')
        'maandtotaal':    maandtotaal,
        'allIn':          ladder_price(webshop_ticket, 7),
        'instap':         ladder_price(webshop_ticket, 1),
        'afschrijfPerMnd': WEBSHOP_AFSCHRIJF_PER_MND,
        'adsSetup':       PRIJS_ADS_SETUP,           # marktonderzoek/Google Ads: €350 opzet + 15% nieuwe omzet
        'gratis':         False,
        'revshare':       REVSHARE_TEKST,
        'revshareProminent': (framing == 'herstel'),
    }

    # ── Vergelijking: alternatief = losse tools stapelen ─────────────────────
    # Aantal regels schaalt mee met het aantal aanbevolen modules (meer services =
    # meer losse tools die je anders zou stapelen), minimaal het vaste 4-tools-lijstje.
    n_tools = min(max(modules_count, 4), len(LOSSE_TOOLS))
    tool_rows = LOSSE_TOOLS[:n_tools]
    vergelijking = {
        'alt': 'losse_tools',
        'systemen': n_tools,
        'losseTools': [{'label': l, 'prijs': p} for l, p in tool_rows],
        'losseToolsPrijs': sum(p for _, p in tool_rows),
    }

    return {
        'type':      'webshop',
        'framing':   framing,
        'ticket':    webshop_ticket,
        'ticketReason': 'omzet ' + omzet,
        'koppen':    koppen,
        'modules':   modules,
        'moduleWaarde': module_waarde(pijn_set, orders, kanalen, quant, omzet),   # per module: uren + omzet (live optellen)
        'uren':      uren,
        'indicatie': indicatie,
        'hoofdgetal': hoofdgetal,
        'kanaalPitch': kanaal_pitch,
        'prijs':     prijs,
        'vergelijking': vergelijking,
        'demo_type': 'webshop',
        'input': {
            'pijn': pijn, 'kanalen': kanalen, 'omzet': omzet, 'orders': orders,
            'producten': producten, 'shop': _norm(input.get('shop')),
            'shop_url': (input.get('shop_url') or '').strip(),
            'wat_verkoop': (input.get('wat_verkoop') or '').strip(),
            'q': quant,
        },
    }
