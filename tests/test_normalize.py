from server.normalize import normalize_description


def test_normalize_lowercases():
    assert normalize_description("MIGROS Zürich") == "migros zuerich"


def test_normalize_folds_umlauts():
    assert normalize_description("Bäckerei Müller") == "baeckerei mueller"
    assert normalize_description("Straße") == "strasse"


def test_normalize_makes_ae_and_umlaut_spellings_equal():
    assert normalize_description("BAECKEREI BODE") == normalize_description("Bäckerei Bode")


def test_normalize_strips_card_number_suffix():
    result = normalize_description("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Migros")
    assert "7369" not in result
    assert "migros" in result


def test_normalize_strips_reference_number():
    result = normalize_description("Gutschrift TWINT: SCHWARTZ Auftrags-Nr. L113P111A9L6LQZ5-2")
    assert "l113p111a9l6lqz5-2" not in result
    assert "schwartz" in result


def test_normalize_collapses_whitespace():
    assert normalize_description("Migros   Zürich  ") == "migros zuerich"


def test_normalize_folds_french_and_italian_accents():
    assert normalize_description("Pathé Genève") == "pathe geneve"
    assert normalize_description("Hôpital de La Tour") == "hopital de la tour"
    assert normalize_description("Ospedale Città") == "ospedale citta"
