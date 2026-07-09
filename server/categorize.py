from server.normalize import normalize_description

CONFIDENCE_THRESHOLD = 0.75

# Words too generic to ever be a useful learned keyword — they appear in
# nearly every transaction of their kind and would produce an overly broad,
# harmful rule. "twint" stays here deliberately: it remains an active,
# matchable pre-seeded rule (see DEFAULT_CATEGORY_RULES), but must never be
# re-learned as a *new* target keyword, or a correction on a merchant-routed
# TWINT payment (e.g. "TWINT: SBB MOBILE BERN") would overwrite the generic
# person-to-person-transfer rule that other merchant-specific keywords are
# deliberately designed to outrank by length.
STOPWORDS = {
    "einkauf", "online-einkauf", "belastung", "gutschrift", "zkb", "visa",
    "debit", "card", "karte", "mastercard", "mobile", "banking",
    "auftraggeber", "referenznummer", "twint",
}


def compute_confidence(match_count, correction_count):
    return (match_count + 1) / (match_count + correction_count + 2)


def _extract_keyword(normalized_description):
    # normalize_description() deliberately preserves punctuation (existing
    # seeded keywords like "apple.com/bill" and "* eats" rely on it for
    # matching), so a raw split() token can carry trailing/leading
    # punctuation the description text happens to have (e.g. "twint:" from
    # "Belastung TWINT: ..."). Strip that punctuation per-token here, only
    # for the stopword/length check and the keyword actually learned —
    # otherwise "twint:" would slip past the "twint" stopword entry and get
    # learned as a near-duplicate rule.
    words = []
    for raw_word in normalized_description.split():
        word = raw_word.strip(":,.;!?*/")
        if len(word) > 3 and word not in STOPWORDS:
            words.append(word)
    if not words:
        return ""
    return max(words, key=len)


class RuleBasedCategorizer:
    def __init__(self, conn):
        self._conn = conn

    def predict(self, description, amount_cents=None, currency=None, source=None):
        # amount_cents/currency/source are accepted but unused by this
        # keyword-matching implementation — kept on the interface per the
        # spec's ML-ready design, so a future MLCategorizer can use them
        # without changing any call site.
        normalized = normalize_description(description)
        rules = self._conn.execute(
            "SELECT id, keyword, category_id, match_count, correction_count "
            "FROM category_rules ORDER BY LENGTH(keyword) DESC"
        ).fetchall()
        for rule in rules:
            # Normalize the stored keyword too, not just the incoming
            # description: seed keywords are written with real umlauts for
            # readability (e.g. "bäckerei"), but normalize_description()
            # folds an incoming "BAECKEREI"/"Bäckerei" description to the
            # ASCII spelling "baeckerei" — comparing a raw umlaut keyword
            # against an already-folded description would never match.
            if normalize_description(rule["keyword"]) in normalized:
                confidence = compute_confidence(rule["match_count"], rule["correction_count"])
                return rule["category_id"], confidence, rule["id"]
        return self._uncategorized_id(), 0.0, None

    def learn(self, description, category_id, was_correction=False):
        # was_correction is accepted but unused by this implementation (the
        # upsert's CASE WHEN already infers "did the target category change"
        # from the data itself) — kept on the interface per the spec's
        # ML-ready design, so a future implementation that wants to weight
        # corrections differently from fresh assignments can use it.
        normalized = normalize_description(description)
        keyword = _extract_keyword(normalized)
        if not keyword:
            return
        self._conn.execute(
            "INSERT INTO category_rules "
            "(keyword, category_id, match_count, correction_count, is_seeded, created_at) "
            "VALUES (?, ?, 1, 0, 0, datetime('now')) "
            "ON CONFLICT(keyword) DO UPDATE SET "
            "category_id = excluded.category_id, "
            "match_count = CASE WHEN category_rules.category_id = excluded.category_id "
            "                    THEN category_rules.match_count + 1 ELSE 1 END, "
            "correction_count = CASE WHEN category_rules.category_id = excluded.category_id "
            "                         THEN category_rules.correction_count ELSE 0 END",
            (keyword, category_id),
        )
        self._conn.commit()

    def _uncategorized_id(self):
        row = self._conn.execute(
            "SELECT id FROM categories WHERE name = 'Unkategorisiert'"
        ).fetchone()
        return row["id"] if row else None
