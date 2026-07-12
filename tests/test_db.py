import sqlite3

from server.db import (
    init_db, reset_db, reset_imported_data, get_or_create_account,
    DEFAULT_CATEGORIES, DEFAULT_CATEGORY_RULES,
)
from server.categorize import RuleBasedCategorizer


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "imported_files", "categories", "category_rules",
        "transactions", "pending_transactions",
    }.issubset(tables)
    conn.close()


def test_init_db_seeds_default_categories(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    names = {row["name"] for row in conn.execute("SELECT name FROM categories").fetchall()}
    assert names == set(DEFAULT_CATEGORIES)
    conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not fail or duplicate rows

    count = conn.execute("SELECT COUNT(*) as c FROM categories").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORIES)
    conn.close()


def test_init_db_seeds_default_category_rules(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    count = conn.execute("SELECT COUNT(*) as c FROM category_rules").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORY_RULES)

    migros_category_id = conn.execute(
        "SELECT category_id FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()["category_id"]
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    assert migros_category_id == lebensmittel_id
    conn.close()


def test_init_db_category_rules_are_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not fail (UNIQUE keyword) or duplicate

    count = conn.execute("SELECT COUNT(*) as c FROM category_rules").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORY_RULES)
    conn.close()


def test_default_category_rules_prefer_merchant_over_generic_twint_keyword(tmp_path):
    # Several ZKB statement lines route a merchant payment through TWINT
    # (e.g. "Belastung TWINT: SBB MOBILE BERN") — these must land in the
    # merchant's real category, not the generic "twint" person-to-person
    # transfer catch-all, which only wins when no more specific keyword
    # matches (categorize() prefers the longest matching keyword).
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Belastung TWINT: SBB MOBILE BERN") == "Transport"
    assert category_name("Belastung TWINT: PARKINGPAY-TWINT SCHLIEREN") == "Transport"
    assert category_name("Belastung TWINT: GALAXUS MOBILE ZURICH") == "Shopping"
    assert category_name("Belastung TWINT: BABY-WALZ AG ST. GALLEN") == "Shopping"
    assert category_name("Gutschrift TWINT: SCHWARTZ, PATRICK +41764535887") == "Privatüberweisungen"
    conn.close()


def test_default_category_rules_recognize_common_fast_food_and_cafe_chains(tmp_path):
    # Well-known chains a first-time import should already get right without
    # manual correction — including two real vendors from Kevin's own
    # statements that were previously landing in "Unkategorisiert".
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Subway Ruemlang 0000") == "Restaurants/Ausgang"
    assert category_name(
        "Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Steiner Flughafebeck AG"
    ) == "Restaurants/Ausgang"
    assert category_name("MCDONALDS ZUERICH HB") == "Restaurants/Ausgang"
    assert category_name("STARBUCKS COFFEE BASEL") == "Restaurants/Ausgang"
    assert category_name("BURGER KING WINTERTHUR") == "Restaurants/Ausgang"
    assert category_name("MANORA ZUERICH") == "Restaurants/Ausgang"
    conn.close()


def test_default_category_rules_recognize_patterns_mined_from_real_statements(tmp_path):
    # Derived by analyzing Kevin's full real transaction history (a year of
    # ZKB/Cornercard statements) for recurring merchants that were landing
    # in "Unkategorisiert" — see the categorization-improvement pass this
    # test documents. Each sample is the exact normalized text shape as it
    # actually appears on his statements (e.g. ZKB drops umlauts as bare
    # ASCII rather than folding them to "ae", unlike Cornercard).
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    # Lebensmittel
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Backerei Stutz 0891 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Lidl Affoltern 0891 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, SPAR DANKT 0891 Affoltern Auftrags-Nr.X") == "Lebensmittel"
    # Restaurants/Ausgang
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Lehmanns Backer-Imbiss 0540 Auftrags-Nr.X") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Sabsins Thai Take-Away 0450 Auftrags-Nr.X") == "Restaurants/Ausgang"
    assert category_name("PIZZAFALCONE,BONSTETTEN") == "Restaurants/Ausgang"
    # Transport
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Shell Birmensdorf 0890 Auftrags-Nr.X") == "Transport"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Dott scooter ride Auftrags-Nr.X") == "Transport"
    # Reisen
    assert category_name("SWISSINTLAIRLINES,FRANKFURTAM") == "Reisen"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, AIRALO 07990 Auftrags-Nr.X") == "Reisen"
    # Gesundheit
    assert category_name("Belastung Mobile Banking: Zahnarztpraxis Birmensdorf AG Auftrags-Nr.X") == "Gesundheit"
    assert category_name("Belastung TWINT: STADTSPITAL TRIEMLI ZURICH Auftrags-Nr.X") == "Gesundheit"
    # Versicherungen
    assert category_name("Belastung TWINT: AXA VERSICHERUNGEN AG WINTERTHUR Auftrags-Nr.X") == "Versicherungen"
    assert category_name("Belastung Mobile Banking: Swiss Life AG, General-Guisan-Quai 40, 8002 Auftrags-Nr.X") == "Versicherungen"
    # Abos
    assert category_name("Belastung eBill: Salt Mobile SA, Avenue de Malley 2, 1008 Prilly, CH Auftrags-Nr.X") == "Abos"
    assert category_name("Belastung TWINT: GALAXUS ABOS ZURICH Auftrags-Nr.X") == "Abos"
    # Sparen/Anlegen
    assert category_name("Belastung Dauerauftrag: Frankly Risky, 8904 Aesch ZH, CH Auftrags-Nr.X") == "Sparen/Anlegen"
    # Miete/Wohnen
    assert category_name("Belastung Dauerauftrag: Otto Markwalder, c/o Barth Real AG, 8055 Auftrags-Nr.X") == "Miete/Wohnen"
    # Lohn/Einkommen
    assert category_name("Gutschrift Salär: Kanton Zürich, Walcheplatz 1, 8090 Zürich, CH Auftrags-Nr.X") == "Lohn/Einkommen"
    assert category_name(
        "Gutschrift Salär: BSI BUSINESS SYSTEMS INTEGRATION AG, TAEFERNWEG 1 CH Auftrags-Nr.X"
    ) == "Lohn/Einkommen"
    # Shopping
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, IKEA AG, Spreitenbach (A Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Hornbach Baumarkt Affolt Auftrags-Nr.X") == "Shopping"
    # Cornercard's comma-joined format has no space between words at all —
    # same issue as "pizzafalcone"/"swissintlairlines" above, for the
    # existing space-separated "scooter planet"/"cutie socks"/"media markt"
    # keywords.
    assert category_name("SCOOTERPLANET,ZURICH") == "Shopping"
    assert category_name("CUTIESOCKS,ZURICH") == "Shopping"
    assert category_name("MEDIAMARKTSCHWEIZAG,DIETIKON") == "Shopping"
    # Versicherungen — ZKB drops the umlaut here too ("ÖKK" -> "OKK"), same
    # issue as the "bäckerei"/"backerei" pair above.
    assert category_name("Gutschrift Auftraggeber: OKK Kranken- und Unfallvers., Bahnhofstrasse Auftrags-Nr.X") == "Versicherungen"
    # Abos
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, NAVIGRAPH 00000 Auftrags-Nr.X") == "Abos"
    # Privatüberweisungen
    assert category_name("Gutschrift Auftraggeber: Kevin Jayaweera, Chilegässli 12d, 8904 Aesch Auftrags-Nr.X") == "Privatüberweisungen"
    conn.close()


def test_default_category_rules_recognize_third_mining_pass_patterns(tmp_path):
    # Derived by analyzing the full 18-month ZKB/Swisscard/Cornercard
    # statement history (extended back to May 2025) plus general knowledge
    # of common Swiss/German retail chains, insurers and payment
    # processors, cross-checked against RuleBasedCategorizer.predict() to
    # find what was still landing in "Unkategorisiert". Raised the
    # keyword-match rate from ~87% to 90.3% of all real transaction rows,
    # verified to introduce zero unintended category changes for anything
    # that already matched (see test_gutschrift_auftraggeber... below for
    # the one collision class found and fixed by NOT adding a keyword).
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    # Lebensmittel
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Aldi Suisse 11 0891 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, ALDI SUeD 60598 Frankfurt Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Tegut Filiale 2398 60598 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, kkiosk 0800 Zuerich Auftrags-Nr.X") == "Lebensmittel"
    # Restaurants/Ausgang
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Marche Take Away-5251 Do Auftrags-Nr.X") == "Restaurants/Ausgang"
    assert category_name("MARCOS,FRANKFURTAM") == "Restaurants/Ausgang"
    # Gesundheit
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Tierarztpraxis Kemper 8918 Auftrags-Nr.X") == "Gesundheit"
    # Shopping — clothing chains, furniture, sporting goods, books, print shop,
    # BNPL settlement collectors, and Amazon's bare-domain format.
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Zara Deutschland 3560 60313 Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, H & M 8001 Zuerich Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, C & A Mode Zuerich / 20 8001 Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Uniqlo Biebergasse 00000 Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, MANGO ZURICH BAHNHOFSTRA Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, XXXLUTZ 00000 BLUDENZ, AT Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Ochsner Sport / 407 8910 Auftrags-Nr.X") == "Shopping"
    assert category_name("Belastung Mobile Banking: Ex Libris AG, Lerzenstrasse 18, 8953 Auftrags-Nr.X") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, die kartenmacherei Auftrags-Nr.X") == "Shopping"
    assert category_name("Klarna Bank AB, Sveavaegen 46, 111 34 Stockholm, SE") == "Shopping"
    assert category_name("Riverty fuer Amazon, Guetersloherstrasse 123, 33145 Verl, DE") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, AMAZON.DE* Z888F2FY4 Auftrags-Nr.X") == "Shopping"
    # Freizeit
    assert category_name("SP INIBUILDS , LONDON , Vereinigtes Koenigreich") == "Freizeit"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Stockhornbahn AG 0376 Auftrags-Nr.X") == "Freizeit"
    # Truncated form actually seen on a real statement (PDF column width cut
    # off "...bahnen" to "...bahn") — the shortened "bergbah" keyword must
    # still catch the untruncated form too.
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Klosters-Madrisa Bergbahnen Auftrags-Nr.X") == "Freizeit"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, deinyogaweg, Eichacherstrasse 1 Auftrags-Nr.X") == "Freizeit"
    # Versicherungen
    assert category_name("Belastung eBill: Allianz Suisse Versicherungs-Gesellschaft AG, 8010 Auftrags-Nr.X") == "Versicherungen"
    # Abos
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Adobe 00000 Auftrags-Nr.X") == "Abos"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, MICROSOFT*STORE 0000 Auftrags-Nr.X") == "Abos"
    # Privatüberweisungen
    assert category_name("Belastung Dauerauftrag: Fabienne Jayaweera, Chilegaessli 12d, 8904 Auftrags-Nr.X") == "Privatüberweisungen"
    # Sonstiges
    assert category_name("Belastung Mobile Banking: SERAFE AG, Summelenweg 91, 8808 Pfaeffikon SZ Auftrags-Nr.X") == "Sonstiges"
    assert category_name("Belastung Mobile Banking: Strassenverkehrsamt Kanton Zuerich Auftrags-Nr.X") == "Sonstiges"
    conn.close()


def test_gutschrift_auftraggeber_prefix_defers_to_specific_company_keywords(tmp_path):
    # Regression guard for a rejected design: adding a generic
    # "gutschrift auftraggeber" catch-all under Privatüberweisungen (the
    # natural mirror image of the existing "kontouebertrag" outgoing-
    # transfer catch-all) seemed like a high-value addition — one keyword
    # for every future incoming person-to-person transfer — but ZKB reuses
    # that exact same "Gutschrift Auftraggeber: <name>, <address>" prefix
    # for insurer reimbursements, a school-district payment and other
    # credits that already have their own, shorter, correct keyword.
    # categorize() prefers the longest match, so a generic phrase long
    # enough to be meaningful was also long enough to beat "protekta" (8
    # chars) and "primarschulgemeinde" (20 chars), silently misfiling them
    # as plain transfers. No safe keyword length exists that both reads as
    # a real phrase and loses to every existing (and future) specific
    # keyword, so the generic catch-all was removed rather than patched
    # per-collision — this test guards against it reappearing.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name(
        "Gutschrift Auftraggeber: OKK Kranken- und Unfallvers., Bahnhofstrasse Auftrags-Nr.X"
    ) == "Versicherungen"
    assert category_name(
        "Gutschrift Auftraggeber: Helsana Versicherungen AG, Zuerichstrasse 130 Auftrags-Nr.X"
    ) == "Versicherungen"
    assert category_name(
        "Gutschrift Auftraggeber: Protekta Rechtsschutz-Versicherung AG, 3011 Bern, CH Auftrags-Nr.X"
    ) == "Versicherungen"
    assert category_name(
        "Gutschrift Auftraggeber: Primarschulgemeinde, Dettenbuehlstrasse 2, 8907 Wettswil Auftrags-Nr.X"
    ) == "Sonstiges"
    conn.close()


def test_default_category_keyword_groups_flatten_into_default_category_rules():
    # DEFAULT_CATEGORY_RULES is derived from the grouped-by-category source
    # (DEFAULT_CATEGORY_KEYWORD_GROUPS) rather than hand-maintained as a
    # flat list — this guards that the derivation is lossless (every
    # keyword shows up, correctly paired with its group's category) and
    # that no keyword was accidentally duplicated across two groups (the
    # category_rules.keyword UNIQUE constraint would silently drop the
    # second one via INSERT OR IGNORE, which is much easier to catch here
    # than by noticing a miscategorized import later).
    from server.db import DEFAULT_CATEGORY_KEYWORD_GROUPS

    expected = [
        (keyword, category)
        for category, keywords in DEFAULT_CATEGORY_KEYWORD_GROUPS.items()
        for keyword in keywords
    ]
    assert DEFAULT_CATEGORY_RULES == expected

    all_keywords = [keyword for keyword, _ in DEFAULT_CATEGORY_RULES]
    assert len(all_keywords) == len(set(all_keywords))


def test_default_category_rules_group_similar_merchants_under_one_category(tmp_path):
    # Consolidation pass: broaden rules so related merchants share a
    # category without needing one keyword per exact vendor name, per
    # Kevin's explicit request to reduce the number of narrow rules.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    # Lebensmittel
    assert category_name("Belastung TWINT: SUTERS HOFMART AESCH ZH") == "Lebensmittel"
    # Restaurants/Ausgang — generic "restaurant"/"cafe" now catch vendors
    # that don't match any specific chain keyword.
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Restaurant Felsenegg 0000") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Wal*Cafe Betschart 0000") == "Restaurants/Ausgang"
    assert category_name("Belastung TWINT: UBER EATS ZUERICH") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Legend Doener Zuerich") == "Restaurants/Ausgang"
    # Transport
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Avia Tankstelle Affoltern") == "Transport"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Agrola Bern") == "Transport"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, EKZ Sihlcity Parkhaus Ta") == "Transport"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Carwash Affoltern") == "Transport"
    # Miete/Wohnen
    assert category_name("Belastung Dauerauftrag: Otto Markwalder, Muster 3, 8000 Zuerich") == "Miete/Wohnen"
    # Versicherungen
    assert category_name("Gutschrift Auftraggeber: Protekta Rechtsschutz-Versicherung AG, 3011 Bern, CH") == "Versicherungen"
    assert category_name("Die Mobiliar Rechnung 2026") == "Versicherungen"
    # Gesundheit
    assert category_name("Belastung TWINT: ZAHNARZT MUSTER AESCH") == "Gesundheit"
    assert category_name("Belastung TWINT: TCM PRAXIS ZUERICH") == "Gesundheit"
    # Shopping
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, AMZN Mktp") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Jysk Duebendorf") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Brack.ch Willisau") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Klarna*ABOUT YOU") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Klarna*H M 0000") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, Klarna* H M 00000") == "Shopping"
    # Sonstiges
    assert category_name("Belastung TWINT: POST CH AG BERN / (QR)") == "Sonstiges"
    assert category_name("Belastung TWINT: BEVOLKERUNGSAMT STADT ZURICH ZURICH") == "Sonstiges"
    assert category_name("Belastung TWINT: EINWOHNERMELDEAMT MUSTERSTADT") == "Sonstiges"
    assert category_name("Belastung TWINT: GEMEINDEVERWALTUNG MUSTERSTADT") == "Sonstiges"
    # Lohn/Einkommen — a single general "Salär" keyword now covers any
    # employer, not one keyword per employer name.
    assert category_name("Gutschrift Salär: Irgendein Neuer Arbeitgeber AG, Musterstrasse 1, CH") == "Lohn/Einkommen"
    # Privatüberweisungen — a general "Kontoübertrag" keyword now covers
    # any account-to-account transfer, not one keyword per counterpart name.
    assert category_name("Gutschrift Kontoübertrag: Irgendjemand Neues, Musterstrasse 1, 8000 Zuerich") == "Privatüberweisungen"
    conn.close()


def test_default_category_rules_recognize_second_mining_pass(tmp_path):
    # Second real-data mining pass, after Kevin dropped a full year of both
    # ZKB and Swisscard/Cornercard statements into statements/ and asked to
    # relearn from the complete set — see the categorization-improvement
    # pass this test documents.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    # Lebensmittel
    assert category_name("TEGUTFILIALE2398,FRANKFURT") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Oswald Nahrungsmittel Gm") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, New Asia Market 0800 Zurich") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Asia Store GmbH 0800 Zurich") == "Lebensmittel"
    # Restaurants/Ausgang — "pizza" is now a generic catch-all, alongside
    # several specific vendors that don't contain "restaurant"/"cafe"/"pizza".
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Neue Pizza Muster 0000") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Brezelkonig AG 0800 Zuerich") == "Restaurants/Ausgang"
    assert category_name("COMPANYSZUERICH,ZUERICH") == "Restaurants/Ausgang"
    assert category_name("STARKEBAB,COSTACAPARIC") == "Restaurants/Ausgang"
    assert category_name("PEZZODIPANEFLUGHAFE,HAMBURG") == "Restaurants/Ausgang"
    assert category_name("LSMPANADASTORE,ZUERICH") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Namastey India Singh 0813") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Luckys Thai Food 0000") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Jack's Thai GmbH 0891") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Marmar Cuisine Orienta 0000") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Elvetino AG 0804 Zurich") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Triemlis Food Shop 0805") == "Restaurants/Ausgang"
    assert category_name("TRIEMLISFOODSHOP,ZUERICH") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Butegar Pizza 0000 Zuerich") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Boostbar 0000 Zuerich") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Il Quadrifoglio 00000") == "Restaurants/Ausgang"
    # Transport — "taxi" is now a generic catch-all instead of one keyword
    # per taxi company.
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Taxi Neuwagen Bern") == "Transport"
    assert category_name("100000008892954 Pedaggi A , Assago , Italien") == "Transport"
    assert category_name("SHOP.ASFINAG.AT,WIEN") == "Transport"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Parkdepot GmbH 0000") == "Transport"
    assert category_name("DOTTSCOOTERRIDE,ZUERICH") == "Transport"
    # Reisen
    assert category_name("LUFTHANSA 2202244494353 , BASEL") == "Reisen"
    assert category_name("RENTALCARS.COM , LONDON , VEREINIGTES KOENIGREI") == "Reisen"
    assert category_name("WWW.SUNNYCARS.CH,R.15591715") == "Reisen"
    # Shopping
    assert category_name("CANYON,KOBLENZ") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Suspension Center GmbH") == "Shopping"
    assert category_name("BIKE-IMPORT.CHAG,ZOLLIKOFEN") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Fielmann 0561 0000 Zurich") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, zooplus CH 447717421") == "Shopping"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 5770, SP EHRENKIND 74523") == "Shopping"
    # Abos
    assert category_name("WHOOP , WHOOP.COM , VEREINIGTE STAATEN") == "Abos"
    assert category_name("ONLYFANS.COM,LONDON") == "Abos"
    # Sonstiges
    assert category_name("Rundung") == "Sonstiges"
    assert category_name("Jahresbeitrag") == "Sonstiges"
    assert category_name("MOL*CORPORATEBENEFITS,41313013636") == "Sonstiges"
    assert category_name("Gutschrift Auftraggeber: Primarschulgemeinde, Dettenbuehlstrasse 2, 8907 Wettswil") == "Sonstiges"
    # Miete/Wohnen
    assert category_name(
        "Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Miteigentumergemeinschaf"
    ) == "Miete/Wohnen"
    # Gesundheit
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Amavita Zug 10254 6300 Zug") == "Gesundheit"
    assert category_name("Kopfwehzentrum Hirslanden AG, Forchstrassse 424, 8702 Zollikon, CH") == "Gesundheit"
    conn.close()


def test_default_category_rules_recognize_kevin_confirmed_merchants(tmp_path):
    # Five merchants flagged as too ambiguous to guess in the second mining
    # pass — Kevin identified what they actually are.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("BANKHAUSMETZLER,FRANKFURTAM") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Bankhaus Metzler 60329") == "Restaurants/Ausgang"
    assert category_name("ECHST.NET,AMSTERDAM") == "Sonstiges"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Reinhard AG 0301 Bern") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Hauptsitz Postfinance 0301") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, NVG Zentrum Oberdorf Aff") == "Sonstiges"
    conn.close()


def test_init_db_adds_learning_columns_to_category_rules(tmp_path):
    conn = init_db(tmp_path / "test.db")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(category_rules)")}
    assert {"match_count", "correction_count", "iban", "is_seeded", "created_at"} <= columns
    conn.close()


def test_init_db_adds_suggestion_columns_to_pending_transactions(tmp_path):
    conn = init_db(tmp_path / "test.db")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pending_transactions)")}
    assert {"suggested_category_id", "suggested_rule_id", "category_confidence"} <= columns
    conn.close()


def test_init_db_seeds_default_rules_without_an_unearned_confidence_head_start(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT match_count, correction_count, is_seeded FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()
    assert row["match_count"] == 0
    assert row["correction_count"] == 0
    assert row["is_seeded"] == 1
    conn.close()


def test_seeded_rules_start_below_the_auto_accept_confidence_threshold(tmp_path):
    # Regression guard for the bug where every seeded rule started with
    # match_count=3 (confidence 0.8), clearing CONFIDENCE_THRESHOLD (0.75)
    # before a single real transaction had ever validated the keyword — on
    # Kevin's real import this silently auto-accepted 899/934 (96%) rows,
    # many via rules with zero real confirmations, instead of the ~90% a
    # genuinely earned track record produces. A seeded rule must still need
    # at least one real confirmation before it can skip manual review.
    from server.categorize import CONFIDENCE_THRESHOLD, compute_confidence

    conn = init_db(tmp_path / "test.db")
    rows = conn.execute(
        "SELECT keyword, match_count, correction_count FROM category_rules WHERE is_seeded = 1"
    ).fetchall()
    conn.close()

    assert rows, "expected seeded rules to exist"
    for row in rows:
        confidence = compute_confidence(row["match_count"], row["correction_count"])
        assert confidence < CONFIDENCE_THRESHOLD, (
            f"seeded rule '{row['keyword']}' starts at confidence {confidence:.3f}, "
            f"already at/above CONFIDENCE_THRESHOLD ({CONFIDENCE_THRESHOLD}) with zero real confirmations"
        )


def test_init_db_migrates_existing_database_without_losing_data(tmp_path):
    # Simulates a database created before this migration existed.
    db_path = tmp_path / "test.db"
    old_conn = sqlite3.connect(db_path)
    old_conn.executescript("""
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            category_id INTEGER NOT NULL REFERENCES categories(id)
        );
        CREATE TABLE pending_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CHF',
            category_id INTEGER REFERENCES categories(id),
            source TEXT NOT NULL,
            file_id INTEGER
        );
    """)
    old_conn.execute("INSERT INTO categories (name) VALUES ('Lebensmittel')")
    old_conn.execute("INSERT INTO category_rules (keyword, category_id) VALUES ('migros', 1)")
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    # Pre-existing row survives with conservative (non-seed) defaults —
    # a migration must never silently rewrite a user's existing rule stats.
    migrated = conn.execute(
        "SELECT category_id, match_count, correction_count, is_seeded FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()
    assert migrated["category_id"] == 1
    assert migrated["match_count"] == 0
    assert migrated["correction_count"] == 0
    assert migrated["is_seeded"] == 0
    conn.close()


def test_init_db_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not raise (duplicate ALTER TABLE)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(category_rules)")}
    assert "match_count" in columns
    conn.close()


def test_reset_db_clears_all_data_and_reseeds_defaults(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO imported_files (hash, filename, source, imported_at) "
        "VALUES ('abc', 'test.csv', 'test', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'test', 0)",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO pending_transactions (date, description, amount_cents, currency, category_id, source) "
        "VALUES ('2026-03-02', 'Noch offen', -500, 'CHF', ?, 'test')",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('gelernteregel', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM imported_files").fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM category_rules WHERE keyword = 'gelernteregel'"
    ).fetchone()["c"] == 0

    names = {row["name"] for row in conn.execute("SELECT name FROM categories").fetchall()}
    assert names == set(DEFAULT_CATEGORIES)
    rule_count = conn.execute("SELECT COUNT(*) c FROM category_rules").fetchone()["c"]
    assert rule_count == len(DEFAULT_CATEGORY_RULES)
    conn.close()


def test_init_db_creates_budgets_table(tmp_path):
    conn = init_db(tmp_path / "test.db")

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "budgets" in tables
    conn.close()


def test_reset_db_clears_budgets(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO budgets (category_id, monthly_limit_cents) VALUES (?, 50000)",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM budgets").fetchone()["c"] == 0
    conn.close()


def test_init_db_creates_accounts_tags_tables(tmp_path):
    conn = init_db(tmp_path / "test.db")

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"accounts", "tags", "transaction_tags"}.issubset(tables)
    conn.close()


def test_get_or_create_account_creates_then_reuses_by_source_key(tmp_path):
    conn = init_db(tmp_path / "test.db")

    first_id = get_or_create_account(conn, "ZKB")
    second_id = get_or_create_account(conn, "ZKB")
    other_id = get_or_create_account(conn, "Cornercard")

    assert first_id == second_id
    assert first_id != other_id
    row = conn.execute("SELECT name FROM accounts WHERE id = ?", (first_id,)).fetchone()
    assert row["name"] == "ZKB"
    conn.close()


def test_init_db_backfills_account_id_for_pre_existing_rows(tmp_path):
    # Simulates a database from before the accounts table existed: rows
    # already have a `source` string but no account_id yet.
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'ZKB', 0)",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO pending_transactions (date, description, amount_cents, currency, category_id, source) "
        "VALUES ('2026-03-02', 'Noch offen', -500, 'CHF', ?, 'ZKB')",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)  # second call must backfill the NULL account_ids

    txn = conn.execute("SELECT account_id FROM transactions WHERE description = 'Testausgabe'").fetchone()
    pending = conn.execute("SELECT account_id FROM pending_transactions WHERE description = 'Noch offen'").fetchone()
    assert txn["account_id"] is not None
    assert txn["account_id"] == pending["account_id"]
    account = conn.execute("SELECT source_key, name FROM accounts WHERE id = ?", (txn["account_id"],)).fetchone()
    assert account["source_key"] == "ZKB"
    assert account["name"] == "ZKB"
    conn.close()


def test_init_db_backfill_never_overwrites_an_existing_account_id(tmp_path):
    # A row someone (or a rename) already pointed at a specific account
    # must not be silently reassigned on the next init_db() call — the
    # backfill only fills in NULLs.
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    other_account_id = get_or_create_account(conn, "Anderes Konto")
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, account_id, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'ZKB', ?, 0)",
        (lebensmittel_id, other_account_id),
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)

    txn = conn.execute("SELECT account_id FROM transactions WHERE description = 'Testausgabe'").fetchone()
    assert txn["account_id"] == other_account_id
    conn.close()


def test_reset_db_clears_accounts_and_tags(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    get_or_create_account(conn, "ZKB")
    conn.execute("INSERT INTO tags (name) VALUES ('Urlaub')")
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"] == 0
    conn.close()


def test_reset_imported_data_clears_accounts_but_keeps_tags(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    get_or_create_account(conn, "ZKB")
    conn.execute("INSERT INTO tags (name) VALUES ('Urlaub')")
    conn.commit()
    conn.close()

    conn = reset_imported_data(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"] == 1
    conn.close()


def test_reset_imported_data_clears_transactions_pending_and_imported_files(tmp_path):
    # A "clean slate for testing" reset: wipes only what an import produced
    # (transactions, pending rows, imported-file records) so statements can
    # be rescanned from scratch — but unlike reset_db(), leaves everything
    # the user configured/learned (categories, rules, budgets, settings)
    # untouched.
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO imported_files (hash, filename, source, imported_at) "
        "VALUES ('abc', 'test.csv', 'test', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'test', 0)",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO pending_transactions (date, description, amount_cents, currency, category_id, source) "
        "VALUES ('2026-03-02', 'Noch offen', -500, 'CHF', ?, 'test')",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_imported_data(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM imported_files").fetchone()["c"] == 0
    conn.close()


def test_reset_imported_data_keeps_learned_rules_categories_budgets_and_settings(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('gelernteregel', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.execute("INSERT INTO categories (name) VALUES ('Haustier')")
    conn.execute(
        "INSERT INTO budgets (category_id, monthly_limit_cents) VALUES (?, 50000)",
        (lebensmittel_id,),
    )
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = reset_imported_data(db_path)

    assert conn.execute(
        "SELECT COUNT(*) c FROM category_rules WHERE keyword = 'gelernteregel'"
    ).fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM categories WHERE name = 'Haustier'"
    ).fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM budgets").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"] == "false"
    conn.close()


def test_kreditkarten_ausgleich_category_is_excluded_from_totals(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT excluded_from_totals FROM categories WHERE name = 'Kreditkarten-Ausgleich'"
    ).fetchone()
    assert row["excluded_from_totals"] == 1
    conn.close()


def test_other_categories_are_not_excluded_from_totals(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT excluded_from_totals FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()
    assert row["excluded_from_totals"] == 0
    conn.close()


def test_default_category_rules_recognize_credit_card_settlement_lines(tmp_path):
    # Both sides of "pay off the credit card bill from the checking
    # account": the credit-card statement's own payment line, and the ZKB
    # checking-account's matching collection debit for each issuer.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Gutschrift TWINT: IHRE ZAHLUNG - BESTEN DANK") != "Sonstiges"
    assert category_name("IHREZAHLUNG–BESTENDANK") == "Kreditkarten-Ausgleich"
    assert category_name("Swisscard AECS GmbH, Postfach 227, 8810 Horgen, CH") == "Kreditkarten-Ausgleich"
    assert category_name("Corner Banca SA Cornercard, Via Canova 16, 6901 Lugano, CH") == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_migrates_ihre_zahlung_away_from_sonstiges(tmp_path):
    # A database created before this fix existed has "ihre zahlung" seeded
    # under "Sonstiges" — must be retargeted on the next init_db() call.
    db_path = tmp_path / "test.db"
    old_conn = init_db(db_path)
    sonstiges_id = old_conn.execute(
        "SELECT id FROM categories WHERE name = 'Sonstiges'"
    ).fetchone()["id"]
    old_conn.execute(
        "UPDATE category_rules SET category_id = ? WHERE keyword = 'ihre zahlung'",
        (sonstiges_id,),
    )
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    row = conn.execute(
        "SELECT c.name FROM category_rules r JOIN categories c ON r.category_id = c.id "
        "WHERE r.keyword = 'ihre zahlung'"
    ).fetchone()
    assert row["name"] == "Kreditkarten-Ausgleich"
    conn.close()


def test_default_category_rules_treat_saldovortrag_as_credit_card_settlement(tmp_path):
    # "Saldovortrag" (balance carried forward from the previous statement) is
    # the credit-card statement's own opening-balance line, not a new June
    # expense — it and "Ihre Zahlung" are the two halves of last month's
    # already-counted balance, and must be excluded from totals the same way.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, _, _ = categorizer.predict("Saldovortrag")
    name = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()["name"]
    assert name == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_migrates_saldovortrag_away_from_sonstiges(tmp_path):
    # A database created before this fix existed has "saldovortrag" seeded
    # under "Sonstiges" — must be retargeted on the next init_db() call.
    db_path = tmp_path / "test.db"
    old_conn = init_db(db_path)
    sonstiges_id = old_conn.execute(
        "SELECT id FROM categories WHERE name = 'Sonstiges'"
    ).fetchone()["id"]
    old_conn.execute(
        "UPDATE category_rules SET category_id = ? WHERE keyword = 'saldovortrag'",
        (sonstiges_id,),
    )
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    row = conn.execute(
        "SELECT c.name FROM category_rules r JOIN categories c ON r.category_id = c.id "
        "WHERE r.keyword = 'saldovortrag'"
    ).fetchone()
    assert row["name"] == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_seeds_default_settings(tmp_path):
    conn = init_db(tmp_path / "test.db")

    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    assert settings["auto_categorize_enabled"] == "true"
    assert settings["confidence_threshold"] == "0.75"
    conn.close()


def test_init_db_settings_are_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = init_db(db_path)  # second call must not overwrite Kevin's existing setting

    value = conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"]
    assert value == "false"
    conn.close()


def test_reset_db_restores_default_settings(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    value = conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"]
    assert value == "true"
    conn.close()
