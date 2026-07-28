#!/usr/bin/env python3
"""Tests voor bereken_advies. Draai: python3 test_demo_advies.py"""
from demo_advies import bereken_advies

_passed = 0
_failed = []


def check(name, cond):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(name)


def A(**kw):
    """Advies voor een basis-input, met overrides."""
    base = {'branche': 'anders', 'website': 'geen', 'pijn': [],
            'capacity': 'ja', 'calls': '0-5', 'value': '<50'}
    base.update(kw)
    return bereken_advies(base)


def keys(adv):
    return {m['key'] for m in adv['modules']}


# ── Telefoon ALTIJD ───────────────────────────────────────────────────────────
check('telefoon-altijd-minimaal', 'telefoon' in keys(A()))
check('telefoon-altijd-vol', 'telefoon' in keys(A(capacity='nee', pijn=[])))

# ── WhatsApp: MSG of CALLS >= 15-30 ──────────────────────────────────────────
check('whatsapp-via-MSG', 'whatsapp' in keys(A(pijn=['MSG'])))
check('whatsapp-via-calls-15-30', 'whatsapp' in keys(A(calls='15-30')))
check('whatsapp-via-calls-30+', 'whatsapp' in keys(A(calls='30+')))
check('whatsapp-uit-zonder-MSG-bij-genoeg', 'whatsapp' not in keys(A(pijn=['TEL', 'FAC'], calls='5-15')))

# ── Offertes: OFF of branche in [loodgieter, stukadoor, b2b] ─────────────────
check('offertes-via-OFF', 'offertes' in keys(A(pijn=['OFF'])))
check('offertes-via-loodgieter', 'offertes' in keys(A(branche='loodgieter')))
check('offertes-via-b2b', 'offertes' in keys(A(branche='b2b')))
check('offertes-uit-kapper-zonder-OFF', 'offertes' not in keys(A(branche='kapper')))

# ── Facturatie: FAC ──────────────────────────────────────────────────────────
check('facturatie-via-FAC', 'facturatie' in keys(A(pijn=['FAC'])))
check('facturatie-uit', 'facturatie' not in keys(A(pijn=['TEL'])))

# ── Reactivatie: LEAD EN capacity != nee ─────────────────────────────────────
check('reactivatie-lead-ja', 'reactivatie' in keys(A(pijn=['LEAD'], capacity='ja')))
check('reactivatie-lead-beetje', 'reactivatie' in keys(A(pijn=['LEAD'], capacity='beetje')))
check('reactivatie-UIT-bij-vol', 'reactivatie' not in keys(A(pijn=['LEAD'], capacity='nee')))
check('reactivatie-uit-zonder-lead', 'reactivatie' not in keys(A(pijn=['TEL'], capacity='ja')))

# ── Reviews: branche in [kapper, tandarts, restaurant, loodgieter] ───────────
check('reviews-kapper', 'reviews' in keys(A(branche='kapper')))
check('reviews-loodgieter', 'reviews' in keys(A(branche='loodgieter')))
check('reviews-uit-b2b', 'reviews' not in keys(A(branche='b2b')))
check('reviews-uit-stukadoor', 'reviews' not in keys(A(branche='stukadoor')))

# ── Kritisch & specifiek: branche voegt NIET meer auto toe zodra er ≥2 pijn-modules zijn ─
check('kritisch-geen-branche-reviews', 'reviews' not in keys(A(branche='kapper', pijn=['TEL', 'FAC'])))
check('kritisch-geen-branche-offertes', 'offertes' not in keys(A(branche='loodgieter', pijn=['MSG', 'FAC', 'TEL'])))
check('altijd-minimaal-2', len(keys(A(pijn=['FAC']))) >= 2)
check('een-pijn-plus-1-aanvulling', 'facturatie' in keys(A(pijn=['FAC'])) and len(keys(A(pijn=['FAC']))) == 2)

# ── Framing ──────────────────────────────────────────────────────────────────
check('framing-genereer', A(capacity='ja')['framing'] == 'genereer')
check('framing-ontlast', A(capacity='nee')['framing'] == 'ontlast')
check('framing-bescheiden', A(capacity='beetje')['framing'] == 'bescheiden')
check('kop-vol', 'zit vol' in A(capacity='nee')['koppen']['titel'].lower())
check('kop-genereer', 'werk mis' in A(capacity='ja')['koppen']['titel'].lower())

# ── Waardeschatting: alleen genereer, altijd range + disclaimer ──────────────
adv_gen = A(capacity='ja', calls='15-30', value='150-500', pijn=['TEL'])
check('indicatie-aanwezig-bij-genereer', adv_gen['indicatie'] is not None)
check('indicatie-is-range', adv_gen['indicatie']['laag'] < adv_gen['indicatie']['hoog'])
check('indicatie-disclaimer', 'geen garantie' in adv_gen['indicatie']['disclaimer'].lower())
check('indicatie-ondergrens-value', adv_gen['indicatie']['laag'] > 0)
# formule: 22*26*0.25=143 ; laag=143*0.30*150=6435→6450 ; hoog=143*0.50*150=10725→10700
check('indicatie-formule-laag', adv_gen['indicatie']['laag'] == 6450)
check('indicatie-formule-hoog', adv_gen['indicatie']['hoog'] == 10700)
check('geen-indicatie-bij-ontlast', A(capacity='nee', calls='30+', value='2000+')['indicatie'] is None)
check('geen-indicatie-bij-bescheiden', A(capacity='beetje', calls='30+', value='2000+')['indicatie'] is None)

# ── 15%-prominentie ──────────────────────────────────────────────────────────
check('revshare-prominent-genereer', A(capacity='ja')['prijs']['revshareProminent'] is True)
check('revshare-klein-ontlast', A(capacity='nee')['prijs']['revshareProminent'] is False)

# ── Website-variant ──────────────────────────────────────────────────────────
check('website-kapper-boeking', A(branche='kapper')['website']['variant'] == 'met_boeking_chatbot')
check('website-restaurant-boeking', A(branche='restaurant')['website']['variant'] == 'met_boeking_chatbot')
check('website-loodgieter-visitekaartje', A(branche='loodgieter')['website']['variant'] == 'visitekaartje')
check('website-b2b-visitekaartje', A(branche='b2b')['website']['variant'] == 'visitekaartje')

# ── Website gratis bij 2+ modules ────────────────────────────────────────────
adv_multi = A(branche='kapper', pijn=['MSG', 'FAC'])   # whatsapp + facturatie = 2 → gratis
check('website-gratis-2plus', adv_multi['website']['prijs'] == 0)
check('website-gratis-flag', adv_multi['prijs']['websiteGratis'] is True)
adv_1 = A(branche='anders', pijn=['MSG'], calls='0-5')  # whatsapp + 1 aanvulling = min-2 modules
check('advies-altijd-min-2', len(keys(adv_1)) >= 2)
check('website-gratis-door-min-2', adv_1['website']['prijs'] == 0)  # min-2 → base altijd gratis

# ── Demo-type: chatbot + boeking alleen bij passende branche ─────────────────
check('demo-type-kapper-boeking', A(branche='kapper')['demo_type'] == 'met_boeking')
check('demo-type-tandarts-boeking', A(branche='tandarts')['demo_type'] == 'met_boeking')
check('demo-type-restaurant-boeking', A(branche='restaurant')['demo_type'] == 'met_boeking')
check('demo-type-loodgieter-simpel', A(branche='loodgieter')['demo_type'] == 'simpel')
check('demo-type-b2b-simpel', A(branche='b2b')['demo_type'] == 'simpel')
check('boeking-default-aan-kapper', A(branche='kapper')['prijs']['boekingDefault'] is True)
check('boeking-default-uit-loodgieter', A(branche='loodgieter')['prijs']['boekingDefault'] is False)
check('boeking-prijs-250', A(branche='kapper')['prijs']['boekingPrijs'] == 250)
check('website-base-600', A(branche='kapper', pijn=[], calls='0-5')['prijs']['websiteBase'] == 600)
# boeking-add-on blijft betaald, óók bij 2+ modules (gratis geldt enkel visitekaartje)
_km = A(branche='kapper', pijn=['MSG', 'FAC'])   # 2+ modules → base gratis, add-on nog €250
check('boeking-betaald-ook-bij-gratis-website', _km['prijs']['websiteBaseEenmalig'] == 0 and _km['prijs']['websiteEenmalig'] == 250)

# ── Ticket-inschatting (afgeleid, geen nieuwe vraag) ─────────────────────────
from demo_advies import bepaal_ticket, ladder_price, LADDER, LADDER_MIDDLE
check('ticket-low-onder-50', bepaal_ticket({'value': '<50'})[0] == 'low')
check('ticket-50-150-low-default', bepaal_ticket({'value': '50-150'})[0] == 'low')
check('ticket-50-150-middle-bij-ja-en-volume', bepaal_ticket({'value': '50-150', 'capacity': 'ja', 'calls': '15-30'})[0] == 'middle')
check('ticket-150-500-middle', bepaal_ticket({'value': '150-500'})[0] == 'middle')
check('ticket-150-500-middle-ook-bij-vol', bepaal_ticket({'value': '150-500', 'capacity': 'nee'})[0] == 'middle')
check('ticket-500-2000-high', bepaal_ticket({'value': '500-2000'})[0] == 'high')
check('ticket-2000plus-high', bepaal_ticket({'value': '2000+'})[0] == 'high')
check('ticket-in-advies', A(value='2000+')['ticket'] == 'high')
check('belminuten-per-ticket', A(value='<50')['belminuten'] == 300 and A(value='2000+')['belminuten'] == 1000)
check('voorrang-alleen-high', A(value='2000+')['voorrang'] is True and A(value='<50')['voorrang'] is False)

# ── Medewerker-ladder (ticket-geschaald, afgerond, expliciet) ────────────────
check('ladder-price-scaling', ladder_price('high', 7) == 950 and ladder_price('middle', 1) == 350 and ladder_price('low', 0) == 0)
check('prijs-2-taken-low', adv_1['prijs']['maandtotaal'] == 380)         # low[1], 2 modules (min-2)
check('prijs-ladder-low', adv_1['prijs']['ladder'] == [295, 380, 445, 495, 540, 570, 595])
adv_3 = A(branche='loodgieter', pijn=['OFF', 'FAC'])                     # offertes + facturatie = 2 (branche voegt NIET toe)
check('prijs-2-taken-count', adv_3['prijs']['modulesCount'] == 2)
check('prijs-2-taken-maand', adv_3['prijs']['maandtotaal'] == 380)       # low[1]
check('prijs-allin-en-instap', adv_3['prijs']['allIn'] == 595 and adv_3['prijs']['instap'] == 295)
check('prijs-laddermiddle-neutraal', adv_3['prijs']['ladderMiddle'] == [350, 450, 530, 590, 650, 700, 750])
check('prijs-high-schaalt-mee', A(branche='loodgieter', pijn=['OFF', 'FAC'], value='2000+')['prijs']['maandtotaal'] == 570)  # high[1]

# ── Niche-gedrag ─────────────────────────────────────────────────────────────
n_lood = A(branche='loodgieter')['niche']
check('niche-loodgieter-triage', n_lood['triage'] is True and n_lood['noShow'] is False)
n_kap = A(branche='kapper')['niche']
check('niche-kapper-noshow-gapfill', n_kap['noShow'] and n_kap['gapFill'] and not n_kap['triage'])
n_b2b = A(branche='b2b')['niche']
check('niche-b2b-offerteopvolging', n_b2b['offerteOpvolging'] and not n_b2b['triage'])
n_rest = A(branche='restaurant')['niche']
check('niche-restaurant-gapfill-noshow', n_rest['gapFill'] and n_rest['noShow'])

# ── Elke module heeft een niet-lege, situatie-specifieke regel ───────────────
adv_full = A(branche='loodgieter', pijn=['OFF', 'MSG', 'LEAD'], capacity='ja', calls='30+', value='2000+')
check('elke-module-heeft-regel', all(m['regel'] for m in adv_full['modules']))
# triage-flavour in de telefoon-regel bij loodgieter
tel_regel = next(m['regel'] for m in adv_full['modules'] if m['key'] == 'telefoon')
check('telefoon-triage-copy', 'spoed' in tel_regel.lower())

# ── VOL-scenario: ontlast, geen reactivatie, geen indicatie ──────────────────
adv_vol = A(branche='loodgieter', pijn=['LEAD', 'TEL', 'NOS'], capacity='nee', calls='30+', value='2000+')
check('vol-framing-ontlast', adv_vol['framing'] == 'ontlast')
check('vol-geen-reactivatie', 'reactivatie' not in keys(adv_vol))
check('vol-geen-indicatie', adv_vol['indicatie'] is None)


# ── Uren-getal (kwantificeer-vragen → harde uren/maand) ──────────────────────
u_tel = A(pijn=['TEL'], q={'TEL': 'meer_dan_5'})['uren']
check('uren-tel-total', bool(u_tel) and u_tel['total'] == 5)              # 6×26×2min = 312 → 5u
check('uren-tel-module', u_tel['breakdown'][0]['module'] == 'telefoon')
check('uren-msg', A(pijn=['MSG'], q={'MSG': '10-25'})['uren']['total'] == 11)   # 17×26×1,5
check('uren-off', A(pijn=['OFF'], q={'OFF': '5-15'})['uren']['total'] == 2)     # 10×15
check('uren-fac', A(pijn=['FAC'], q={'FAC': '10-30'})['uren']['total'] == 2)    # 20×6
check('uren-nos-module-telefoon', A(pijn=['NOS'], q={'NOS': '30+'})['uren']['breakdown'][0]['module'] == 'telefoon')
u_comb = A(pijn=['TEL', 'MSG', 'FAC'], q={'TEL': 'meer_dan_5', 'MSG': '10-25', 'FAC': '10-30'})['uren']
check('uren-combi-total', u_comb['total'] == 18)                          # 5+11+2
check('uren-combi-drie-regels', len(u_comb['breakdown']) == 3)
check('uren-alleen-gekozen-pijn', A(pijn=['TEL'], q={'TEL': 'meer_dan_5', 'MSG': '50+'})['uren']['total'] == 5)
check('uren-none-zonder-q', A(pijn=['TEL'])['uren'] is None)
check('uren-none-zonder-pijn', A()['uren'] is None)
check('uren-none-bij-nul', A(pijn=['TEL'], q={'TEL': 'bijna_geen'})['uren'] is None)
check('uren-input-echo-q', A(pijn=['TEL'], q={'TEL': '3-5'})['input']['q'] == {'TEL': '3-5'})

# ── Kortere toon: geen brochure-woorden in module-regels ─────────────────────
_allregels = ' '.join(m['regel'] for m in A(branche='loodgieter', pijn=['OFF', 'MSG', 'FAC'], capacity='ja')['modules']).lower()
check('toon-geen-naadloos', 'naadloos' not in _allregels)
check('toon-geen-optimaal', 'optimaal' not in _allregels)
check('toon-geen-professioneel', 'professioneel' not in _allregels)

# ── Rapport ───────────────────────────────────────────────────────────────────
total = _passed + len(_failed)
if _failed:
    print(f'❌ {len(_failed)}/{total} FAILED:')
    for f in _failed:
        print('   -', f)
    raise SystemExit(1)
print(f'✅ Alle {total} tests geslaagd.')
