import sqlite3
from pathlib import Path

from server.categorize import RuleBasedCategorizer

SCHEMA = """
CREATE TABLE IF NOT EXISTS imported_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CHF',
    category_id INTEGER REFERENCES categories(id),
    source TEXT NOT NULL,
    file_id INTEGER REFERENCES imported_files(id),
    manually_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CHF',
    category_id INTEGER REFERENCES categories(id),
    source TEXT NOT NULL,
    file_id INTEGER REFERENCES imported_files(id)
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL UNIQUE REFERENCES categories(id),
    monthly_limit_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- source_key is the immutable identifier import derives from the
-- filename/folder (see import_service.py) — an account row is looked up
-- or created by source_key at import time. name is the user-editable
-- display name (defaults to source_key), so renaming an account doesn't
-- break the link back to future re-imports of the same source folder.
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- Tags apply to confirmed transactions only, not pending rows — tagging is
-- a post-review organizational step, not part of the import/categorize flow.
CREATE TABLE IF NOT EXISTS transaction_tags (
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_id, tag_id)
);

-- Tracks which of DEFAULT_CATEGORIES has ever been seeded, independent of
-- whether that category still exists under its original name (or at all).
-- Without this, init_db()'s "INSERT OR IGNORE ... WHERE name = ?" seeding
-- looked up defaults purely by their current NAME — so renaming or
-- deleting a default category (e.g. "Transport" -> "OeV & Taxi") made it
-- look, to the next app start, exactly like a default that had never been
-- created yet, silently resurrecting a fresh empty duplicate under the old
-- name on every restart. See init_db() below.
CREATE TABLE IF NOT EXISTS seeded_defaults (
    key TEXT PRIMARY KEY
);
"""

# Stored as strings (settings.value is TEXT) and parsed by the API layer —
# a flat key/value store rather than dedicated columns so a new setting
# never needs a schema migration, just a new default entry here.
DEFAULT_SETTINGS = {
    "auto_categorize_enabled": "true",
    "confidence_threshold": "0.75",  # keep in sync with server.categorize.CONFIDENCE_THRESHOLD
    "default_date_range_days": "",  # empty = no default filtering ("alle")
}

# Columns added after the initial schema. Applied via idempotent ALTER TABLE
# so both fresh databases and Kevin's existing local data/budget.db pick
# them up on next start — see test_init_db_migrates_existing_database_*.
MIGRATIONS = [
    ("category_rules", "match_count", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "correction_count", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "iban", "TEXT"),
    ("category_rules", "is_seeded", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "created_at", "TEXT NOT NULL DEFAULT ''"),
    ("pending_transactions", "suggested_category_id", "INTEGER REFERENCES categories(id)"),
    ("pending_transactions", "suggested_rule_id", "INTEGER REFERENCES category_rules(id)"),
    ("pending_transactions", "category_confidence", "REAL"),
    ("categories", "excluded_from_totals", "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "account_id", "INTEGER REFERENCES accounts(id)"),
    ("pending_transactions", "account_id", "INTEGER REFERENCES accounts(id)"),
    ("categories", "is_hidden", "INTEGER NOT NULL DEFAULT 0"),
]

# Categories seeded with excluded_from_totals=1 — money movement that isn't
# real income/spending (e.g. paying off a credit card bill from the linked
# checking account). Re-asserted idempotently on every init_db() call, since
# there's no UI to toggle this per-category; it's a fixed property of what
# the category represents, not a per-transaction user choice.
CATEGORIES_EXCLUDED_FROM_TOTALS = {"Kreditkarten-Ausgleich"}

# Categories seeded with is_hidden=1 — matching transactions skip the
# pending-review queue entirely (see import_service.scan_and_parse) and are
# filtered out of every listing/summary endpoint by default (see
# server/app.py's _fetch_filtered_transactions), unlike
# excluded_from_totals categories, which still show up in the ledger and
# only drop out of aggregate sums. Re-asserted idempotently, same reasoning
# as CATEGORIES_EXCLUDED_FROM_TOTALS above.
CATEGORIES_HIDDEN = {"Versteckt"}

# Derived from Kevin's real ZKB bank statements (CSV/PDF) and Swisscard/
# Cornercard credit card statements — see docs/superpowers/specs for the
# analysis. Goal: the first real import needs as little manual
# categorization as possible.
DEFAULT_CATEGORIES = [
    "Lebensmittel", "Restaurants/Ausgang", "Transport", "Auto", "Reisen",
    "Miete/Wohnen", "Versicherungen", "Gesundheit", "Shopping", "Abos",
    "Freizeit", "Hobby", "Bargeldbezug", "Privatüberweisungen",
    "Sparen/Anlegen", "Lohn/Einkommen", "Steuern", "Sonstiges",
    "Kreditkarten-Ausgleich", "Versteckt", "Unkategorisiert",
]

# Grouped by category so maintenance means "find the category, add a
# keyword" instead of hunting through one long flat list for where a new
# merchant belongs. Keyword must be lowercase (categorize() matches against
# a lowercased description) and is matched as a substring, so a more
# specific/longer keyword should be used wherever a shorter one would
# otherwise misfire (see "sbb mobile" vs "twint" below) — categorize()
# always prefers the longest matching keyword, regardless of list order.
# Flattened into the (keyword, category) pairs actually seeded by
# init_db() via _flatten_category_keyword_groups() below; that flattening
# doesn't change matching/precedence behavior at all, only this source
# layout is new.
DEFAULT_CATEGORY_KEYWORD_GROUPS = {
    "Lebensmittel": [
        "migros", "coop", "volg", "denner", "metzg", "suters hofmart",
        # A single umlaut spelling is enough now — RuleBasedCategorizer.
        # predict() normalizes stored keywords before matching (see Task 3),
        # so "bäckerei" transparently also matches an incoming "BAECKEREI"
        # spelling.
        "bäckerei", "back.-conf.", "confis",
        # ZKB's own statement text drops "ä" entirely rather than folding it
        # to "ae" the way Cornercard's export does (e.g. "Bäckerei" ->
        # "Backerei", not "Baeckerei") — normalize_description() can't
        # recover a dropped letter, so this needs its own keyword alongside
        # "bäckerei" above. Same issue recurs a few times below (ÖKK,
        # Bevölkerungsamt) — noted once here, not repeated at length.
        "backerei", "konditorei", "lidl",
        # The Spar chain's own receipt/statement text ("Spar dankt") rather
        # than the bare brand name — "spar" alone would false-positive on
        # "sparen"/"Sparen/Anlegen"-adjacent text.
        "spar dankt",
        # Second real-data mining pass (full year of statements): Tegut is
        # a German supermarket chain; "nahrungsmittel" (foodstuffs) is a
        # generic word that reliably signals a food wholesaler/supplier
        # regardless of the specific company name.
        "tegutfiliale", "nahrungsmittel", "new asia market", "asia store",
        # Third real-data mining pass (18 months of statements after the ZKB
        # export was extended back to May 2025). "tegut" (bare) supplements
        # "tegutfiliale" above — a second observed format has a space
        # ("Tegut Filiale") that the no-space keyword doesn't match. Aldi
        # (both "Aldi Suisse" and German "ALDI SUeD") and k kiosk (Swiss
        # newsstand/convenience chain, both "kkiosk" and "k kiosk" spellings
        # seen) are well-known chains not yet covered.
        "tegut", "aldi", "kkiosk", "k kiosk",
        # Sixth pass: a general keyword-coverage expansion using well-known
        # Swiss/international brand names and multi-language terms rather
        # than statement-mined ones — see server/db.py's module docstring
        # note above DEFAULT_CATEGORY_KEYWORD_GROUPS. Manor's grocery
        # department ("Manor Food") is kept longer/more specific than the
        # bare "manor" department-store keyword in Shopping below, so a
        # Manor Food receipt still wins Lebensmittel over the shorter
        # Shopping catch-all. Gas-station convenience shops (Migrolino,
        # Coop Pronto) are grocery/snack purchases, not fuel, so they
        # belong here rather than "Auto" — but "coop pronto tankstelle" is
        # kept as a longer, more specific override in Auto below, so a
        # purchase explicitly at the fuel pump still wins there. "avec"
        # (the Coop-affiliated convenience-store chain) was deliberately
        # dropped after a real-data regression check: it's also the plain
        # French word "with" and false-matched an unrelated company
        # ("Fais Avec GmbH") — no safe unambiguous form of this brand name
        # was found short enough to be worth keeping.
        "manor food", "migrolino", "coop pronto", "otto's", "ottos",
        "landi", "denner satellit",
    ],
    "Restaurants/Ausgang": [
        # Generic dining-out words — catch any vendor that doesn't match a
        # specific chain keyword below, so a new restaurant/café doesn't
        # need its own rule the way a genuinely distinct merchant does.
        "restaurant", "cafe",
        "pizza falcone", "curry factory", "bar / restaurant caled",
        "kuhn back & gastro", "autogrill", "* eats", "uber eats",
        # Well-known fast-food/café chains — a curated starter list so
        # common chains are recognized on first encounter instead of
        # needing a manual correction each time. "mcdonald" and "domino"
        # are deliberately the bare brand stem, not the full name, so they
        # match both "McDonald's"/"McDonalds" and "Domino's"/"Dominos"
        # spelling variants (normalize_description doesn't strip
        # apostrophes).
        "subway", "mcdonald", "burger king", "kfc", "starbucks", "dunkin",
        "domino", "manora", "vapiano", "nordsee", "legend doener",
        # Real vendor from Kevin's own statements: a bakery/food counter at
        # the airport — food bought there is eating-out, not grocery
        # shopping, so it's mapped here rather than under the generic
        # bakery keywords above.
        "steiner flughafebeck",
        # More real vendors mined from Kevin's full statement history —
        # small bakery/café/take-away stops where he's eating, not grocery
        # shopping. "wal*cafe" is deliberately NOT listed separately: it
        # already contains "cafe" as a substring, so the generic keyword
        # above catches it without a dedicated rule.
        "backer-imbiss", "take-away", "ristorante", "berggasthaus",
        # Kept longer/more specific than the generic "cafe" above so the
        # hospital's on-site café still wins Restaurants/Ausgang over the
        # shorter "stadtspital triemli" (Gesundheit) keyword below —
        # categorize() prefers the longest match.
        "stadtspital triemli cafe",
        "estia home of taste", "tillystante",
        # Cornercard's own comma-joined "MERCHANT,CITY" format has no space
        # between words — the existing "pizza falcone" keyword (with a
        # space) never matches it, so this is a second keyword for the
        # same vendor.
        "pizzafalcone",
        # Marché (SV Group) — a restaurant chain at Swiss train stations/
        # airports/highway stops, e.g. "Marche-6137 Firehouse".
        "marche-",
        # Second real-data mining pass (full year of statements). "pizza"
        # is a generic catch-all — like "restaurant"/"cafe" above, it
        # covers any pizzeria that isn't "pizza falcone" specifically.
        "pizza", "brezelkonig", "starkebab", "pezzodipane", "lsmpanadastore",
        "namastey", "luckys thai", "jack's thai", "marmar cuisine",
        "elvetino", "triemlis food shop", "triemlisfoodshop", "butegar",
        "boostbar", "quadrifoglio",
        # Cornercard glues "Companys" (a tapas-bar chain) directly onto the
        # city with no space, same issue as "pizzafalcone" above.
        "companyszuerich",
        # Kevin identified these four as restaurants after they showed up
        # too ambiguous to guess in the second mining pass — "Bankhaus
        # Metzler" and "Hauptsitz Postfinance" read like banking/postal
        # institutions but are actually restaurant names at those
        # locations; "Reinhard AG" likewise.
        "bankhausmetzler", "bankhaus metzler", "reinhard ag",
        "hauptsitz postfinance",
        # Third real-data mining pass. "marche take away" is a second
        # observed Marché format (space-separated, no hyphen) that "marche-"
        # above doesn't match. "marcos" recurs as a Frankfurt restaurant
        # across both the ZKB and Swisscard statements.
        "marche take away", "marcos",
        # Sixth pass: generic multi-language dining-out terms (French/
        # Italian, folded via normalize_description's accent map — see
        # "hopital"/"pathe" in Gesundheit/Freizeit below for the same
        # pattern) and well-known chains not yet covered.
        "boulangerie", "brasserie", "bistro", "trattoria", "pizzeria",
        "kebab", "doener", "sushi",
        "tibits", "hiltl", "holy cow", "coffee fellows", "five guys",
        "deliveroo", "smood", "eat.ch",
    ],
    # Getting around WITHOUT owning/driving a personal car — public transit,
    # taxis, and shared micro-mobility rentals. See "Auto" below for the
    # complementary "operating your own car" category (fuel, parking,
    # tolls, leasing, maintenance) — split out in the fourth pass, since
    # the two are meaningfully different budget questions even though both
    # are "transport" in the broadest sense.
    "Transport": [
        "sbb", "sbb mobile", "taxifahrt", "ubr* pending",
        # Generic ride/refund fallback — shorter than "* eats"/"uber eats"
        # above, so a food delivery line still wins Restaurants/Ausgang;
        # this only catches plain Uber rides and refunds like
        # "Rückerstattung ... UBER 00000 AMSTERDAM".
        "uber",
        # E-scooter/ride-share apps, mined from real statements.
        "dott scooter", "bolt.",
        # Cornercard's comma-joined format glues this one too — same issue
        # as "pizzafalcone"/"swissintlairlines" above.
        "dottscooterride",
        # Second real-data mining pass: "taxi" generalizes past the
        # existing "taxifahrt" — a taxi company's own name (e.g. "Taxi
        # Asmat") doesn't contain that word at all.
        "taxi",
        # Sixth pass: public-transit operators (regional/municipal), long-
        # distance coach/rail alternatives, and shared-mobility brands not
        # yet covered. "mobility carsharing"/"tier scooter"/"voi scooter"
        # kept as the fuller phrase rather than the bare brand name — each
        # is also an unrelated common word/company on its own ("mobility"
        # overlaps conceptually with "die mobiliar" the insurer in
        # Versicherungen, "tier" is German for "animal", "voi" is too short
        # to safely stand alone).
        "postauto", "vbz", "swisspass", "flixbus", "flixtrain", "publibike",
        "mobility carsharing", "tier scooter", "voi scooter", "limebike",
    ],
    # Costs of owning/operating Kevin's own car — fuel, parking, tolls,
    # leasing, registration/road tax, and repairs. Split out of "Transport"
    # (fourth pass) so gas/parking spending can be tracked separately from
    # train tickets and taxi rides, which answer a different budget
    # question ("how much do I spend getting around without my car").
    "Auto": [
        # Gas station brands and parking, mined from real statements.
        "tankstell", "parkingpay", "shell", "socar", "avia", "agrola",
        "parkhaus", "carwash",
        # Car leasing/dealer (AMAG is Switzerland's largest car importer),
        # highway tolls (Italian "pedaggi", Austrian "asfinag" — charged
        # when driving Kevin's own car abroad, unlike public-transit fares
        # above), and a parking-garage operator.
        "amag leasing", "pedaggi", "asfinag", "parkdepot",
        # Fourth pass: the cantonal road-traffic office (vehicle
        # registration/road tax, moved here from the generic "Sonstiges"
        # administrative-fee bucket — it's specifically about owning a
        # car, not a general government fee) and a generic repair-shop
        # keyword ("Garage" reliably means a car workshop in Swiss usage,
        # not a building attached to a house).
        "strassenverkehrsamt", "garage",
        # Sixth pass: "parking" itself was missing — only the specific
        # "parkingpay"/"parkhaus"/"parkdepot" vendor names existed, so a
        # plain "Parking" line (a very common statement text) fell through
        # to manual review. Added as a generic catch-all plus its common
        # synonyms/multi-language variants (French "parcage", Italian
        # "parcheggio") and abbreviations, so any parking charge is
        # recognized regardless of which specific operator printed it.
        "parking", "parkplatz", "parking fee", "p+r", "car park",
        "parking ticket", "parcage", "parcheggio",
        # More fuel-station brands beyond the ones already mined from real
        # statements (Shell, Socar, Avia, Agrola).
        "esso", "migrol", "tamoil",
        # Swiss/Austrian highway toll sticker, a car-repair chain, a tire
        # shop, and the Touring Club Suisse (kept as the full name rather
        # than the 3-letter "tcs" abbreviation, which risks matching inside
        # unrelated text).
        "vignette", "midas", "pneuhaus", "touring club",
        # Longer, more specific overrides found via a real-data regression
        # check after the "coop pronto"/"coop city" keywords were added to
        # Lebensmittel/Shopping above: those brand names are also the
        # location a parking garage or fuel pump happens to be attached to,
        # and (being longer than bare "parking"/"tankstell") would
        # otherwise win and misfile an actual parking/fuel charge as a
        # grocery/shopping purchase. These compound phrases are longer
        # still, so they correctly take priority for exactly that case.
        "coop pronto tankstelle", "parking coop city",
    ],
    "Reisen": [
        "swiss intl air lines",
        # Real vendors print the same airline with no spaces at all
        # ("SwissIntlAirlines,Frankfurt") — the spaced keyword above never
        # matches that; kept as a second keyword rather than replacing it,
        # in case a spaced format ever does show up.
        "swissintlairlines",
        "easyjet", "emirates", "hotel", "airbnb", "meininger",
        "getyourguide", "airalo",
        # Second real-data mining pass: another airline, two car-rental
        # sites, and a Frankfurt museum-district visit (Kevin travels there
        # regularly per other Reisen/Frankfurt entries elsewhere).
        "lufthansa", "rentalcars", "sunnycars", "frankfurtmuseumsufer",
        # Sixth pass: more airlines, hotel chains, car-rental counters (car
        # rental is travel-related, unlike "Auto" above which is
        # specifically about operating Kevin's own car), and booking
        # platforms/travel agencies.
        "ryanair", "wizz air", "eurowings", "klm", "air france",
        "british airways", "qatar airways",
        "novotel", "motel one",
        "europcar", "hertz", "sixt", "avis rent a car",
        "booking.com", "trivago", "kayak", "skyscanner", "expedia",
        "interrail", "tui",
    ],
    "Versicherungen": [
        "ökk",
        # ZKB drops the umlaut entirely on this one too ("ÖKK" -> "OKK").
        # Kept as "okk kranken" rather than the bare 3-letter "okk" to
        # avoid an accidental substring match inside unrelated words.
        "okk kranken",
        "helsana", "axa leben", "axa versicherungen",
        "innova versicherungen", "mobiliar versicherungsgesellschaft",
        # The insurer's own marketing name ("Die Mobiliar") — broader than
        # the full legal name above, but kept as this exact phrase rather
        # than the bare word "mobiliar" (which is also plain German for
        # "furniture" and could false-positive elsewhere).
        "die mobiliar",
        "swiss life", "protekta",
        # Rega (Swiss air rescue) — an annual patronage membership,
        # functionally the same kind of recurring protection payment as
        # the insurers above.
        "rega,",
        # Third real-data mining pass: one of Switzerland's largest general
        # insurers, not yet covered.
        "allianz suisse",
        # Sixth pass: the rest of Switzerland's major health insurers (CSS,
        # Swica, Sanitas, Concordia, Visana, Groupe Mutuel, Assura, Sympany,
        # Atupri — together with the existing ÖKK/Helsana/Innova, this
        # covers the large majority of the ~40-insurer market) and general
        # insurers (Generali, Baloise, Vaudoise, Zurich). "css versicherung"
        # kept as the fuller phrase rather than the bare 3-letter "css",
        # which risks matching inside unrelated text. A generic
        # multi-language fallback ("versicherung" bare, French
        # "assurance", Italian "assicurazione") catches any insurer not
        # individually listed — safe as a fallback since every
        # already-listed insurer's keyword above is longer and so still
        # wins first (categorize() prefers the longest match).
        "css versicherung", "swica", "sanitas", "concordia", "visana",
        "groupe mutuel", "assura", "sympany", "atupri",
        "generali", "baloise", "vaudoise", "zurich versicherung",
        "versicherung", "assurance", "assicurazione",
    ],
    "Gesundheit": [
        "apotheke",
        # "zahnarzt" (dentist, in general) is broader than the specific
        # "zahnarztpraxis" vendor name below — kept both since a shorter
        # keyword only matters if the longer one doesn't also match.
        "zahnarzt", "zahnarztpraxis", "gynpraxis", "gemeinschaftspraxis",
        "shiatsu", "physio-therapien", "orthopaedie-technik",
        # Traditional Chinese Medicine practices — no single vendor name to
        # anchor on, so matched by the generic practice type instead. Kept
        # as "tcm praxis" rather than the bare 3-letter "tcm": TWINT-routed
        # payments are common in Kevin's data, and a 3-letter keyword loses
        # to the 5-letter generic "twint" catch-all below (categorize()
        # prefers the longest match).
        "tcm praxis",
        # Hospital visits — kept shorter/broader than "stadtspital triemli
        # cafe" (Restaurants/Ausgang) above so the on-site café still wins
        # for actual café purchases; this only catches the hospital itself.
        "stadtspital triemli",
        # Second real-data mining pass: Amavita is a Swiss pharmacy chain;
        # Hirslanden is a private-hospital-group brand (kept as the bare
        # name so it also catches other Hirslanden-branded facilities, not
        # just this specific headache clinic).
        "amavita", "hirslanden",
        # Third real-data mining pass: a veterinary practice (recurs twice,
        # closest fit here since there's no dedicated pet-care category) and
        # an optician chain, consistent with "fielmann" above.
        "tierarztpraxis", "foto-optik",
        # Sixth pass: multi-language generic terms (French/Italian pharmacy,
        # hospital, clinic — "hopital"/"clinique" fold from
        # "hôpital"/"clinique" via normalize_description's accent map) and
        # well-known Swiss pharmacy/optician chains. NOT a bare "spital":
        # tried first, but it false-matched inside the unrelated English
        # word "Hospitality" (real-data regression check: "Weisse Arena
        # Hospitality" contains "ho-SPITAL-ity" as a literal substring) — a
        # trailing space doesn't help either, since normalize_description()
        # strips it off both the incoming text AND the stored keyword
        # before comparing. Using the specific compound forms below instead
        # (cantonal/university/city hospitals) avoids the collision while
        # still covering most major Swiss hospitals by name.
        "pharmacie", "farmacia", "kantonsspital", "universitatsspital",
        "inselspital", "hopital", "ospedale", "klinik",
        "clinique", "clinica", "arztpraxis", "hausarzt",
        "coop vitality", "topwell", "sunstore", "visilab",
    ],
    "Shopping": [
        "zalando", "digitec galaxus", "galaxus mobile", "media markt",
        "velotec", "calzedoni", "cutie socks", "scooter planet", "ofinto",
        "baby-walz", "ikea", "hornbach", "jysk", "brack", "about you",
        "amzn",
        # Cornercard's comma-joined "MERCHANT,CITY" format has no space
        # between words at all — same issue as "pizzafalcone"/
        # "swissintlairlines" above, for these existing space-separated
        # keywords.
        "cutiesocks", "scooterplanet", "mediamarkt",
        # H&M purchased via the Klarna checkout — ZKB strips the "&" and
        # prints inconsistent spacing after the asterisk across statements,
        # so both observed spellings are kept.
        "klarna*h m", "klarna* h m",
        # Second real-data mining pass: a bicycle brand, a bike-suspension
        # shop, an online bike importer, an optician chain, a pet-supplies
        # retailer, and a children's-clothing brand bought via Klarna.
        "canyon", "suspension center", "bike-import", "fielmann", "zooplus",
        "ehrenkind",
        # Third real-data mining pass: well-known clothing chains (Zara, H&M
        # bought directly rather than via Klarna, C&A, Uniqlo, Mango), a
        # furniture chain (XXXLutz), a sporting-goods chain (Ochsner Sport),
        # a books/media retailer (Ex Libris), a personalized photo-print
        # shop (Kartenmacherei, both spellings observed), a sportswear
        # brand (Odlo), and a drugstore chain (dm, both the "DM-Drogerie
        # Markt" and truncated "DM-FIL." formats seen).
        "zara", "h & m", "c & a", "uniqlo", "mango", "xxxlutz",
        "ochsner sport", "ex libris", "die kartenmacherei", "kartenmacherei",
        "odlo", "dm-drogerie", "dm-fil",
        # Buy-now-pay-later checkout providers — the actual bank debit
        # collecting an earlier online purchase, not a duplicate of it (no
        # separate "purchase" line exists for a BNPL checkout, unlike the
        # credit-card settlement pattern — this debit IS the real expense).
        # "riverty" was observed specifically settling an Amazon order;
        # "klarna" (bare) supplements the existing H&M-specific keywords
        # above to also catch Klarna-routed purchases from other merchants.
        "riverty", "klarna",
        # Amazon's own bare domain, as printed by ZKB ("AMAZON.DE*...") —
        # distinct from the existing "amzn" abbreviation above, which
        # doesn't match this format.
        "amazon",
        # Sixth pass: well-known Swiss/international retail chains and
        # online marketplaces not yet covered. "manor" (bare, department
        # store) is deliberately shorter than "manor food" in Lebensmittel
        # above, so a Manor Food grocery receipt still wins that category —
        # this bare keyword only catches other Manor departments.
        "manor", "globus", "coop city", "conforama", "micasa", "interio",
        "pfister", "decathlon", "intersport", "bauhaus", "jumbo",
        "temu", "shein", "aliexpress", "asos",
        "fust", "interdiscount", "microspot", "apple store",
    ],
    "Abos": [
        "spotify", "netflix", "apple.com/bill",
        "salt mobile",
        # digitec Galaxus's own subscription product — distinct from the
        # plain "digitec galaxus"/"galaxus mobile" one-off purchases above
        # (Shopping).
        "galaxus abos",
        # Second real-data mining pass: a fitness-tracker subscription.
        # ("onlyfans" used to live here too — moved to the dedicated
        # "Versteckt" category below, fifth pass.)
        "whoop",
        # Third real-data mining pass: common software subscriptions.
        "adobe", "microsoft",
        # Sixth pass: streaming/telecom subscriptions and other recurring
        # digital services not yet covered. "amazon prime" kept longer/more
        # specific than the bare "amazon" keyword in Shopping above, so a
        # Prime subscription charge still wins Abos over a one-off Amazon
        # purchase. Telecom providers land here (not a dedicated category)
        # for consistency with the existing "salt mobile" entry — a phone/
        # internet plan is a recurring subscription like the others.
        "disneyplus", "disney+", "dazn", "wilmaa", "teleboy",
        "icloud", "google one", "nordvpn", "expressvpn", "audible",
        "amazon prime",
        "swisscom", "sunrise", "quickline", "init7", "wingo", "yallo",
    ],
    # General leisure/social activities — day trips, wellness, entertainment
    # venues. See "Hobby" below for the complementary "an ongoing personal
    # pursuit with its own gear/subscriptions" category (gaming, flight
    # simulation) — split out in the fourth pass, on the same reasoning as
    # Transport/Auto: these answer different budget questions even though
    # both are "leisure spending" in the broadest sense.
    "Freizeit": [
        "coiffure", "sanapark",
        # Shortened from "bergbahnen" (plural, generic) to "bergbah" — the
        # PDF's own column width truncates longer merchant names, and a real
        # statement line cut it to "...Bergbah" (missing "nen"), which the
        # longer keyword never matched.
        "bergbah",
        # Third real-data mining pass: a named mountain railway and a lake
        # ferry (both recur, and aren't caught by the generic "bergbah"
        # above since they're not phrased as "...Bergbahn"), cinema chains,
        # and a recurring yoga-studio membership.
        "stockhornbahn", "zurichsee-fahre", "arena cinemas", "cinema 8",
        "deinyogaweg",
        # Sixth pass: cinemas, gyms/wellness, and entertainment venues.
        # "pathe" folds from "Pathé" via normalize_description's accent map.
        "kino", "cinema", "pathe", "kitag",
        "activ fitness", "fitnesspark", "hallenbad", "schwimmbad",
        "minigolf", "bowling", "europa park", "conny land", "museum",
        "ticketcorner", "starticket", "eventfrog",
    ],
    # A specific ongoing pursuit with its own recurring subscriptions
    # and/or one-off gear purchases — gaming and flight simulation so far.
    # Fourth pass: moved out of Freizeit (steamgames, playstation network,
    # the flight-sim addon store spinibuilds/inibuilds) and Abos
    # (sayintentions/navigraph, the flight-sim subscriptions those addons
    # are bought for) once a dedicated category existed for them, rather
    # than splitting the same hobby's spending across two unrelated
    # categories by whether a given purchase happens to be recurring.
    "Hobby": [
        "steamgames", "playstation network", "spinibuilds", "inibuilds",
        "sayintentions", "navigraph",
        # Sixth pass: other gaming platforms/storefronts and subscriptions.
        # "playstation" (bare) is deliberately shorter than "playstation
        # network" above, so that more specific keyword still wins first.
        "epic games", "playstation", "xbox", "nintendo", "twitch",
        "discord nitro", "battle.net", "riot games", "ea play", "ubisoft",
    ],
    "Bargeldbezug": [
        "bezug zkb visa debit card",
        # Sixth pass: generic multi-language ATM/cash-withdrawal terms, for
        # accounts/cards other than the ZKB Visa Debit one above.
        "bancomat", "geldautomat", "distributeur", "auszahlung", "retrait",
    ],
    "Sparen/Anlegen": [
        "findependent",
        # ZKB's own pillar-3a app; the risk-strategy fund name appears in
        # the statement text ("Frankly Risky").
        "frankly",
        # Sixth pass: other well-known Swiss pillar-3a/investing platforms.
        "viac", "finpension", "swissquote", "selma finance",
        "descartes finance", "truewealth", "vontobel", "postfinance fonds",
    ],
    "Miete/Wohnen": [
        "barth real ag", "otto markwalder", "elektrizitaetswerke",
        # Second real-data mining pass: a condo owners' association fee
        # (Miteigentümergemeinschaft) is housing-related, not generic
        # "Sonstiges". Kept as this literal (umlaut-dropped) substring
        # rather than folding, since the stored description text is
        # itself truncated at this point.
        "miteigentumergemeinschaf",
        # Sixth pass: major Swiss property-management companies, generic
        # rent/utility terms, and multi-language rent synonyms.
        "hausverwaltung", "wincasa", "privera", "livit",
        "mietzins", "nebenkosten", "kautionskonto",
        "loyer", "affitto",
    ],
    # Both sides of the "pay off the credit card bill from the checking
    # account" event: the credit-card statement's own payment-received
    # line ("Ihre Zahlung – Besten Dank", a credit) and the checking
    # account's matching collection debit (identified by the card issuer's
    # legal name as it appears on the ZKB statement). Excluded from totals
    # (see categories.excluded_from_totals) because the money already
    # counted once, either way, when the individual card transactions
    # themselves were imported — counting this too would double it.
    "Kreditkarten-Ausgleich": [
        "ihre zahlung",
        # Some PDF exports merge "IHRE"/"ZAHLUNG" into one word with no
        # space (a pdfplumber word-extraction quirk on that specific
        # statement layout) — kept as a separate keyword rather than
        # loosening to bare "zahlung", which would false-positive on
        # "Zahlungszweck"/"Ratenzahlung"/etc.
        "ihrezahlung", "swisscard aecs", "corner banca",
        # "Saldovortrag" (balance carried forward) is the credit-card
        # statement's own opening-balance line — the mirror image of "Ihre
        # Zahlung" above. Both represent last month's already-counted
        # balance, not a new expense/income this month, so both are
        # excluded from totals.
        "saldovortrag",
    ],
    "Sonstiges": [
        # Unclear small vendors, kept out of real spending categories.
        "ubs - zahlungen div", "corporate benefits", "marko switzerland",
        # Generic bank transaction fees — no dedicated category, and
        # clearly not everyday spending. (Cantonal tax withdrawal used to
        # live here too — moved to the new "Steuern" category, fourth
        # pass.)
        "zahlungsverkehrspreise",
        # Government/administrative offices — not personal spending, but
        # not a transfer either.
        "post ch", "bevölkerungsamt",
        # ZKB drops the umlaut entirely here too ("Bevölkerungsamt" ->
        # "Bevolkerungsamt"), same issue as "bäckerei"/"backerei" above.
        "bevolkerungsamt", "einwohnermeldeamt", "gemeindeverwaltung",
        # Second real-data mining pass: a payment-rounding adjustment, a
        # generic annual-membership-fee line, the Cornercard no-space
        # spelling of the existing "corporate benefits" keyword, and a
        # primary-school-district payment.
        "rundung", "jahresbeitrag", "corporatebenefits",
        "primarschulgemeinde",
        # Kevin identified these as Sonstiges after they showed up too
        # ambiguous to guess in the second mining pass.
        "echst.net", "nvg zentrum",
        # Third real-data mining pass: SERAFE (confirmed via web search —
        # the mandatory Swiss radio/TV reception fee collector, the
        # replacement for the old Billag). (The cantonal road-traffic
        # office found in the same pass moved straight to the new "Auto"
        # category, fourth pass, instead of landing here first — it's
        # specifically about owning a car, not a general government fee.)
        "serafe",
        # Sixth pass: generic bank-fee terms not covered by the specific
        # "zahlungsverkehrspreise" line above.
        "kontofuehrung", "kartengebuehr",
    ],
    # Fourth pass: cantonal/federal tax obligations, split out of the
    # generic "Sonstiges" bucket now that there's a dedicated home for
    # them — distinct from "zahlungsverkehrspreise" (a bank fee) and
    # "steuerbezug" specifically means the tax authority withdrawing money
    # already owed, not a purchase or transfer.
    "Steuern": [
        "steuerbezug",
        # Sixth pass: generic tax-authority/tax-type terms and their French/
        # Italian equivalents. "tasse" (Italian "taxes") is deliberately
        # NOT included — it's also the German word for "cup" ("Tasse"),
        # e.g. inside "Kaffeetasse", and would misfire there.
        "steueramt", "kantonssteueramt", "quellensteuer", "mwst",
        "steuerverwaltung", "impot", "imposta",
    ],
    "Lohn/Einkommen": [
        # Any "Gutschrift Salär: <employer>" line, regardless of employer
        # — one general keyword instead of one entry per employer name.
        "salaer",
        # Sixth pass: generic salary/bonus terms, including the French
        # equivalent of "Salär".
        "salaire", "lohn", "gehalt", "gratifikation",
    ],
    "Privatüberweisungen": [
        # Recurring transfers to/from family, identified by name as they
        # appear on the statement.
        "jayaweera kevin oder fabienne", "fabienne brun",
        "jasmin xenia liviero", "kevin jayaweera",
        # Third real-data mining pass: a second name format for the same
        # person as "fabienne brun" above — ZKB prints the family member's
        # name differently depending on the transaction type (Dauerauftrag
        # vs. Mobile Banking), and word order means neither existing
        # keyword is a substring of this one.
        "fabienne jayaweera",
        # ZKB's own transaction-type label for any account-to-account
        # transfer — broader than the name-specific keywords above, so a
        # transfer to/from someone not yet named here is still recognized
        # instead of needing a new rule added.
        #
        # NOT extended with a matching generic keyword for the INCOMING
        # side ("Gutschrift Auftraggeber: <sender>, <address>"), even
        # though it looks like the natural mirror image of this one —
        # tried during the third mining pass and reverted. That prefix is
        # also how insurer reimbursements ("...Auftraggeber: Helsana
        # Versicherungen AG...", "...Protekta Rechtsschutz..."), a school-
        # district payment ("...Primarschulgemeinde...") and other already-
        # correctly-categorized credits are worded, and several of their
        # specific keywords are shorter than any safe generic phrase would
        # be — categorize() prefers the longest match, so the generic
        # catch-all kept winning and misfiling them as plain transfers.
        # "kontouebertrag" is safe because it's ZKB's own fixed internal
        # label, never reused for a company; no equivalent fixed label
        # exists for incoming Auftraggeber-style credits.
        "kontouebertrag",
        # Generic TWINT catch-all. Kept last / shortest on purpose: every
        # merchant-routed "TWINT: X" line above has a longer, more specific
        # keyword that must win first (categorize() prefers the longest
        # matching keyword), so this only catches person-to-person
        # transfers like "TWINT: SCHWARTZ, PATRICK +4176..." that have no
        # merchant-specific rule.
        "twint",
        # Sixth pass: generic French/Italian terms for a bank transfer.
        "virement", "bonifico",
    ],
    # Fifth pass: sensitive/private bookings Kevin wants excluded from the
    # import review queue and every ledger/dashboard view entirely, not just
    # sorted into a category (see CATEGORIES_HIDDEN + import_service.py).
    # "onlyfans" moved here from "Abos", where it used to just be a regular
    # subscription line.
    "Versteckt": [
        "onlyfans",
    ],
}


def _flatten_category_keyword_groups(groups):
    return [
        (keyword, category)
        for category, keywords in groups.items()
        for keyword in keywords
    ]


DEFAULT_CATEGORY_RULES = _flatten_category_keyword_groups(DEFAULT_CATEGORY_KEYWORD_GROUPS)


def get_connection(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_migrations(conn):
    for table, column, ddl in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _category_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    for name in DEFAULT_CATEGORIES:
        already_seeded = conn.execute(
            "SELECT 1 FROM seeded_defaults WHERE key = ?", (name,)
        ).fetchone()
        if already_seeded:
            # Already created once, on some earlier init_db() call — even if
            # the user has since renamed or deleted it, that's a deliberate
            # choice (see PUT/DELETE /api/categories) that must stick, not
            # get silently undone on the next restart.
            continue
        # Never seeded before. A database migrating from before
        # seeded_defaults existed may already have a same-named row (from
        # the old plain "INSERT OR IGNORE ... name" seeding) — claim it
        # instead of creating a duplicate; only a genuinely fresh database
        # (or one where this default was already renamed/deleted before
        # this fix shipped) falls through to actually inserting a new row.
        existing_by_name = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (name,)
        ).fetchone()
        if existing_by_name is None:
            conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        conn.execute("INSERT OR IGNORE INTO seeded_defaults (key) VALUES (?)", (name,))
    for name in CATEGORIES_EXCLUDED_FROM_TOTALS:
        conn.execute(
            "UPDATE categories SET excluded_from_totals = 1 WHERE name = ?", (name,)
        )
    for name in CATEGORIES_HIDDEN:
        conn.execute(
            "UPDATE categories SET is_hidden = 1 WHERE name = ?", (name,)
        )
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    # Seeded with match_count=0 like any freshly-learned rule (see
    # RuleBasedCategorizer.learn()) — NOT a head start above
    # CONFIDENCE_THRESHOLD. An earlier version seeded match_count=3, giving
    # every one of the ~200 seeded rules confidence >= 0.8 (compute_confidence
    # in server/categorize.py) from the moment the database existed, before a
    # single real transaction had ever validated the keyword. On Kevin's real
    # 934-transaction import that silently auto-accepted 899 (96%) — most of
    # them via rules that had never once been confirmed — instead of the
    # ~90% a genuinely earned track record produces. A hand-curated keyword
    # still gets used for the suggestion immediately; it just has to be
    # confirmed correct twice (match_count reaching 2, confidence 0.75)
    # before it's trusted enough to skip manual review.
    for keyword, category_name in DEFAULT_CATEGORY_RULES:
        category_id = _category_id_by_name(conn, category_name)
        if category_id is None:
            # This default category has been renamed or deleted by the
            # user (see seeded_defaults above). If this rule was already
            # seeded in an earlier init_db() call, it's already correctly
            # attached to that category's id — a rename doesn't move
            # existing rules — and unaffected by this rename; if not,
            # there's no default category left to attach a NEW seeded rule
            # to, so it's skipped rather than crashing every app start.
            continue
        conn.execute(
            "INSERT OR IGNORE INTO category_rules "
            "(keyword, category_id, match_count, correction_count, is_seeded, created_at) "
            "VALUES (?, ?, 0, 0, 1, datetime('now'))",
            (keyword, category_id),
        )
    # One-off retarget for a pre-existing database where "ihre zahlung" was
    # already seeded under its old category (Sonstiges), before this
    # exclude-from-totals fix existed. Deliberately NOT a generic
    # "resync every seeded rule to its DEFAULT_CATEGORY_RULES category"
    # mechanism: is_seeded is never cleared when a rule is retargeted via
    # PUT /api/rules/<id> (see update_rule), so a generic version would
    # silently undo any manual re-categorization Kevin makes through
    # "Regeln verwalten" on every restart.
    kreditkarten_ausgleich_id = _category_id_by_name(conn, "Kreditkarten-Ausgleich")
    sonstiges_id = _category_id_by_name(conn, "Sonstiges")
    if kreditkarten_ausgleich_id is not None and sonstiges_id is not None:
        conn.execute(
            "UPDATE category_rules SET category_id = ? "
            "WHERE keyword = 'ihre zahlung' AND category_id = ?",
            (kreditkarten_ausgleich_id, sonstiges_id),
        )
        # Same one-off retarget for "saldovortrag", seeded under Sonstiges
        # before the credit-card-settlement exclusion existed for it too.
        conn.execute(
            "UPDATE category_rules SET category_id = ? "
            "WHERE keyword = 'saldovortrag' AND category_id = ?",
            (kreditkarten_ausgleich_id, sonstiges_id),
        )
    # Fourth pass: introduced Auto (split out of Transport/Sonstiges), Hobby
    # (split out of Freizeit/Abos), and Steuern (split out of Sonstiges) —
    # same "already seeded under an old category" situation as ihre
    # zahlung/saldovortrag above, but for enough keywords that a loop is
    # more maintainable than one UPDATE per keyword. Each entry only names
    # the OLD category a keyword used to live in; its new category is
    # looked up from DEFAULT_CATEGORY_KEYWORD_GROUPS itself (via
    # DEFAULT_CATEGORY_RULES), so this list can't drift out of sync with
    # where a keyword actually lives now.
    _keyword_to_new_category = dict(DEFAULT_CATEGORY_RULES)
    _fourth_pass_retargets = [
        ("tankstell", "Transport"), ("parkingpay", "Transport"),
        ("shell", "Transport"), ("socar", "Transport"), ("avia", "Transport"),
        ("agrola", "Transport"), ("amag leasing", "Transport"),
        ("parkhaus", "Transport"), ("carwash", "Transport"),
        ("pedaggi", "Transport"), ("asfinag", "Transport"),
        ("parkdepot", "Transport"), ("strassenverkehrsamt", "Sonstiges"),
        ("steamgames", "Freizeit"), ("playstation network", "Freizeit"),
        ("spinibuilds", "Freizeit"), ("inibuilds", "Freizeit"),
        ("sayintentions", "Abos"), ("navigraph", "Abos"),
        ("steuerbezug", "Sonstiges"),
    ]
    for keyword, old_category_name in _fourth_pass_retargets:
        old_category_id = _category_id_by_name(conn, old_category_name)
        new_category_id = _category_id_by_name(conn, _keyword_to_new_category[keyword])
        if old_category_id is None or new_category_id is None:
            # Either category has been renamed/deleted since — this
            # one-off migration either already ran successfully in the
            # past (nothing left to retarget) or has no sensible category
            # left to retarget to/from; skip rather than crash.
            continue
        conn.execute(
            "UPDATE category_rules SET category_id = ? "
            "WHERE keyword = ? AND category_id = ?",
            (new_category_id, keyword, old_category_id),
        )
    # Fifth pass: "onlyfans" moved from "Abos" to the new hidden "Versteckt"
    # category (see CATEGORIES_HIDDEN). Same "already seeded under an old
    # category" retarget as above, plus — unlike a plain re-categorization —
    # this one also needs to move any transaction that was already imported
    # under the old rule, since the whole point of "Versteckt" is that
    # matching transactions never appear in the ledger/dashboard at all.
    # manually_corrected = 0 guard: never override a deliberate manual
    # re-categorization Kevin already made away from Abos, same reasoning as
    # every other retarget in this function not touching manual corrections.
    abos_id = _category_id_by_name(conn, "Abos")
    versteckt_id = _category_id_by_name(conn, "Versteckt")
    if abos_id is not None and versteckt_id is not None:
        conn.execute(
            "UPDATE category_rules SET category_id = ? "
            "WHERE keyword = 'onlyfans' AND category_id = ?",
            (versteckt_id, abos_id),
        )
        conn.execute(
            "UPDATE transactions SET category_id = ? "
            "WHERE category_id = ? AND manually_corrected = 0 "
            "AND LOWER(description) LIKE '%onlyfans%'",
            (versteckt_id, abos_id),
        )
        conn.execute(
            "UPDATE pending_transactions SET category_id = ? "
            "WHERE category_id = ? AND LOWER(description) LIKE '%onlyfans%'",
            (versteckt_id, abos_id),
        )
    _backfill_accounts(conn)
    _fix_location_poisoned_rules(conn)
    conn.commit()
    return conn


def get_or_create_account(conn, source_key):
    """Look up the account for a source_key (the filename/folder-derived
    string import_service.py already computes as `source`), creating it
    with name=source_key on first sight. Renaming an account only changes
    its display name, so it keeps resolving to the same row on future
    re-imports of that source."""
    row = conn.execute(
        "SELECT id FROM accounts WHERE source_key = ?", (source_key,)
    ).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO accounts (source_key, name) VALUES (?, ?)",
        (source_key, source_key),
    )
    return cursor.lastrowid


def _backfill_accounts(conn):
    """Create an account for every distinct `source` string already present
    in transactions/pending_transactions (e.g. from before the accounts
    table existed, or rows inserted by code that hasn't been updated to set
    account_id directly) and point account_id at it. Only fills in NULLs —
    never overwrites an existing account_id — so it's safe to run on every
    init_db() call without disturbing a row someone reassigned."""
    sources = set()
    for table in ("transactions", "pending_transactions"):
        for row in conn.execute(f"SELECT DISTINCT source FROM {table} WHERE account_id IS NULL"):
            sources.add(row["source"])
    for source in sources:
        account_id = get_or_create_account(conn, source)
        for table in ("transactions", "pending_transactions"):
            conn.execute(
                f"UPDATE {table} SET account_id = ? WHERE source = ? AND account_id IS NULL",
                (account_id, source),
            )


# One-off cleanup for a real incident found via a real-data audit: before
# STOPWORDS (see server/categorize.py) was hardened against place names and
# numeric placeholders, RuleBasedCategorizer.learn() had already picked up
# several of them as "keywords" from earlier corrections — e.g. "zuerich"
# (92 hits, Restaurants/Ausgang) outranked the real "salaer"/"coop"/"migros"
# rules on every transaction whose address happens to be in Zürich,
# including salary credits and grocery purchases. This list is exactly the
# set found poisoned in that audit — not meant to be extended going
# forward; STOPWORDS now prevents new instances of this at the source.
_LOCATION_POISONED_KEYWORDS = {
    "00000", "zuerich", "zurich", "affoltern", "merenschwand", "vereinigtes",
    "duesseldorf", "dusseldorf", "frankfurt", "postfach", "waedenswil",
    "60327", "(suisse)",
}


def _fix_location_poisoned_rules(conn):
    """Deletes the poisoned rules above and re-categorizes any
    already-imported transaction currently sitting under one of them — but
    only if it's still exactly what the (still-poisoned) rule set would
    predict for it right now, so a transaction Kevin has since corrected by
    hand (manually_corrected=1), or that reassigning has already moved onto
    some other rule, is left untouched. Naturally idempotent: once the
    poisoned rows are gone, the keyword lookup below finds nothing and the
    function returns immediately on every later init_db() call."""
    placeholders = ",".join("?" for _ in _LOCATION_POISONED_KEYWORDS)
    poisoned_rule_ids = {
        row["id"] for row in conn.execute(
            f"SELECT id FROM category_rules WHERE keyword IN ({placeholders})",
            tuple(_LOCATION_POISONED_KEYWORDS),
        )
    }
    if not poisoned_rule_ids:
        return

    categorizer = RuleBasedCategorizer(conn)
    affected_transaction_ids = []
    for row in conn.execute(
        "SELECT id, description, category_id FROM transactions WHERE manually_corrected = 0"
    ):
        predicted_category_id, _, rule_id = categorizer.predict(row["description"])
        if rule_id in poisoned_rule_ids and predicted_category_id == row["category_id"]:
            affected_transaction_ids.append(row["id"])

    # pending_transactions.suggested_rule_id has a foreign-key reference to
    # category_rules(id) with no ON DELETE clause — fetch (and re-predict)
    # any row using a poisoned rule *before* the DELETE below, or the
    # DELETE would fail with a foreign-key-constraint error.
    pending_placeholders = ",".join("?" for _ in poisoned_rule_ids)
    pending_rows = conn.execute(
        f"SELECT id, description FROM pending_transactions WHERE suggested_rule_id IN ({pending_placeholders})",
        tuple(poisoned_rule_ids),
    ).fetchall()

    conn.execute(
        f"DELETE FROM category_rules WHERE keyword IN ({placeholders})",
        tuple(_LOCATION_POISONED_KEYWORDS),
    )

    for row in pending_rows:
        category_id, confidence, rule_id = categorizer.predict(row["description"])
        conn.execute(
            "UPDATE pending_transactions SET category_id = ?, suggested_category_id = ?, "
            "suggested_rule_id = ?, category_confidence = ? WHERE id = ?",
            (category_id, category_id, rule_id, confidence if rule_id is not None else None, row["id"]),
        )

    for txn_id in affected_transaction_ids:
        description = conn.execute(
            "SELECT description FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()["description"]
        new_category_id, _, _ = categorizer.predict(description)
        conn.execute(
            "UPDATE transactions SET category_id = ? WHERE id = ?",
            (new_category_id, txn_id),
        )


def reset_db(db_path):
    """Wipe all data (transactions, pending rows, imported-file records,
    learned/seeded rules, categories, accounts, tags) and reseed the
    defaults — a clean-slate reset for repeated test imports, not a
    normal-operation code path."""
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transaction_tags")
    conn.execute("DELETE FROM tags")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM pending_transactions")
    conn.execute("DELETE FROM imported_files")
    conn.execute("DELETE FROM category_rules")
    conn.execute("DELETE FROM budgets")
    conn.execute("DELETE FROM categories")
    conn.execute("DELETE FROM seeded_defaults")
    conn.execute("DELETE FROM accounts")
    conn.execute("DELETE FROM settings")
    conn.commit()
    conn.close()
    return init_db(db_path)


def reset_imported_data(db_path):
    """Wipe only what an import produced (transactions, pending rows,
    imported-file records, accounts — accounts are re-derived from source
    on the next scan) so statements can be rescanned from scratch — unlike
    reset_db(), this leaves categories, learned/seeded rules, budgets, tags
    and settings untouched. Meant for repeatedly re-testing imports without
    losing the categorization the user has already trained."""
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transaction_tags")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM pending_transactions")
    conn.execute("DELETE FROM imported_files")
    conn.execute("DELETE FROM accounts")
    conn.commit()
    return conn
