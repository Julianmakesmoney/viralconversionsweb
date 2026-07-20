"""
prijzen.py — BRON VAN WAARHEID voor het hele Viral Conversions-prijsmodel.

Eén edit hier werkt overal door: demo_advies.py + demo_webshop_advies.py importeren
deze waarden, de schermen lezen ze via de advies-output. Waar elders iets anders
staat, wint dit bestand.
"""

# ── 1. Retainer-ladder: volledige maandprijs per #taken (1..7), aflopende trap ──
LADDER_MIDDLE = [350, 450, 530, 590, 650, 700, 750]      # +100 +80 +60 +60 +50 +50
LADDER = {
    'low':      [295, 380, 445, 495, 540, 570, 595],     # ~0.85× · klant/klus <€150
    'middle':   LADDER_MIDDLE,                            # 1.00× · €150–500
    'high':     [450, 570, 665, 740, 815, 875, 950],     # ~1.25× · €500+
    'draaiend': LADDER_MIDDLE,                            # webshop €5–15k/mnd
    'schaal':   [450, 585, 690, 765, 845, 910, 975],     # webshop ~1.30× · €15k+/mnd
}
TAKEN = ('telefoon', 'whatsapp', 'offertes', 'facturatie', 'reactivatie', 'reviews', 'ads')

# Belminuten inbegrepen in de retainer; daarboven per minuut. Maakt de ticket-schaling
# verdedigbaar: het verschil hangt aan wat de klant KRIJGT, niet aan wie hij is.
BELMINUTEN        = {'low': 300, 'middle': 500, 'high': 1000, 'draaiend': 500, 'schaal': 500}
BELMINUUT_OVERAGE = 0.25                                  # €/min boven de bundel
VOORRANG_TICKETS  = ('high',)

# ── 2. Website — gratis als hefboom ──
PRIJS_VISITEKAARTJE        = 600
PRIJS_BOEKING              = 250        # boekingssysteem + AI-receptionist in je website (eenmalig)
PRIJS_BOEKING_MND          = 30         # + €30/mnd voor het boekingssysteem
BOEKING_BRANCHES           = {'kapper', 'tandarts', 'restaurant'}   # default-aan; elders opt-in via toggle
WEBSITE_GRATIS_VANAF_TAKEN = 2
WEBSITE_AFSCHRIJF_MND      = 12
WEBSITE_AFSCHRIJF_PER_MND  = 50
PRIJS_WEBSITE_MND          = 30        # bij <2 modules: €600 eenmalig + €30/mnd (geen gratis website)

# ── 3. Setup — €300 PER MODULE, ALTIJD. Geen korting, ongeacht ticket/pakket. Nooit tonen vóór de meeting. ──
PRIJS_SETUP = 350                 # legacy (flat) — niet meer in de medewerker-frame
PRIJS_SETUP_PER_MODULE = 300      # elke module opzetten kost €300 — altijd, geen korting, ongeacht ticket

# ── 4. Webshop — zelfde structuur, hogere tickets, nooit gratis ──
PRIJS_WEBSHOP_BOUW        = 2500        # + retainer/mnd; altijd betaald
PRIJS_WEBSHOP_MND         = 150         # kale-shop floor (0 taken)
WEBSHOP_AFSCHRIJF_PER_MND = 210

# ── 5. Revenue share — 15% alleen op aantoonbaar NIEUWE omzet ──
# ── Google Ads / marktonderzoek — €350 opzet + 15% van de nieuwe omzet (selecteerbaar) ──
PRIJS_ADS_SETUP = 350

REVSHARE_PCT = 15
REVSHARE_TELT = [
    'Ads — klik traceerbaar',
    'Reactivatie — klant 6+ mnd weg, terug na jouw bericht',
    'Buiten openingstijden — gesprek dat zonder jou nooit gebeurde',
    'Gemiste oproep opgevangen — was anders weg',
]
REVSHARE_TELT_NIET = [
    'Reguliere inbound tijdens openingstijden',
    'Vaste klant die belt',
    'Omzet die je toch al binnenkreeg',
]


def ladder_price(ticket, n):
    """Volledige maandprijs voor n taken op de gegeven ladder (n<=0 → 0)."""
    lad = LADDER.get(ticket, LADDER_MIDDLE)
    return 0 if n <= 0 else lad[min(n, len(lad)) - 1]
