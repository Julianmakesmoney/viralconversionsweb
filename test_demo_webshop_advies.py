#!/usr/bin/env python3
"""Tests voor bereken_webshop_advies. Draai: python3 test_demo_webshop_advies.py"""
from demo_webshop_advies import bereken_webshop_advies

_passed = 0
_failed = []


def check(name, cond):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(name)


def A(**kw):
    base = {'omzet': 'nog_niks', 'orders': '<25', 'producten': '<25', 'pijn': [], 'kanalen': [], 'q': {}}
    base.update(kw)
    return bereken_webshop_advies(base)


def keys(adv):
    return {m['key'] for m in adv['modules']}


# ── Framing op OMZET ─────────────────────────────────────────────────────────
check('framing-nogniks-bouw', A(omzet='nog_niks')['framing'] == 'bouw')
check('framing-5000-herstel', A(omzet='5000-15000')['framing'] == 'herstel')
check('framing-50000-herstel', A(omzet='50000+')['framing'] == 'herstel')

# ── Verlaten winkelwagen: alleen bij omzet (de kern) ─────────────────────────
check('cart-uit-bij-nogniks', 'cart' not in keys(A(omzet='nog_niks')))
check('cart-aan-bij-omzet', 'cart' in keys(A(omzet='<5000')))
check('cart-aan-bij-5000', 'cart' in keys(A(omzet='5000-15000')))

# ── Service: SERVICE of ORDERS >= 100-500 ────────────────────────────────────
check('service-via-pijn', 'service' in keys(A(pijn=['SERVICE'])))
check('service-via-orders', 'service' in keys(A(orders='100-500')))
check('service-uit-lage-orders', 'service' not in keys(A(orders='25-100')))

# ── Voorraad: VOORRAAD of KANALEN >= 2 ───────────────────────────────────────
check('voorraad-via-pijn', 'voorraad' in keys(A(pijn=['VOORRAAD'])))
check('voorraad-via-kanalen2', 'voorraad' in keys(A(kanalen=['eigen', 'bol'])))
check('voorraad-uit-1-kanaal', 'voorraad' not in keys(A(kanalen=['eigen'])))

# ── Reactivatie: OMZET >= 5000-15000 ─────────────────────────────────────────
check('reactivatie-aan-5000', 'reactivatie' in keys(A(omzet='5000-15000')))
check('reactivatie-uit-onder-5000', 'reactivatie' not in keys(A(omzet='<5000')))

# ── Reviews: ORDERS >= 25-100 ────────────────────────────────────────────────
check('reviews-aan-25-100', 'reviews' in keys(A(orders='25-100')))
check('reviews-uit-onder-25', 'reviews' not in keys(A(orders='<25')))

# ── Facturatie: ADMIN ────────────────────────────────────────────────────────
check('facturatie-via-admin', 'facturatie' in keys(A(pijn=['ADMIN'])))
check('facturatie-uit-zonder-admin', 'facturatie' not in keys(A()))

# ── BOUW: geen cart / reactivatie / indicatie ────────────────────────────────
b = A(omzet='nog_niks', pijn=['TRAFFIC'], orders='500+')
check('bouw-geen-cart', 'cart' not in keys(b))
check('bouw-geen-reactivatie', 'reactivatie' not in keys(b))
check('bouw-geen-indicatie', b['indicatie'] is None)
check('bouw-geen-modules-zonder-pijn', keys(A(omzet='nog_niks')) == set())

# ── HERSTEL: cart + indicatie ────────────────────────────────────────────────
h = A(omzet='5000-15000')
check('herstel-cart', 'cart' in keys(h))
check('herstel-indicatie', h['indicatie'] is not None)

# ── Indicatie-formule (ondergrens × 2 × 0,04 / 0,08) ─────────────────────────
i = A(omzet='5000-15000')['indicatie']       # ondergrens 5000 → verlaten 10000 → 400 / 800
check('indicatie-5000-laag', i['laag'] == 400)
check('indicatie-5000-hoog', i['hoog'] == 800)
i2 = A(omzet='50000+')['indicatie']          # 50000 → 100000 → 4000 / 8000
check('indicatie-50000-laag', i2['laag'] == 4000)
check('indicatie-50000-hoog', i2['hoog'] == 8000)

# ── Hoofdgetal: bij hoge omzet is verlaten winkelwagen het sterkst ──────────
check('hoofdgetal-hoge-omzet-indicatie', A(omzet='50000+')['hoofdgetal'] == 'indicatie')
check('hoofdgetal-met-uren', A(omzet='5000-15000', pijn=['SERVICE'], q={'SERVICE': '5-15'})['hoofdgetal'] == 'uren')

# ── KANALEN >= 3 → consolidatie is de hoofdpitch ─────────────────────────────
k = A(kanalen=['eigen', 'bol', 'amazon'])
check('kanaalpitch-flag', k['kanaalPitch'] is True)
check('kanaalpitch-kop', k['koppen']['titel'] == 'Al je kanalen uit één overzicht')
check('geen-kanaalpitch-2', A(kanalen=['eigen', 'bol'])['kanaalPitch'] is False)

# ── Uren ─────────────────────────────────────────────────────────────────────
us = A(pijn=['SERVICE'], q={'SERVICE': '5-15'})['uren']          # 10×26×4 = 1040 → 17u
check('uren-service', us and us['total'] == 17)
check('uren-service-module', us['breakdown'][0]['module'] == 'service')
uv = A(pijn=['VOORRAAD'], kanalen=['eigen', 'bol'], q={'VOORRAAD': 'paar_week'})['uren']  # 10×2×20=400 → 7u
check('uren-voorraad', uv['total'] == 7)
check('uren-voorraad-module', uv['breakdown'][0]['module'] == 'voorraad')
check('uren-versnipperd-geen-uren', A(pijn=['VERSNIPPERD'], orders='500+', q={'VERSNIPPERD': '4'})['uren'] is None)
uf = A(pijn=['ADMIN'], orders='100-500')['uren']                # 300×2=600 → 10u
check('uren-facturen', uf['total'] == 10)
check('uren-facturen-module', uf['breakdown'][0]['module'] == 'facturatie')
check('uren-none-zonder-pijn', A()['uren'] is None)
check('uren-none-service-zonder-q', A(pijn=['SERVICE'])['uren'] is None)

# ── Prijs: €2.500 eenmalig + €150/mnd + modules-ladder, nooit gratis ────────
p = A(omzet='5000-15000')['prijs']            # modules cart+reactivatie = 2
check('prijs-eenmalig-2500', p['eenmalig'] == 2500)
check('prijs-shop-150', p['shopMaand'] == 150)
check('prijs-nooit-gratis', p['gratis'] is False)
check('prijs-2-modules-draaiend', p['maandtotaal'] == 450)       # draaiend (middle) [1], omzet 5000-15000
check('prijs-ladder-draaiend', p['ladder'] == [350, 450, 530, 590, 650, 700, 750])
check('prijs-0-modules-kale-shop', A(omzet='nog_niks')['prijs']['maandtotaal'] == 150)  # bouw zonder taken = kale shop
# Schaal-tier (€15k+/mnd): ladder ~1,3×, nooit low
_sch = A(omzet='50000+', pijn=['CART', 'SERVICE'], orders='500+')
check('webshop-ticket-schaal', _sch['ticket'] == 'schaal')
check('webshop-schaal-ladder', _sch['prijs']['ladder'] == [450, 585, 690, 765, 845, 910, 975])
check('webshop-ticket-draaiend', A(omzet='5000-15000')['ticket'] == 'draaiend')
check('revshare-prominent-herstel', A(omzet='5000-15000')['prijs']['revshareProminent'] is True)
check('revshare-klein-bouw', A(omzet='nog_niks')['prijs']['revshareProminent'] is False)

# ── Type + shape ─────────────────────────────────────────────────────────────
check('type-webshop', A()['type'] == 'webshop')
check('elke-module-heeft-regel', all(m['regel'] for m in A(omzet='5000-15000', pijn=['SERVICE', 'VOORRAAD'], kanalen=['eigen', 'bol'])['modules']))
check('vergelijking-losse-tools', A()['vergelijking']['alt'] == 'losse_tools')
# VERSNIPPERD stuurt de vergelijking (aantal systemen = aantal regels), niet de uren
check('vergelijking-default-4-tools', A()['vergelijking']['systemen'] == 4 and A()['vergelijking']['losseToolsPrijs'] == 145)
check('vergelijking-versnipperd-2', A(pijn=['VERSNIPPERD'], q={'VERSNIPPERD': '2'})['vergelijking']['losseToolsPrijs'] == 70)
check('vergelijking-versnipperd-4', A(pijn=['VERSNIPPERD'], q={'VERSNIPPERD': '4'})['vergelijking']['losseToolsPrijs'] == 145)
check('vergelijking-versnipperd-5plus', A(pijn=['VERSNIPPERD'], q={'VERSNIPPERD': '5+'})['vergelijking']['systemen'] == 6)
check('input-echo-shop-url', A(shop_url='https://mijnshop.nl')['input']['shop_url'] == 'https://mijnshop.nl')


# ── Rapport ───────────────────────────────────────────────────────────────────
total = _passed + len(_failed)
if _failed:
    print(f'❌ {len(_failed)}/{total} FAILED:')
    for f in _failed:
        print('   -', f)
    raise SystemExit(1)
print(f'✅ Alle {total} tests geslaagd.')
