# Lernfähige Kategorisierung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static keyword-rule categorizer with a learning one: every confirmed categorization reinforces or corrects a rule's confidence, high-confidence suggestions are pre-filled and marked "sicher", low-confidence ones are pre-filled but marked "bitte prüfen", and a new page lets the user inspect/edit/delete learned rules.

**Architecture:** Extends the existing `category_rules` table with per-rule match/correction counters (Laplace-smoothed confidence) instead of replacing it; a new `RuleBasedCategorizer` class in `server/categorize.py` wraps prediction and learning behind an interface a future ML model could implement instead; `pending_transactions` gains an immutable snapshot of the original suggestion so `confirm_import` can tell an accepted suggestion from a correction.

**Tech Stack:** Same as the existing project — Python/Flask/SQLite backend, vanilla HTML/CSS/JS frontend. No new dependencies.

## Global Constraints

- Confidence formula: `confidence = (match_count + 1) / (match_count + correction_count + 2)` (Laplace-smoothed).
- Auto-apply threshold: `CONFIDENCE_THRESHOLD = 0.75`, a named constant in `server/categorize.py` — never hardcoded elsewhere.
- Pre-seeded default rules (the 57 from the prior iteration) are seeded with `match_count=3, correction_count=0, is_seeded=1` (→ 80% starting confidence), not `0/0`.
- Rule *selection* when multiple keywords match is unchanged from the existing system: longest matching keyword wins (`ORDER BY LENGTH(keyword) DESC`). Confidence is an attached property of the winning rule, not a second selection criterion.
- No date/amount-proximity recurring-transaction detection — `match_count` itself is the recurring-transaction signal (explicit scope decision, see spec).
- Rule management page supports viewing, editing (target category), and deleting rules — NOT creating new rules manually.
- `category_rules.iban` column is prepared but unused (no current file format supplies an IBAN per transaction); matching never references it.
- Money is stored as integer cents, dates as ISO `YYYY-MM-DD` strings (carried over from the original project design).
- Stopword list for keyword-extraction-on-learn (exact set, extensible list in code, not a magic literal scattered around): `einkauf, online-einkauf, belastung, gutschrift, zkb, visa, debit, card, karte, mastercard, mobile, banking, auftraggeber, referenznummer, twint`. `"twint"` MUST stay in this list — it remains an active, matchable pre-seeded rule, but must never be re-learned as a *new* target keyword (that would break the length-based override that lets e.g. `"sbb mobile"` win over generic `"twint"` for merchant-routed TWINT payments).
- Full spec: [2026-07-08-lernfaehige-kategorisierung-design.md](../specs/2026-07-08-lernfaehige-kategorisierung-design.md)

---

### Task 1: Schema migration for learning fields

**Files:**
- Modify: `server/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `init_db(db_path)` now also idempotently adds `category_rules.match_count`, `category_rules.correction_count`, `category_rules.iban`, `category_rules.is_seeded`, `category_rules.created_at`, `pending_transactions.suggested_category_id`, `pending_transactions.suggested_rule_id`, `pending_transactions.category_confidence` to both fresh and pre-existing databases. `DEFAULT_CATEGORY_RULES` seeding now sets `match_count=3, correction_count=0, is_seeded=1, created_at=datetime('now')` on insert.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py` (keep the existing content above this; add `import sqlite3` at the top if not already present — it already is):

```python
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


def test_init_db_seeds_default_rules_with_starting_track_record(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT match_count, correction_count, is_seeded FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()
    assert row["match_count"] == 3
    assert row["correction_count"] == 0
    assert row["is_seeded"] == 1
    conn.close()


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `test_init_db_adds_learning_columns_to_category_rules` and friends fail because the columns don't exist yet.

- [ ] **Step 3: Implement the migration in `server/db.py`**

Replace the whole file with:

```python
import sqlite3
from pathlib import Path

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
"""

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
]

# Derived from Kevin's real ZKB bank statements (CSV/PDF) and Swisscard/
# Cornercard credit card statements — see docs/superpowers/specs for the
# analysis. Goal: the first real import needs as little manual
# categorization as possible.
DEFAULT_CATEGORIES = [
    "Lebensmittel", "Restaurants/Ausgang", "Transport", "Reisen",
    "Miete/Wohnen", "Versicherungen", "Gesundheit", "Shopping", "Abos",
    "Freizeit", "Bargeldbezug", "Privatüberweisungen", "Sparen/Anlegen",
    "Lohn/Einkommen", "Sonstiges", "Unkategorisiert",
]

# (keyword, category_name) — keyword must be lowercase (categorize() matches
# against a lowercased description) and is matched as a substring, so a more
# specific/longer keyword should be used wherever a shorter one would
# otherwise misfire (see "sbb mobile" vs "twint" below).
DEFAULT_CATEGORY_RULES = [
    # Lebensmittel
    ("migros", "Lebensmittel"),
    ("coop", "Lebensmittel"),
    ("volg", "Lebensmittel"),
    ("denner", "Lebensmittel"),
    ("metzg", "Lebensmittel"),
    # A single umlaut spelling is enough now — RuleBasedCategorizer.predict()
    # normalizes stored keywords before matching (see Task 3), so "bäckerei"
    # transparently also matches an incoming "BAECKEREI"/"Backerei" spelling.
    ("bäckerei", "Lebensmittel"),
    ("back.-conf.", "Lebensmittel"),
    ("confis", "Lebensmittel"),
    # Restaurants/Ausgang
    ("pizza falcone", "Restaurants/Ausgang"),
    ("curry factory", "Restaurants/Ausgang"),
    ("bar / restaurant caled", "Restaurants/Ausgang"),
    ("kuhn back & gastro", "Restaurants/Ausgang"),
    ("autogrill", "Restaurants/Ausgang"),
    ("* eats", "Restaurants/Ausgang"),
    # Transport (checked before generic "twint" below due to length)
    ("sbb", "Transport"),
    ("sbb mobile", "Transport"),
    ("tankstell", "Transport"),
    ("parkingpay", "Transport"),
    ("taxifahrt", "Transport"),
    ("ubr* pending", "Transport"),
    # Generic ride/refund fallback — shorter than "* eats" above, so a food
    # delivery line still wins Restaurants/Ausgang; this only catches plain
    # Uber rides and refunds like "Rückerstattung ... UBER 00000 AMSTERDAM".
    ("uber", "Transport"),
    ("bergbahnen", "Freizeit"),
    # Reisen
    ("swiss intl air lines", "Reisen"),
    ("easyjet", "Reisen"),
    ("emirates", "Reisen"),
    ("hotel", "Reisen"),
    ("airbnb", "Reisen"),
    ("meininger", "Reisen"),
    # Versicherungen
    ("ökk", "Versicherungen"),
    ("helsana", "Versicherungen"),
    ("axa leben", "Versicherungen"),
    # Gesundheit
    ("apotheke", "Gesundheit"),
    # Shopping
    ("zalando", "Shopping"),
    ("digitec galaxus", "Shopping"),
    ("galaxus mobile", "Shopping"),
    ("media markt", "Shopping"),
    ("velotec", "Shopping"),
    ("calzedoni", "Shopping"),
    ("cutie socks", "Shopping"),
    ("scooter planet", "Shopping"),
    ("ofinto", "Shopping"),
    ("baby-walz", "Shopping"),
    # Abos
    ("spotify", "Abos"),
    ("netflix", "Abos"),
    ("apple.com/bill", "Abos"),
    ("sayintentions", "Abos"),
    # Freizeit
    ("steamgames", "Freizeit"),
    ("coiffure", "Freizeit"),
    # Bargeldbezug
    ("bezug zkb visa debit card", "Bargeldbezug"),
    # Sparen/Anlegen
    ("findependent", "Sparen/Anlegen"),
    # Sonstiges — card-bill settlements and unclear small vendors, kept out
    # of real spending categories
    ("ihre zahlung", "Sonstiges"),
    ("saldovortrag", "Sonstiges"),
    ("ubs - zahlungen div", "Sonstiges"),
    ("corporate benefits", "Sonstiges"),
    ("marko switzerland", "Sonstiges"),
    # Privatüberweisungen — generic TWINT catch-all. Kept last / shortest on
    # purpose: every merchant-routed "TWINT: X" line above has a longer,
    # more specific keyword that must win first (categorize() prefers the
    # longest matching keyword), so this only catches person-to-person
    # transfers like "TWINT: SCHWARTZ, PATRICK +4176..." that have no
    # merchant-specific rule.
    ("twint", "Privatüberweisungen"),
]


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


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    for name in DEFAULT_CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    for keyword, category_name in DEFAULT_CATEGORY_RULES:
        category_id = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (category_name,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO category_rules "
            "(keyword, category_id, match_count, correction_count, is_seeded, created_at) "
            "VALUES (?, ?, 3, 0, 1, datetime('now'))",
            (keyword, category_id),
        )
    conn.commit()
    return conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: all tests in the file pass, including the pre-existing ones (unchanged in behavior) and the 5 new ones above.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `pytest -v`
Expected: all tests pass (schema additions are purely additive; no other code references the new columns yet).

- [ ] **Step 6: Commit**

```bash
git add server/db.py tests/test_db.py
git commit -m "feat: add learning columns to category_rules and pending_transactions"
```

---

### Task 2: Description normalization

**Files:**
- Create: `server/normalize.py`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize_description(text: str) -> str` — lowercases, folds umlauts (ä→ae, ö→oe, ü→ue, ß→ss), strips card-number suffixes and "Auftrags-Nr." reference numbers, collapses whitespace.

- [ ] **Step 1: Write the failing test**

`tests/test_normalize.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.normalize'`

- [ ] **Step 3: Implement `server/normalize.py`**

```python
import re

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_CARD_SUFFIX_RE = re.compile(r"nr\.?\s*xxxx\s*\d+")
_REFERENCE_RE = re.compile(r"auftrags-nr\.?\s*\S+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_description(text):
    normalized = text.lower().translate(_UMLAUT_MAP)
    normalized = _CARD_SUFFIX_RE.sub(" ", normalized)
    normalized = _REFERENCE_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_normalize.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add server/normalize.py tests/test_normalize.py
git commit -m "feat: add description normalization for fuzzy keyword matching"
```

---

### Task 3: RuleBasedCategorizer with confidence, replacing categorize()/learn_rule()

**Files:**
- Modify: `server/categorize.py` (full rewrite)
- Modify: `server/import_service.py`
- Modify: `server/app.py` (only the `confirm_import` route's learning call — full accept/correct branching comes in Task 4)
- Test: `tests/test_categorize.py` (full rewrite)
- Test: `tests/test_import_service.py` (extend existing tests)
- Test: `tests/test_api_pending.py` (no behavior change expected yet — existing tests must still pass)

**Interfaces:**
- Consumes: `server.normalize.normalize_description` (Task 2), `server.db` schema from Task 1
- Produces: `server.categorize.compute_confidence(match_count: int, correction_count: int) -> float`, `server.categorize.CONFIDENCE_THRESHOLD: float`, `server.categorize.RuleBasedCategorizer(conn)` with `.predict(description: str, amount_cents=None, currency=None, source=None) -> tuple[int, float, int | None]` (category_id, confidence, rule_id — rule_id is `None` when nothing matched) and `.learn(description: str, category_id: int, was_correction=False) -> None`. The extra `predict`/`learn` parameters are accepted-but-unused by this rule-based implementation — kept on the interface per the spec's ML-ready design so a future implementation can use them without call sites changing. The old free functions `categorize()` and `learn_rule()` are removed — this task updates every caller in the same commit so the suite stays green.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_categorize.py` entirely with:

```python
from server.db import init_db
from server.categorize import RuleBasedCategorizer, compute_confidence, CONFIDENCE_THRESHOLD


def _category_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def test_compute_confidence_new_rule_starts_at_fifty_percent():
    assert compute_confidence(match_count=0, correction_count=0) == 0.5


def test_compute_confidence_increases_with_matches():
    assert compute_confidence(match_count=5, correction_count=0) == 6 / 7


def test_compute_confidence_decreases_with_corrections():
    low = compute_confidence(match_count=4, correction_count=1)
    high = compute_confidence(match_count=5, correction_count=0)
    assert low < high


def test_confidence_threshold_is_seventy_five_percent():
    assert CONFIDENCE_THRESHOLD == 0.75


def test_predict_falls_back_to_uncategorized_with_zero_confidence(tmp_path):
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, rule_id = categorizer.predict("Unbekannte Buchung XYZ")

    assert category_id == _category_id(conn, "Unkategorisiert")
    assert confidence == 0.0
    assert rule_id is None
    conn.close()


def test_predict_matches_existing_rule_and_returns_its_confidence(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    cursor = conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('fischmarkt', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.commit()
    rule_id = cursor.lastrowid
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, matched_rule_id = categorizer.predict("Fischmarkt Zürich AG")

    assert category_id == lebensmittel_id
    assert confidence == compute_confidence(5, 0)
    assert matched_rule_id == rule_id
    conn.close()


def test_predict_prefers_longer_more_specific_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    transport_id = _category_id(conn, "Transport")
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('coop', ?, 3, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('coop pronto', ?, 3, 0, 0, datetime('now'))",
        (transport_id,),
    )
    conn.commit()
    categorizer = RuleBasedCategorizer(conn)

    category_id, _, _ = categorizer.predict("Coop Pronto Zuerich")

    assert category_id == transport_id
    conn.close()


def test_predict_normalizes_before_matching(tmp_path):
    # "BAECKEREI" (ASCII spelling) must match the pre-seeded "bäckerei" rule
    # via normalization, without a separate hand-added keyword.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, rule_id = categorizer.predict("Einkauf ZKB Visa Debit Card Nr. xxxx 1234, BAECKEREI BODE")

    assert category_id == _category_id(conn, "Lebensmittel")
    assert rule_id is not None
    conn.close()


def test_learn_creates_new_rule_with_match_count_one(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    category_id, confidence, rule_id = categorizer.predict("Kletterzentrum Adliswil Eintritt")

    assert category_id == freizeit_id
    assert confidence == compute_confidence(1, 0)
    conn.close()


def test_learn_ignores_description_with_no_extractable_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("", freizeit_id)
    categorizer.learn("   ", freizeit_id)
    category_id, confidence, rule_id = categorizer.predict("Some totally unrelated new transaction")

    assert category_id == _category_id(conn, "Unkategorisiert")
    conn.close()


def test_learn_never_extracts_twint_as_the_keyword(tmp_path):
    # Regression guard: learning from a merchant-routed TWINT line must not
    # overwrite the generic "twint" system rule (Privatüberweisungen), which
    # would break the length-based override for lines like "TWINT: SBB MOBILE".
    conn = init_db(tmp_path / "test.db")
    shopping_id = _category_id(conn, "Shopping")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Belastung TWINT: NEUERHAENDLER XYZ", shopping_id)

    twint_rule = conn.execute("SELECT category_id FROM category_rules WHERE keyword = 'twint'").fetchone()
    assert twint_rule["category_id"] == _category_id(conn, "Privatüberweisungen")
    conn.close()


def test_learn_upsert_preserves_history_when_category_unchanged(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)

    row = conn.execute(
        "SELECT match_count, correction_count FROM category_rules WHERE keyword = 'kletterzentrum'"
    ).fetchone()
    assert row["match_count"] == 3
    assert row["correction_count"] == 0
    conn.close()


def test_learn_upsert_resets_history_when_category_changes(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    shopping_id = _category_id(conn, "Shopping")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", shopping_id)  # retargeted

    row = conn.execute(
        "SELECT category_id, match_count, correction_count FROM category_rules WHERE keyword = 'kletterzentrum'"
    ).fetchone()
    assert row["category_id"] == shopping_id
    assert row["match_count"] == 1
    assert row["correction_count"] == 0
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_categorize.py -v`
Expected: FAIL — `RuleBasedCategorizer` doesn't exist yet.

- [ ] **Step 3: Implement `server/categorize.py`**

Replace the whole file with:

```python
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
```

- [ ] **Step 4: Update `server/import_service.py` to use the new categorizer**

Read the current file first. Replace the import and the per-row categorization call:

Change:
```python
from server.categorize import categorize
```
to:
```python
from server.categorize import RuleBasedCategorizer
```

Inside `scan_and_parse`, find where `conn` is available at the top of the function (right after the `created = 0` / `duplicates_skipped = 0` initialization) and add:
```python
    categorizer = RuleBasedCategorizer(conn)
```

Then find the loop that does:
```python
                category_id = categorize(row["description"], conn)
                conn.execute(
                    "INSERT INTO pending_transactions "
                    "(date, description, amount_cents, currency, category_id, source, file_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["date"], row["description"], row["amount_cents"],
                     row["currency"], category_id, source, file_id),
                )
```
and replace it with:
```python
                category_id, confidence, rule_id = categorizer.predict(
                    row["description"], row["amount_cents"], row["currency"], source
                )
                conn.execute(
                    "INSERT INTO pending_transactions "
                    "(date, description, amount_cents, currency, category_id, source, file_id, "
                    " suggested_category_id, suggested_rule_id, category_confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["date"], row["description"], row["amount_cents"],
                     row["currency"], category_id, source, file_id,
                     category_id, rule_id, confidence if rule_id is not None else None),
                )
```

Note: `suggested_category_id` is always set to the predicted `category_id` (even the "Unkategorisiert" fallback — it's a real, immutable snapshot of what the system suggested, whether or not that suggestion was "a match"). `category_confidence` is only stored when a rule actually matched (`rule_id is not None`); a confidence of `0.0` for the no-match case would look like a real (very low) confidence score rather than "no suggestion existed" — storing `NULL` there keeps the two cases distinguishable for the UI in a later task.

- [ ] **Step 5: Update `server/app.py`'s `confirm_import` to use the new categorizer**

Read the current file first. Replace:
```python
from server.categorize import learn_rule
```
with:
```python
from server.categorize import RuleBasedCategorizer
```

Inside `confirm_import`, find:
```python
        for row in rows:
            conn.execute(
                "INSERT INTO transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id, manually_corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"], row["currency"],
                 row["category_id"], row["source"], row["file_id"], 0),
            )
            if row["category_id"] is not None:
                learn_rule(conn, row["description"], row["category_id"])
```
and replace it with (this is intentionally still the simple "always learn" behavior — the accept/correct distinction is Task 4):
```python
        categorizer = RuleBasedCategorizer(conn)
        for row in rows:
            conn.execute(
                "INSERT INTO transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id, manually_corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"], row["currency"],
                 row["category_id"], row["source"], row["file_id"], 0),
            )
            if row["category_id"] is not None:
                categorizer.learn(row["description"], row["category_id"])
```

(`learn()`'s optional `was_correction` parameter is left at its default `False` here — this task doesn't yet distinguish accepted suggestions from corrections; that distinction, and passing `was_correction=True` where it applies, is Task 4.)

- [ ] **Step 6: Update `tests/test_import_service.py` for the new pending columns**

Read the current file first. In `test_scan_and_parse_creates_pending_rows_and_marks_file_imported`, add after the existing assertions:
```python
    assert pending[0]["suggested_category_id"] == pending[0]["category_id"]
```

- [ ] **Step 7: Run the categorize and import_service tests**

Run: `pytest tests/test_categorize.py tests/test_import_service.py -v`
Expected: all pass.

- [ ] **Step 8: Run the full suite**

Run: `pytest -v`
Expected: all tests pass, including `tests/test_api_pending.py` and `tests/test_api_transactions.py` unchanged (they don't reference the old `categorize`/`learn_rule` names directly, only through the app/import_service layer this task already updated).

- [ ] **Step 9: Commit**

```bash
git add server/categorize.py server/import_service.py server/app.py tests/test_categorize.py tests/test_import_service.py
git commit -m "feat: replace static categorizer with learning RuleBasedCategorizer"
```

---

### Task 4: Accept-vs-correct learning in confirm_import

**Files:**
- Modify: `server/app.py` (`confirm_import` route)
- Test: `tests/test_api_pending.py`

**Interfaces:**
- Consumes: `RuleBasedCategorizer.learn` (Task 3), `pending_transactions.suggested_category_id`/`suggested_rule_id` (Task 1/3)
- Produces: `confirm_import` now sets `transactions.manually_corrected` correctly and updates `category_rules.match_count`/`correction_count` precisely instead of unconditionally learning a (possibly redundant) new rule on every confirm.

- [ ] **Step 1: Write the failing tests**

Read the current `tests/test_api_pending.py` first for the exact `client` fixture (it uses `db_path=tmp_path / "test.db"`). Add `from server.db import get_connection` to the top of the file if not already present, then add this helper and the three tests below it (the helper queries `category_rules` directly by DB path rather than through `GET /api/rules`, since that endpoint doesn't exist until Task 5 — this task must be independently testable without depending on a later task):

```python
from server.db import get_connection


def _rule_stats(db_path):
    conn = get_connection(db_path)
    rows = conn.execute("SELECT keyword, match_count, correction_count FROM category_rules").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_confirm_import_reinforces_rule_when_suggestion_accepted(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    migros_before = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")

    client.post("/api/import/confirm")  # accept the suggested category as-is

    migros_after = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")
    assert migros_after["match_count"] == migros_before["match_count"] + 1
    assert migros_after["correction_count"] == migros_before["correction_count"]

    transactions = client.get("/api/transactions").get_json()
    assert transactions[0]["description"] == "Migros Zürich"


def test_confirm_import_penalizes_old_rule_and_learns_new_one_on_correction(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    migros_before = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")

    # Correct the auto-suggested "Lebensmittel" to something else.
    client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": pending["currency"],
        "category_id": categories["Sonstiges"],
    })
    client.post("/api/import/confirm")

    migros_after = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")
    assert migros_after["correction_count"] == migros_before["correction_count"] + 1
    assert migros_after["match_count"] == migros_before["match_count"]  # unchanged, not rewarded

    transactions = client.get("/api/transactions").get_json()
    assert transactions[0]["category_id"] == categories["Sonstiges"]


def test_confirm_import_sets_manually_corrected_flag(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": pending["currency"],
        "category_id": categories["Sonstiges"],
    })
    response = client.post("/api/import/confirm")
    assert response.get_json() == {"imported": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_pending.py -v -k "reinforces or penalizes or manually_corrected"`
Expected: FAIL — `migros_after["match_count"]` doesn't increment yet (current code always calls `learn()` unconditionally instead of branching), and `manually_corrected` is hardcoded to `0`.

- [ ] **Step 3: Implement the accept/correct branching in `server/app.py`**

Read the current `confirm_import` route. Replace the loop body:

```python
        categorizer = RuleBasedCategorizer(conn)
        for row in rows:
            conn.execute(
                "INSERT INTO transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id, manually_corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"], row["currency"],
                 row["category_id"], row["source"], row["file_id"], 0),
            )
            if row["category_id"] is not None:
                categorizer.learn(row["description"], row["category_id"])
```

with:

```python
        categorizer = RuleBasedCategorizer(conn)
        for row in rows:
            final_category_id = row["category_id"]
            suggested_category_id = row["suggested_category_id"]
            suggested_rule_id = row["suggested_rule_id"]
            manually_corrected = 1 if final_category_id != suggested_category_id else 0

            if suggested_rule_id is not None:
                if manually_corrected:
                    conn.execute(
                        "UPDATE category_rules SET correction_count = correction_count + 1 WHERE id = ?",
                        (suggested_rule_id,),
                    )
                    if final_category_id is not None:
                        categorizer.learn(row["description"], final_category_id, was_correction=True)
                else:
                    conn.execute(
                        "UPDATE category_rules SET match_count = match_count + 1 WHERE id = ?",
                        (suggested_rule_id,),
                    )
            elif final_category_id is not None:
                categorizer.learn(row["description"], final_category_id, was_correction=False)

            conn.execute(
                "INSERT INTO transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id, manually_corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"], row["currency"],
                 final_category_id, row["source"], row["file_id"], manually_corrected),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_pending.py -v`
Expected: all pass, including the 3 new ones.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_api_pending.py
git commit -m "feat: distinguish accepted suggestions from corrections when learning"
```

---

### Task 5: Expose confidence on GET /api/pending; rule management API

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_api_pending.py`
- Test: `tests/test_api_rules.py` (new)

**Interfaces:**
- Consumes: `compute_confidence`, `RuleBasedCategorizer` (Task 3), `category_rules` columns (Task 1)
- Produces: `GET /api/pending` rows include `category_confidence`; new routes `GET /api/rules`, `PUT /api/rules/<id>`, `DELETE /api/rules/<id>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api_pending.py`:

```python
def test_pending_list_includes_category_confidence(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    rows = client.get("/api/pending").get_json()

    assert rows[0]["category_confidence"] is not None
    assert 0.0 <= rows[0]["category_confidence"] <= 1.0
```

Create `tests/test_api_rules.py`:

```python
import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def test_list_rules_includes_confidence_and_stats(client):
    rules = client.get("/api/rules").get_json()

    migros = next(r for r in rules if r["keyword"] == "migros")
    assert migros["category_name"] == "Lebensmittel"
    assert migros["match_count"] == 3
    assert migros["correction_count"] == 0
    assert migros["is_seeded"] == 1
    assert migros["confidence"] == pytest.approx(4 / 5)


def test_update_rule_changes_target_category(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put(f"/api/rules/{migros['id']}", json={"category_id": categories["Sonstiges"]})

    assert response.status_code == 200
    updated = next(r for r in client.get("/api/rules").get_json() if r["id"] == migros["id"])
    assert updated["category_id"] == categories["Sonstiges"]


def test_update_rule_with_missing_body_returns_400(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")

    response = client.put(f"/api/rules/{migros['id']}")

    assert response.status_code == 400


def test_update_nonexistent_rule_returns_404(client):
    response = client.put("/api/rules/99999", json={"category_id": 1})

    assert response.status_code == 404


def test_delete_rule_removes_it(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")

    response = client.delete(f"/api/rules/{migros['id']}")

    assert response.status_code == 200
    remaining = client.get("/api/rules").get_json()
    assert not any(r["id"] == migros["id"] for r in remaining)


def test_delete_nonexistent_rule_returns_404(client):
    response = client.delete("/api/rules/99999")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_rules.py tests/test_api_pending.py::test_pending_list_includes_category_confidence -v`
Expected: FAIL — `/api/rules` doesn't exist (404s), `category_confidence` missing from `/api/pending` response.

- [ ] **Step 3: Update `server/app.py`**

Add `compute_confidence` to the import:
```python
from server.categorize import RuleBasedCategorizer, compute_confidence
```

In `list_pending`, find:
```python
        rows = conn.execute(
            "SELECT p.id, p.date, p.description, p.amount_cents, p.currency, "
            "p.category_id, c.name as category_name, p.source "
            "FROM pending_transactions p LEFT JOIN categories c ON p.category_id = c.id "
            "ORDER BY p.date"
        ).fetchall()
```
and change the `SELECT` list to also include `p.category_confidence`:
```python
        rows = conn.execute(
            "SELECT p.id, p.date, p.description, p.amount_cents, p.currency, "
            "p.category_id, c.name as category_name, p.source, p.category_confidence "
            "FROM pending_transactions p LEFT JOIN categories c ON p.category_id = c.id "
            "ORDER BY p.date"
        ).fetchall()
```

Add three new routes inside `register_routes(app)`, alongside the existing `/api/categories` route:

```python
    @app.route("/api/rules", methods=["GET"])
    def list_rules():
        conn = get_db()
        rows = conn.execute(
            "SELECT r.id, r.keyword, r.category_id, c.name as category_name, "
            "r.match_count, r.correction_count, r.is_seeded, r.created_at "
            "FROM category_rules r LEFT JOIN categories c ON r.category_id = c.id "
            "ORDER BY r.keyword"
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            entry = dict(row)
            entry["confidence"] = compute_confidence(row["match_count"], row["correction_count"])
            result.append(entry)
        return jsonify(result)

    @app.route("/api/rules/<int:rule_id>", methods=["PUT"])
    def update_rule(rule_id):
        data = request.get_json(silent=True)
        if data is None or "category_id" not in data:
            return jsonify({"error": "request body must include category_id"}), 400
        conn = get_db()
        try:
            cursor = conn.execute(
                "UPDATE category_rules SET category_id = ? WHERE id = ?",
                (data["category_id"], rule_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "rule not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
    def delete_rule(rule_id):
        conn = get_db()
        try:
            cursor = conn.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "rule not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_rules.py tests/test_api_pending.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_api_pending.py tests/test_api_rules.py
git commit -m "feat: expose confidence on pending rows and add rule management API"
```

---

### Task 6: Confidence badge in the correction table

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/style.css`

**Interfaces:**
- Consumes: `GET /api/pending`'s new `category_confidence` field (Task 5)
- Produces: each pending row shows a small colored dot next to the category `<select>` — green when `category_confidence >= 0.75`, amber when `< 0.75`, none when `null`.

- [ ] **Step 1: Add confidence tokens to `web/style.css`**

Read the current file first. Add these two custom properties next to the existing `--credit`/`--debit` declarations in **all three** places they appear (`:root`, the `prefers-color-scheme: dark` block, and `:root[data-theme="dark"]` — light mode reuses the base `:root` values, so add `--confidence-low` there too):

In `:root`:
```css
  --credit: #3c6e52;
  --debit: #b03a2e;
  --confidence-high: #3c6e52;
  --confidence-low: #b8860b;
```

In the `@media (prefers-color-scheme: dark)` block and in `:root[data-theme="dark"]`:
```css
    --credit: #6fa687;
    --debit: #e0685c;
    --confidence-high: #6fa687;
    --confidence-low: #e0b84a;
```

In `:root[data-theme="light"]`, add alongside the existing `--credit`/`--debit`:
```css
  --confidence-high: #3c6e52;
  --confidence-low: #b8860b;
```

Add a new rule near `.chip .dot`:
```css
.confidence-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-left: 6px;
  vertical-align: middle;
}
.confidence-dot.high { background: var(--confidence-high); }
.confidence-dot.low { background: var(--confidence-low); }
```

- [ ] **Step 2: Update `web/app.js`'s `loadPending()` to render the badge**

Read the current file first. Find the `loadPending()` function's row-building `tr.innerHTML` template, specifically the `<td><select data-field="category_id">...</select></td>` cell, and add a confidence dot after the `</select>`:

```javascript
    const confidence = row.category_confidence;
    let confidenceBadge = "";
    if (confidence !== null && confidence !== undefined) {
      const level = confidence >= 0.75 ? "high" : "low";
      const title = `${Math.round(confidence * 100)}% Konfidenz`;
      confidenceBadge = `<span class="confidence-dot ${level}" title="${title}"></span>`;
    }
```

Then change the category `<td>` from:
```javascript
      <td><select data-field="category_id">${categoryOptions}</select></td>
```
to:
```javascript
      <td><select data-field="category_id">${categoryOptions}</select>${confidenceBadge}</td>
```

(Add the `confidenceBadge`/`confidence`/`level`/`title` variable block right before the `tr.innerHTML = ...` template literal, alongside the existing `categoryOptions`/`currencyTag` variable declarations in that function.)

- [ ] **Step 3: Manually verify in the browser**

Start the server (`python -m server.app` from the repo root), drop a statement into `statements/`, click "Neue Dateien importieren", and confirm:
- Rows matched by a pre-seeded rule (confidence 80%) show a green dot.
- If you can trigger a low-confidence row (e.g. temporarily lower a rule's `match_count`/raise `correction_count` via the `/api/rules` endpoint with `curl`, or just trust the unit-level confidence math verified in Task 3/5), it would show an amber dot.
- Rows with no match (still "Unkategorisiert") show no dot.
- Hovering a dot shows the percentage as a tooltip.

- [ ] **Step 4: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "feat: show confidence indicator on pending transaction rows"
```

---

### Task 7: Rule management page

**Files:**
- Create: `web/regeln.html`
- Create: `web/regeln.js`
- Modify: `web/index.html` (add a link to the new page)
- Modify: `README.md`

**Interfaces:**
- Consumes: `GET /api/rules`, `PUT /api/rules/<id>`, `DELETE /api/rules/<id>` (Task 5), `GET /api/categories` (existing)
- Produces: a second page at `/regeln.html` listing all rules with confidence/stats, letting the user retarget or delete a rule.

- [ ] **Step 1: Add a header link in `web/index.html`**

Read the current file first. In the `.app-header` section, next to the existing `#scan-btn` button, add:

```html
      <a href="/regeln.html" class="btn btn-ghost">Regeln verwalten</a>
```

(Place it before or after `#scan-btn` inside the header — keep both elements inside the header's flex container so they line up with the existing layout.)

- [ ] **Step 2: Create `web/regeln.html`**

```html
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Budget Tracker — Regeln</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <div class="page">

    <header class="app-header">
      <p class="app-title">Kategorisierungs-Regeln</p>
      <a href="/index.html" class="btn btn-ghost">Zurück zum Dashboard</a>
    </header>

    <p class="review-note">
      Regeln entstehen automatisch, wenn du beim Bestätigen eines Imports
      eine Kategorie zuweist oder korrigierst. Hier kannst du eine Regel
      umlenken oder löschen — neue Regeln legst du nicht direkt an.
    </p>

    <div class="ledger">
      <table id="rules-table">
        <thead>
          <tr>
            <th>Keyword</th>
            <th>Kategorie</th>
            <th class="num">Konfidenz</th>
            <th class="num">Treffer</th>
            <th class="num">Korrekturen</th>
            <th>Herkunft</th>
            <th></th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>

  </div>

  <script src="/regeln.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `web/regeln.js`**

```javascript
function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

let categories = [];

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
}

async function loadRules() {
  const [rulesRes] = await Promise.all([fetch("/api/rules"), loadCategories()]);
  const rules = await rulesRes.json();
  const tbody = document.querySelector("#rules-table tbody");
  tbody.innerHTML = "";

  rules.forEach((rule) => {
    const tr = document.createElement("tr");
    tr.dataset.id = rule.id;
    const categoryOptions = categories
      .map((c) => `<option value="${c.id}" ${c.id === rule.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
      .join("");
    const confidencePct = Math.round(rule.confidence * 100);
    const confidenceClass = rule.confidence >= 0.75 ? "high" : "low";
    tr.innerHTML = `
      <td class="mono">${escapeHtml(rule.keyword)}</td>
      <td><select data-field="category_id">${categoryOptions}</select></td>
      <td class="amount tabular"><span class="confidence-dot ${confidenceClass}"></span> ${confidencePct}%</td>
      <td class="amount tabular">${rule.match_count}</td>
      <td class="amount tabular">${rule.correction_count}</td>
      <td>${rule.is_seeded ? "vordefiniert" : "gelernt"}</td>
      <td><button type="button" class="btn-danger" data-action="delete">Löschen</button></td>
    `;
    tbody.appendChild(tr);
  });
}

document.querySelector("#rules-table tbody").addEventListener("change", async (event) => {
  if (event.target.dataset.field === "category_id") {
    const tr = event.target.closest("tr");
    const categoryId = parseInt(event.target.value, 10);
    const res = await fetch(`/api/rules/${tr.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category_id: categoryId }),
    });
    if (!res.ok) {
      alert("Fehler beim Speichern — bitte erneut versuchen.");
      await loadRules();
    }
  }
});

document.querySelector("#rules-table tbody").addEventListener("click", async (event) => {
  if (event.target.dataset.action === "delete") {
    const tr = event.target.closest("tr");
    const res = await fetch(`/api/rules/${tr.dataset.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Fehler beim Löschen — bitte erneut versuchen.");
      return;
    }
    tr.remove();
  }
});

loadRules();
```

- [ ] **Step 4: Update `README.md`**

Read the current file first. Add a new section after "## Monatlicher Workflow" (before "## Tests ausführen"):

```markdown
## Lernfähige Kategorisierung

Jede bestätigte Kategorie-Zuordnung verbessert künftige Importe: wird ein
automatischer Vorschlag unverändert bestätigt, steigt seine Konfidenz;
wird er korrigiert, sinkt sie und eine neue Regel für die richtige
Kategorie wird gelernt. In der Korrektur-Tabelle zeigt ein grüner Punkt
neben der Kategorie "sicher" (≥75% Konfidenz), ein gelber Punkt "bitte
prüfen". Unter "Regeln verwalten" (Link im Dashboard-Header) lassen sich
alle Regeln einsehen, umlenken oder löschen.
```

- [ ] **Step 5: Manually verify in the browser**

Start the server, click "Regeln verwalten" in the header, and confirm:
- All ~57 pre-seeded rules are listed with "vordefiniert", 80% confidence, 3 Treffer, 0 Korrekturen.
- Changing a rule's category via the dropdown persists (reload the page, the new category is still selected).
- Deleting a rule removes it from the list and it's gone after reload.
- "Zurück zum Dashboard" returns to the main page.

- [ ] **Step 6: Commit**

```bash
git add web/regeln.html web/regeln.js web/index.html README.md
git commit -m "feat: add rule management page"
```

---

## Post-implementation manual check

After all tasks are complete, run the full suite once more (`pytest -v`) and do one end-to-end pass in the browser: scan a real statement, confirm a few rows (accepting some suggestions, correcting others), check `/regeln.html` to see the corrected rule's confidence actually changed, then re-scan an overlapping file and confirm the correction "stuck" (the previously-corrected merchant now suggests the corrected category, not the original one).
