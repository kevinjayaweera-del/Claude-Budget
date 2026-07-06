# Budget Tracker MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, private budget-tracker web app that imports bank statements (CSV/PDF) from a folder, lets the user review/correct parsed transactions, auto-categorizes them, and shows them in an interactive dashboard with date-range and category/text filters.

**Architecture:** Python + Flask backend serving a JSON API and static frontend files; SQLite for storage (file-based, no server); pdfplumber for PDF text/table extraction; pandas for aggregation; plain HTML/CSS/JS frontend using Chart.js (via CDN) for charts. No build step, no frontend framework.

**Tech Stack:** Python 3.11+, Flask, sqlite3 (stdlib), pdfplumber, pandas, Chart.js (CDN), pytest + reportlab (test-only, to generate PDF fixtures).

## Global Constraints

- Runs entirely locally — no cloud upload, no external API calls with financial data.
- Amounts are stored as **integer cents** (`amount_cents`), never floats, to avoid rounding errors.
- Dates are stored and exchanged as ISO strings: `YYYY-MM-DD`.
- Multi-currency: each transaction carries its own `currency` (e.g. `CHF`, `EUR`); no live exchange-rate conversion (explicit non-goal per spec).
- Default categories (from spec, seeded on first run): `Lebensmittel`, `Miete/Wohnen`, `Freizeit`, `Transport`, `Versicherungen`, `Gesundheit`, `Shopping`, `Abos`, `Sonstiges`, `Unkategorisiert`.
- Duplicate-import protection is by file content hash (SHA-256), not filename.
- Commands in this plan assume a bash-compatible shell (Git Bash on Windows). Adjust venv activation to `source venv/Scripts/activate` on Windows Git Bash, or `venv\Scripts\Activate.ps1` on PowerShell.
- Out of scope for this plan (per spec): exchange-rate conversion, exports, year-over-year comparisons, budgets/savings goals, folder auto-watching, multi-user/auth.

---

### Task 1: Project Scaffolding & Database Schema

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.gitignore`
- Create: `server/__init__.py`
- Create: `server/db.py`
- Create: `tests/__init__.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces: `db.init_db(db_path: str | Path) -> sqlite3.Connection`, `db.get_connection(db_path: str | Path) -> sqlite3.Connection`, `db.DEFAULT_CATEGORIES: list[str]`

- [ ] **Step 1: Verify/install Python 3.11+**

Run: `python --version`
Expected: `Python 3.11` or higher. If Python is missing or older, install it first:

```bash
winget install --id Python.Python.3.12 -e --source winget
```

Then reopen the shell and re-run `python --version` to confirm it succeeds.

- [ ] **Step 2: Write dependency files**

`requirements.txt`:
```
Flask==3.0.3
pdfplumber==0.11.4
pandas==2.2.3
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.3.3
reportlab==4.2.5
```

`.gitignore`:
```
venv/
__pycache__/
*.pyc
data/*.db
statements/*
!statements/.gitkeep
```

- [ ] **Step 3: Create virtual environment and install dependencies**

```bash
python -m venv venv
source venv/Scripts/activate
pip install -r requirements-dev.txt
```

Expected: install completes with no errors.

- [ ] **Step 4: Write the failing test**

`tests/test_db.py`:
```python
import sqlite3

from server.db import init_db, DEFAULT_CATEGORIES


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
```

`tests/__init__.py`: empty file.
`server/__init__.py`: empty file.

- [ ] **Step 5: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.db'`

- [ ] **Step 6: Implement `server/db.py`**

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

DEFAULT_CATEGORIES = [
    "Lebensmittel", "Miete/Wohnen", "Freizeit", "Transport",
    "Versicherungen", "Gesundheit", "Shopping", "Abos", "Sonstiges",
    "Unkategorisiert",
]


def get_connection(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    for name in DEFAULT_CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    return conn
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: 3 passed

- [ ] **Step 8: Commit**

```bash
git add requirements.txt requirements-dev.txt .gitignore server/__init__.py server/db.py tests/__init__.py tests/test_db.py
git commit -m "feat: add project scaffolding and SQLite schema"
```

---

### Task 2: File Hashing for Duplicate-Import Protection

**Files:**
- Create: `server/file_hash.py`
- Create: `tests/test_file_hash.py`

**Interfaces:**
- Produces: `file_hash.hash_file(path: str | Path) -> str` (hex SHA-256 digest)

- [ ] **Step 1: Write the failing test**

`tests/test_file_hash.py`:
```python
import hashlib

from server.file_hash import hash_file


def test_hash_file_matches_known_sha256(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello budget tracker")

    expected = hashlib.sha256(b"hello budget tracker").hexdigest()
    assert hash_file(file_path) == expected


def test_hash_file_differs_for_different_content(tmp_path):
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    file_a.write_bytes(b"content a")
    file_b.write_bytes(b"content b")

    assert hash_file(file_a) != hash_file(file_b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_file_hash.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.file_hash'`

- [ ] **Step 3: Implement `server/file_hash.py`**

```python
import hashlib


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_file_hash.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add server/file_hash.py tests/test_file_hash.py
git commit -m "feat: add file hashing for duplicate-import protection"
```

---

### Task 3: CSV Parser with Column Auto-Detection

**Files:**
- Create: `server/parsers/__init__.py`
- Create: `server/parsers/csv_parser.py`
- Create: `tests/test_csv_parser.py`

**Interfaces:**
- Produces: `csv_parser.parse_csv(path: str | Path) -> list[dict]`, where each dict has keys `date` (`YYYY-MM-DD` str), `description` (str), `amount_cents` (int), `currency` (str). Raises `csv_parser.CsvParseError` if required columns can't be identified.

- [ ] **Step 1: Write the failing test**

`tests/test_csv_parser.py`:
```python
import pytest

from server.parsers.csv_parser import parse_csv, CsvParseError


def test_parse_csv_semicolon_delimited(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "03.03.2026;Lohn März;5200.00;CHF\n",
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zürich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Lohn März", "amount_cents": 520000, "currency": "CHF"},
    ]


def test_parse_csv_comma_delimited_english_headers(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Date,Description,Amount\n"
        "2026-03-01,Coffee Shop,-4.50\n",
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Coffee Shop", "amount_cents": -450, "currency": "CHF"},
    ]


def test_parse_csv_raises_when_columns_unrecognized(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text("Foo,Bar\n1,2\n", encoding="utf-8-sig")

    with pytest.raises(CsvParseError):
        parse_csv(csv_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_csv_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.parsers'`

- [ ] **Step 3: Implement `server/parsers/csv_parser.py`**

`server/parsers/__init__.py`: empty file.

`server/parsers/csv_parser.py`:
```python
import csv
from datetime import datetime

DATE_COLUMNS = ["datum", "buchungsdatum", "date", "buchungstag"]
AMOUNT_COLUMNS = ["betrag", "amount", "umsatz"]
DESCRIPTION_COLUMNS = ["text", "verwendungszweck", "beschreibung", "buchungstext", "description"]
CURRENCY_COLUMNS = ["währung", "waehrung", "currency"]

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]


class CsvParseError(Exception):
    pass


def _find_column(fieldnames, candidates):
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _parse_date(value):
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise CsvParseError(f"Unbekanntes Datumsformat: {value}")


def _parse_amount_cents(value):
    value = value.strip().replace("'", "").replace(" ", "")
    if value.count(",") == 1 and value.count(".") == 0:
        value = value.replace(",", ".")
    else:
        value = value.replace(",", "")
    try:
        return round(float(value) * 100)
    except ValueError:
        raise CsvParseError(f"Unbekanntes Betragsformat: {value}")


def parse_csv(file_path):
    with open(file_path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise CsvParseError("Leere oder unlesbare CSV-Datei")

        date_col = _find_column(reader.fieldnames, DATE_COLUMNS)
        amount_col = _find_column(reader.fieldnames, AMOUNT_COLUMNS)
        desc_col = _find_column(reader.fieldnames, DESCRIPTION_COLUMNS)
        currency_col = _find_column(reader.fieldnames, CURRENCY_COLUMNS)

        if not date_col or not amount_col or not desc_col:
            raise CsvParseError(
                f"Konnte Spalten nicht erkennen. Gefunden: {reader.fieldnames}"
            )

        rows = []
        for row in reader:
            rows.append({
                "date": _parse_date(row[date_col]),
                "description": row[desc_col].strip(),
                "amount_cents": _parse_amount_cents(row[amount_col]),
                "currency": (row.get(currency_col) or "").strip() if currency_col else "" ,
            })
        for row in rows:
            if not row["currency"]:
                row["currency"] = "CHF"
        return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_csv_parser.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add server/parsers/__init__.py server/parsers/csv_parser.py tests/test_csv_parser.py
git commit -m "feat: add CSV parser with column auto-detection"
```

---

### Task 4: PDF Parser (Heuristic Line Matching)

**Files:**
- Create: `server/parsers/pdf_parser.py`
- Create: `tests/test_pdf_parser.py`

**Interfaces:**
- Consumes: none from earlier tasks
- Produces: `pdf_parser.parse_pdf(path: str | Path) -> list[dict]` (same row shape as `csv_parser.parse_csv`); `pdf_parser._parse_line(line: str) -> dict | None` (internal, unit-tested directly)

- [ ] **Step 1: Write the failing test**

`tests/test_pdf_parser.py`:
```python
from reportlab.pdfgen import canvas

from server.parsers.pdf_parser import parse_pdf, _parse_line


def test_parse_line_extracts_date_description_amount():
    result = _parse_line("01.03.2026 Migros Zürich -45.90")
    assert result == {
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
    }


def test_parse_line_returns_none_when_no_date():
    assert _parse_line("Kontostand am Monatsende") is None


def test_parse_line_returns_none_when_no_amount():
    assert _parse_line("01.03.2026 Migros Zürich") is None


def test_parse_pdf_extracts_transactions_from_real_pdf(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(50, 800, "Kontoauszug März 2026")
    c.drawString(50, 780, "01.03.2026 Migros Zuerich -45.90")
    c.drawString(50, 760, "03.03.2026 Lohn Maerz 5200.00")
    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Lohn Maerz", "amount_cents": 520000, "currency": "CHF"},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pdf_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.parsers.pdf_parser'`

- [ ] **Step 3: Implement `server/parsers/pdf_parser.py`**

```python
import re

import pdfplumber

DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
AMOUNT_RE = re.compile(r"([+-]?\d{1,3}(?:['’]?\d{3})*[.,]\d{2})\s*$")


def _to_iso_date(value):
    day, month, year = value.split(".")
    return f"{year}-{month}-{day}"


def _amount_to_cents(value):
    normalized = value.replace("’", "").replace("'", "").replace(" ", "")
    normalized = normalized.replace(",", ".")
    return round(float(normalized) * 100)


def _parse_line(line):
    date_match = DATE_RE.search(line)
    amount_match = AMOUNT_RE.search(line)
    if not date_match or not amount_match:
        return None
    if amount_match.start() < date_match.end():
        return None

    description = line[date_match.end():amount_match.start()].strip(" -\t")
    if not description:
        return None

    return {
        "date": _to_iso_date(date_match.group(1)),
        "description": description,
        "amount_cents": _amount_to_cents(amount_match.group(1)),
        "currency": "CHF",
    }


def parse_pdf(file_path):
    rows = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                parsed = _parse_line(line)
                if parsed:
                    rows.append(parsed)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pdf_parser.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/parsers/pdf_parser.py tests/test_pdf_parser.py
git commit -m "feat: add heuristic PDF statement parser"
```

---

### Task 5: Categorization Engine (Keyword Rules + Learning)

**Files:**
- Create: `server/categorize.py`
- Create: `tests/test_categorize.py`

**Interfaces:**
- Consumes: `db.init_db` (Task 1) for test setup
- Produces: `categorize.categorize(description: str, conn: sqlite3.Connection) -> int` (category id), `categorize.learn_rule(conn: sqlite3.Connection, description: str, category_id: int) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_categorize.py`:
```python
from server.db import init_db
from server.categorize import categorize, learn_rule


def _category_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def test_categorize_falls_back_to_uncategorized(tmp_path):
    conn = init_db(tmp_path / "test.db")
    result = categorize("Unbekannte Buchung XYZ", conn)
    assert result == _category_id(conn, "Unkategorisiert")


def test_categorize_matches_existing_rule(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id) VALUES (?, ?)",
        ("migros", lebensmittel_id),
    )
    conn.commit()

    result = categorize("MIGROS Zuerich Filiale 12", conn)
    assert result == lebensmittel_id


def test_learn_rule_stores_keyword_and_is_reused(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")

    learn_rule(conn, "Starbucks Bahnhof", freizeit_id)
    result = categorize("Starbucks Hauptbahnhof", conn)

    assert result == freizeit_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_categorize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.categorize'`

- [ ] **Step 3: Implement `server/categorize.py`**

```python
def categorize(description, conn):
    text = description.lower()
    rules = conn.execute("SELECT keyword, category_id FROM category_rules").fetchall()
    for rule in rules:
        if rule["keyword"] in text:
            return rule["category_id"]
    return _get_category_id(conn, "Unkategorisiert")


def learn_rule(conn, description, category_id):
    keyword = _extract_keyword(description)
    conn.execute(
        "INSERT OR REPLACE INTO category_rules (keyword, category_id) VALUES (?, ?)",
        (keyword, category_id),
    )
    conn.commit()


def _get_category_id(conn, name):
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def _extract_keyword(description):
    words = [w for w in description.split() if len(w) > 3]
    candidate = words[0] if words else description.strip()
    return candidate.lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_categorize.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add server/categorize.py tests/test_categorize.py
git commit -m "feat: add keyword-based categorization with rule learning"
```

---

### Task 6: Import Orchestration Service

**Files:**
- Create: `server/import_service.py`
- Create: `tests/test_import_service.py`

**Interfaces:**
- Consumes: `db.init_db` (Task 1), `file_hash.hash_file` (Task 2), `csv_parser.parse_csv` (Task 3), `pdf_parser.parse_pdf` (Task 4), `categorize.categorize` (Task 5)
- Produces: `import_service.scan_and_parse(conn: sqlite3.Connection, statements_dir: str | Path) -> int` (count of newly-created pending rows)

- [ ] **Step 1: Write the failing test**

`tests/test_import_service.py`:
```python
from server.db import init_db
from server.import_service import scan_and_parse


def test_scan_and_parse_creates_pending_rows_and_marks_file_imported(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    created = scan_and_parse(conn, statements_dir)

    assert created == 1
    pending = conn.execute("SELECT * FROM pending_transactions").fetchall()
    assert len(pending) == 1
    assert pending[0]["description"] == "Migros Zürich"
    assert pending[0]["amount_cents"] == -4590

    files = conn.execute("SELECT * FROM imported_files").fetchall()
    assert len(files) == 1


def test_scan_and_parse_skips_already_imported_files(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)
    second_run_created = scan_and_parse(conn, statements_dir)

    assert second_run_created == 0
    files = conn.execute("SELECT * FROM imported_files").fetchall()
    assert len(files) == 1


def test_scan_and_parse_uses_subfolder_name_as_source(tmp_path):
    statements_dir = tmp_path / "statements"
    (statements_dir / "ZKB").mkdir(parents=True)
    (statements_dir / "ZKB" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)

    pending = conn.execute("SELECT * FROM pending_transactions").fetchone()
    assert pending["source"] == "ZKB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.import_service'`

- [ ] **Step 3: Implement `server/import_service.py`**

```python
from pathlib import Path

from server.file_hash import hash_file
from server.parsers.csv_parser import parse_csv
from server.parsers.pdf_parser import parse_pdf
from server.categorize import categorize


def scan_and_parse(conn, statements_dir):
    statements_dir = Path(statements_dir)
    created = 0

    for path in sorted(statements_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".csv", ".pdf"):
            continue

        file_hash = hash_file(path)
        existing = conn.execute(
            "SELECT id FROM imported_files WHERE hash = ?", (file_hash,)
        ).fetchone()
        if existing:
            continue

        rows = parse_csv(path) if path.suffix.lower() == ".csv" else parse_pdf(path)

        source = (
            path.parent.name
            if path.parent.resolve() != statements_dir.resolve()
            else path.stem
        )

        cursor = conn.execute(
            "INSERT INTO imported_files (hash, filename, source, imported_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (file_hash, path.name, source),
        )
        file_id = cursor.lastrowid

        for row in rows:
            category_id = categorize(row["description"], conn)
            conn.execute(
                "INSERT INTO pending_transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"],
                 row["currency"], category_id, source, file_id),
            )
            created += 1

    conn.commit()
    return created
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_import_service.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add server/import_service.py tests/test_import_service.py
git commit -m "feat: add import orchestration with dedup and auto-categorization"
```

---

### Task 7: Flask API — Scan, Pending Review, Import Confirmation

**Files:**
- Create: `server/app.py`
- Create: `web/index.html` (minimal placeholder, extended in Task 9)
- Create: `tests/test_api_pending.py`

**Interfaces:**
- Consumes: `db.init_db`/`db.get_connection` (Task 1), `import_service.scan_and_parse` (Task 6), `categorize.learn_rule` (Task 5)
- Produces: `app.create_app(db_path=None, statements_dir=None) -> Flask app`; routes `POST /api/scan`, `GET /api/pending`, `PUT /api/pending/<id>`, `DELETE /api/pending/<id>`, `POST /api/import/confirm`, `GET /api/categories`

- [ ] **Step 1: Write the failing test**

`tests/test_api_pending.py`:
```python
import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db",
        statements_dir=tmp_path / "statements",
    )
    app.config["TESTING"] = True
    return app.test_client()


def _write_sample_csv(tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )


def test_scan_endpoint_returns_new_pending_count(client, tmp_path):
    _write_sample_csv(tmp_path)

    response = client.post("/api/scan")

    assert response.status_code == 200
    assert response.get_json() == {"new_pending": 1}


def test_pending_list_reflects_scanned_rows(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    rows = client.get("/api/pending").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Migros Zürich"
    assert rows[0]["amount_cents"] == -4590


def test_update_pending_row_changes_category(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put(f"/api/pending/{pending_id}", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        "category_id": categories["Lebensmittel"],
    })

    assert response.status_code == 200
    updated = client.get("/api/pending").get_json()[0]
    assert updated["category_id"] == categories["Lebensmittel"]


def test_delete_pending_row_removes_it(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.delete(f"/api/pending/{pending_id}")

    assert response.status_code == 200
    assert client.get("/api/pending").get_json() == []


def test_confirm_import_moves_rows_to_transactions_and_clears_pending(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    response = client.post("/api/import/confirm")

    assert response.get_json() == {"imported": 1}
    assert client.get("/api/pending").get_json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_pending.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app'`

- [ ] **Step 3: Create minimal placeholder frontend and implement `server/app.py`**

`web/index.html`:
```html
<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><title>Budget Tracker</title></head>
<body><p>Platzhalter – wird in Task 9 ersetzt.</p></body>
</html>
```

`server/app.py`:
```python
from pathlib import Path

from flask import Flask, current_app, jsonify, request, send_from_directory

from server.db import get_connection, init_db
from server.import_service import scan_and_parse
from server.categorize import learn_rule

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"


def get_db():
    return get_connection(current_app.config["DB_PATH"])


def create_app(db_path=None, statements_dir=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = Path(db_path) if db_path else BASE_DIR / "data" / "budget.db"
    app.config["STATEMENTS_DIR"] = Path(statements_dir) if statements_dir else BASE_DIR / "statements"
    app.config["STATEMENTS_DIR"].mkdir(parents=True, exist_ok=True)
    init_db(app.config["DB_PATH"]).close()

    register_routes(app)
    return app


def register_routes(app):
    @app.route("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(WEB_DIR, filename)

    @app.route("/api/scan", methods=["POST"])
    def scan():
        conn = get_db()
        count = scan_and_parse(conn, current_app.config["STATEMENTS_DIR"])
        conn.close()
        return jsonify({"new_pending": count})

    @app.route("/api/pending", methods=["GET"])
    def list_pending():
        conn = get_db()
        rows = conn.execute(
            "SELECT p.id, p.date, p.description, p.amount_cents, p.currency, "
            "p.category_id, c.name as category_name, p.source "
            "FROM pending_transactions p LEFT JOIN categories c ON p.category_id = c.id "
            "ORDER BY p.date"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/pending/<int:pending_id>", methods=["PUT"])
    def update_pending(pending_id):
        data = request.get_json()
        conn = get_db()
        conn.execute(
            "UPDATE pending_transactions SET date=?, description=?, amount_cents=?, "
            "currency=?, category_id=? WHERE id=?",
            (data["date"], data["description"], data["amount_cents"],
             data["currency"], data["category_id"], pending_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/pending/<int:pending_id>", methods=["DELETE"])
    def delete_pending(pending_id):
        conn = get_db()
        conn.execute("DELETE FROM pending_transactions WHERE id=?", (pending_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/import/confirm", methods=["POST"])
    def confirm_import():
        conn = get_db()
        rows = conn.execute("SELECT * FROM pending_transactions").fetchall()
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
        conn.execute("DELETE FROM pending_transactions")
        conn.commit()
        imported = len(rows)
        conn.close()
        return jsonify({"imported": imported})

    @app.route("/api/categories", methods=["GET"])
    def list_categories():
        conn = get_db()
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_pending.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/app.py web/index.html tests/test_api_pending.py
git commit -m "feat: add Flask API for scanning, pending review, and import confirmation"
```

---

### Task 8: Flask API — Filtered Transactions & Summary Aggregation

**Files:**
- Modify: `server/app.py` (add two routes inside `register_routes`)
- Create: `tests/test_api_transactions.py`

**Interfaces:**
- Consumes: `server.app.create_app` (Task 7)
- Produces: routes `GET /api/transactions` and `GET /api/summary`, both accepting query params `start`, `end`, `category_id`, `source`, `q`, `min_amount`, `max_amount` (amounts in whole-currency units, e.g. `min_amount=10.50`) via a shared `_fetch_filtered_transactions(conn, args)` helper; `GET /api/sources` (distinct source list for the frontend dropdown). `/api/summary` response shape: `{total_income, total_expense, by_category: [{category, amount_cents}], by_month: [{month, amount_cents}]}`

- [ ] **Step 1: Write the failing test**

`tests/test_api_transactions.py`:
```python
import pytest

from server.app import create_app


@pytest.fixture
def client_with_data(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()

    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "05.04.2026;Lohn April;5200.00;CHF\n"
        "10.04.2026;Coop Zürich;-30.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    return client


def test_transactions_filtered_by_date_range(client_with_data):
    rows = client_with_data.get("/api/transactions?start=2026-04-01&end=2026-04-30").get_json()

    dates = {r["date"] for r in rows}
    assert dates == {"2026-04-05", "2026-04-10"}


def test_transactions_filtered_by_text_search(client_with_data):
    rows = client_with_data.get("/api/transactions?q=Migros").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Migros Zürich"


def test_transactions_filtered_by_amount_range(client_with_data):
    rows = client_with_data.get("/api/transactions?min_amount=-40&max_amount=-1").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Coop Zürich"


def test_transactions_filtered_by_source(client_with_data):
    rows = client_with_data.get("/api/transactions?source=test").get_json()

    assert len(rows) == 3
    assert all(r["source"] == "test" for r in rows)


def test_sources_endpoint_lists_distinct_sources(client_with_data):
    sources = client_with_data.get("/api/sources").get_json()

    assert sources == ["test"]


def test_summary_totals_and_by_month(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    assert summary["total_income"] == 520000
    assert summary["total_expense"] == -7590
    months = {m["month"] for m in summary["by_month"]}
    assert months == {"2026-03", "2026-04"}


def test_summary_by_category_groups_expenses(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    unkategorisiert = next(
        (c for c in summary["by_category"] if c["category"] == "Unkategorisiert"), None
    )
    assert unkategorisiert is not None
    assert unkategorisiert["amount_cents"] == 7590


def test_summary_respects_category_filter(client_with_data):
    # None of the seeded transactions matches any category_rules, so they all
    # land in "Unkategorisiert". Filtering by an unrelated category must
    # therefore return the empty-result shape, proving category_id narrows
    # the summary (previously only start/end did).
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lebensmittel_id = categories["Lebensmittel"]

    summary = client_with_data.get(f"/api/summary?category_id={lebensmittel_id}").get_json()

    assert summary == {"total_income": 0, "total_expense": 0, "by_category": [], "by_month": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_transactions.py -v`
Expected: FAIL with 404 (route not found) — assertion errors on missing keys/status codes

- [ ] **Step 3: Add routes to `server/app.py`**

Add `import pandas as pd` to the top of the file. Add this module-level helper function (outside `register_routes`, e.g. below `get_db`):

```python
def _fetch_filtered_transactions(conn, args):
    query = (
        "SELECT t.id, t.date, t.description, t.amount_cents, t.currency, "
        "t.category_id, c.name as category_name, t.source "
        "FROM transactions t LEFT JOIN categories c ON t.category_id = c.id WHERE 1=1"
    )
    params = []
    if args.get("start"):
        query += " AND t.date >= ?"
        params.append(args["start"])
    if args.get("end"):
        query += " AND t.date <= ?"
        params.append(args["end"])
    if args.get("category_id"):
        query += " AND t.category_id = ?"
        params.append(args["category_id"])
    if args.get("source"):
        query += " AND t.source = ?"
        params.append(args["source"])
    if args.get("q"):
        query += " AND t.description LIKE ?"
        params.append(f"%{args['q']}%")
    if args.get("min_amount"):
        query += " AND t.amount_cents >= ?"
        params.append(round(float(args["min_amount"]) * 100))
    if args.get("max_amount"):
        query += " AND t.amount_cents <= ?"
        params.append(round(float(args["max_amount"]) * 100))
    query += " ORDER BY t.date DESC"
    return conn.execute(query, params).fetchall()
```

Add these three routes inside `register_routes(app)`, alongside the existing ones:

```python
    @app.route("/api/transactions", methods=["GET"])
    def list_transactions():
        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/sources", methods=["GET"])
    def list_sources():
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT source FROM transactions ORDER BY source"
        ).fetchall()
        conn.close()
        return jsonify([r["source"] for r in rows])

    @app.route("/api/summary", methods=["GET"])
    def summary():
        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        categories = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
        conn.close()

        if not rows:
            return jsonify({"total_income": 0, "total_expense": 0, "by_category": [], "by_month": []})

        df = pd.DataFrame([dict(r) for r in rows])
        total_income = int(df[df.amount_cents > 0]["amount_cents"].sum())
        total_expense = int(df[df.amount_cents < 0]["amount_cents"].sum())

        expenses = df[df.amount_cents < 0].copy()
        by_category_list = []
        if not expenses.empty:
            grouped = expenses.groupby("category_id")["amount_cents"].sum().abs().reset_index()
            by_category_list = [
                {"category": categories.get(row.category_id, "Unbekannt"), "amount_cents": int(row.amount_cents)}
                for row in grouped.itertuples()
            ]

        df["month"] = df["date"].str.slice(0, 7)
        by_month = df.groupby("month")["amount_cents"].sum().reset_index()
        by_month_list = [
            {"month": row.month, "amount_cents": int(row.amount_cents)}
            for row in by_month.itertuples()
        ]

        return jsonify({
            "total_income": total_income,
            "total_expense": total_expense,
            "by_category": by_category_list,
            "by_month": by_month_list,
        })
```

Note: `_fetch_filtered_transactions` selects `t.id, t.category_name` etc. alongside the columns `summary()` needs (`date`, `amount_cents`, `category_id`) — the extra columns are simply unused by the pandas aggregation, which keeps both routes sharing one filter implementation (DRY) instead of duplicating the `WHERE` clause.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_transactions.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests across all previous tasks still pass

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_api_transactions.py
git commit -m "feat: add filtered transactions and summary aggregation API"
```

---

### Task 9: Frontend Shell — Layout, Styling, Data Loading

**Files:**
- Modify: `web/index.html` (replace placeholder)
- Create: `web/style.css`
- Create: `web/app.js`

**Interfaces:**
- Consumes: `GET /api/categories`, `GET /api/sources`, `GET /api/transactions`, `GET /api/summary` (Task 7/8)
- Produces: page renders a filter bar (date range, category, source, amount range, text search), summary cards, and a transactions table populated from the API. Chart rendering is added in Task 10; pending-review UI is added in Task 12.

- [ ] **Step 1: Replace `web/index.html`**

```html
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Budget Tracker</title>
  <link rel="stylesheet" href="/style.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <header>
    <h1>Budget Tracker</h1>
    <button id="scan-btn">Neue Dateien importieren</button>
  </header>

  <section id="pending-section" class="hidden">
    <h2>Zu prüfende Buchungen</h2>
    <table id="pending-table">
      <thead>
        <tr><th>Datum</th><th>Beschreibung</th><th>Betrag</th><th>Währung</th><th>Kategorie</th><th></th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <button id="confirm-btn">Import bestätigen</button>
  </section>

  <section id="filters">
    <label>Von <input type="date" id="filter-start"></label>
    <label>Bis <input type="date" id="filter-end"></label>
    <label>Kategorie
      <select id="filter-category"><option value="">Alle Kategorien</option></select>
    </label>
    <label>Konto/Quelle
      <select id="filter-source"><option value="">Alle Konten</option></select>
    </label>
    <label>Betrag von <input type="number" step="0.01" id="filter-min-amount" placeholder="z.B. -500"></label>
    <label>Betrag bis <input type="number" step="0.01" id="filter-max-amount" placeholder="z.B. 0"></label>
    <label>Suche <input type="text" id="filter-search" placeholder="z.B. Migros"></label>
    <button id="apply-filters-btn">Filtern</button>
  </section>

  <section id="summary-cards">
    <div class="card"><span class="label">Einnahmen</span><span id="total-income" class="value"></span></div>
    <div class="card"><span class="label">Ausgaben</span><span id="total-expense" class="value"></span></div>
    <div class="card"><span class="label">Saldo</span><span id="total-balance" class="value"></span></div>
  </section>

  <section id="charts">
    <canvas id="category-chart"></canvas>
    <canvas id="month-chart"></canvas>
  </section>

  <section id="transactions-section">
    <h2>Buchungen</h2>
    <table id="transactions-table">
      <thead>
        <tr><th>Datum</th><th>Beschreibung</th><th>Kategorie</th><th>Quelle</th><th>Betrag</th></tr>
      </thead>
      <tbody></tbody>
    </table>
  </section>

  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `web/style.css`**

```css
:root {
  --accent: #2f6f4f;
  --bg: #f6f7f5;
  --card-bg: #ffffff;
  --text: #1f2a24;
  --border: #dfe4e0;
}

* { box-sizing: border-box; }

body {
  font-family: "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  margin: 0;
  padding: 0 24px 48px;
}

header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 0;
}

button {
  background: var(--accent);
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

button:hover { opacity: 0.9; }

#filters {
  display: flex;
  gap: 12px;
  align-items: end;
  flex-wrap: wrap;
  background: var(--card-bg);
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--border);
  margin-bottom: 24px;
}

#filters label {
  display: flex;
  flex-direction: column;
  font-size: 12px;
  gap: 4px;
}

#summary-cards {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px 24px;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.card .label { font-size: 12px; color: #6b7a70; }
.card .value { font-size: 24px; font-weight: 600; }

#charts {
  display: flex;
  gap: 24px;
  margin-bottom: 24px;
}

#charts canvas {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  max-height: 320px;
}

table {
  width: 100%;
  border-collapse: collapse;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

th, td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}

.hidden { display: none; }
```

- [ ] **Step 3: Write `web/app.js` (data loading and rendering only — no chart logic yet)**

```javascript
let categories = [];

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
  const select = document.getElementById("filter-category");
  categories.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    select.appendChild(opt);
  });
}

async function loadSources() {
  const res = await fetch("/api/sources");
  const sources = await res.json();
  const select = document.getElementById("filter-source");
  sources.forEach((source) => {
    const opt = document.createElement("option");
    opt.value = source;
    opt.textContent = source;
    select.appendChild(opt);
  });
}

function formatAmount(cents, currency) {
  return (cents / 100).toLocaleString("de-CH", { style: "currency", currency: currency || "CHF" });
}

function buildQuery() {
  const params = new URLSearchParams();
  const start = document.getElementById("filter-start").value;
  const end = document.getElementById("filter-end").value;
  const categoryId = document.getElementById("filter-category").value;
  const source = document.getElementById("filter-source").value;
  const minAmount = document.getElementById("filter-min-amount").value;
  const maxAmount = document.getElementById("filter-max-amount").value;
  const search = document.getElementById("filter-search").value;
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  if (categoryId) params.set("category_id", categoryId);
  if (source) params.set("source", source);
  if (minAmount) params.set("min_amount", minAmount);
  if (maxAmount) params.set("max_amount", maxAmount);
  if (search) params.set("q", search);
  return params.toString();
}

async function loadTransactions() {
  const query = buildQuery();
  const res = await fetch(`/api/transactions?${query}`);
  const rows = await res.json();
  const tbody = document.querySelector("#transactions-table tbody");
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.date}</td>
      <td>${row.description}</td>
      <td>${row.category_name || "Unkategorisiert"}</td>
      <td>${row.source}</td>
      <td>${formatAmount(row.amount_cents, row.currency)}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadSummary() {
  const query = buildQuery();
  const res = await fetch(`/api/summary?${query}`);
  const summary = await res.json();

  document.getElementById("total-income").textContent = formatAmount(summary.total_income, "CHF");
  document.getElementById("total-expense").textContent = formatAmount(summary.total_expense, "CHF");
  document.getElementById("total-balance").textContent = formatAmount(
    summary.total_income + summary.total_expense, "CHF"
  );
}

async function refreshDashboard() {
  await Promise.all([loadTransactions(), loadSummary()]);
}

document.getElementById("apply-filters-btn").addEventListener("click", refreshDashboard);

(async function init() {
  await loadCategories();
  await loadSources();
  await refreshDashboard();
})();
```

- [ ] **Step 4: Manually verify in the browser**

Run (from the repo root, so `server` resolves as a package): `python -m server.app`

Open `http://127.0.0.1:5000/` and confirm:
- Page loads with header, filter bar (including Konto/Quelle dropdown and Betrag von/bis fields), summary cards (empty/zero), and an empty transactions table (no data imported yet).
- Category dropdown is populated with the default categories.
- No errors in the browser console.

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/style.css web/app.js
git commit -m "feat: add dashboard shell with filters and transaction table"
```

---

### Task 10: Frontend Charts (Category Breakdown & Monthly Trend)

**Files:**
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `summary.by_category`, `summary.by_month` from `GET /api/summary` (Task 8)

- [ ] **Step 1: Add chart rendering to `web/app.js`**

Add these two module-level variables near the top (after `let categories = [];`):

```javascript
let categoryChart = null;
let monthChart = null;
```

Replace the body of `loadSummary()` to also render charts — the function becomes:

```javascript
async function loadSummary() {
  const query = buildQuery();
  const res = await fetch(`/api/summary?${query}`);
  const summary = await res.json();

  document.getElementById("total-income").textContent = formatAmount(summary.total_income, "CHF");
  document.getElementById("total-expense").textContent = formatAmount(summary.total_expense, "CHF");
  document.getElementById("total-balance").textContent = formatAmount(
    summary.total_income + summary.total_expense, "CHF"
  );

  const categoryCtx = document.getElementById("category-chart");
  if (categoryChart) categoryChart.destroy();
  categoryChart = new Chart(categoryCtx, {
    type: "doughnut",
    data: {
      labels: summary.by_category.map((c) => c.category),
      datasets: [{ data: summary.by_category.map((c) => c.amount_cents / 100) }],
    },
    options: { plugins: { title: { display: true, text: "Ausgaben nach Kategorie" } } },
  });

  const monthCtx = document.getElementById("month-chart");
  if (monthChart) monthChart.destroy();
  monthChart = new Chart(monthCtx, {
    type: "line",
    data: {
      labels: summary.by_month.map((m) => m.month),
      datasets: [{
        label: "Saldo pro Monat (CHF)",
        data: summary.by_month.map((m) => m.amount_cents / 100),
        borderColor: "#2f6f4f",
        tension: 0.2,
      }],
    },
    options: { plugins: { title: { display: true, text: "Verlauf über Zeit" } } },
  });
}
```

- [ ] **Step 2: Manually verify in the browser**

With the Flask dev server running (`python -m server.app`, from the repo root), place a sample CSV in `statements/` (e.g. the one from Task 8's test data), call `POST /api/scan` and `POST /api/import/confirm` via `curl` or the pending UI once Task 12 lands — for now, verify via:

```bash
curl -X POST http://127.0.0.1:5000/api/scan
curl -X POST http://127.0.0.1:5000/api/import/confirm
```

Reload `http://127.0.0.1:5000/`, click "Filtern", and confirm:
- The category doughnut chart renders with at least one slice.
- The monthly line chart renders with at least one point.
- No errors in the browser console.

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat: add category and monthly trend charts to dashboard"
```

---

### Task 11: Frontend Correction View (Pending Transactions)

**Files:**
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `GET/PUT/DELETE /api/pending`, `POST /api/scan`, `POST /api/import/confirm` (Task 7)

- [ ] **Step 1: Add pending-review logic to `web/app.js`**

Append the following to `web/app.js`:

```javascript
async function loadPending() {
  const res = await fetch("/api/pending");
  const rows = await res.json();
  const section = document.getElementById("pending-section");
  const tbody = document.querySelector("#pending-table tbody");
  tbody.innerHTML = "";

  if (rows.length === 0) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.id = row.id;
    const categoryOptions = categories
      .map((c) => `<option value="${c.id}" ${c.id === row.category_id ? "selected" : ""}>${c.name}</option>`)
      .join("");
    tr.innerHTML = `
      <td><input type="date" value="${row.date}" data-field="date"></td>
      <td><input type="text" value="${row.description}" data-field="description"></td>
      <td><input type="number" step="0.01" value="${(row.amount_cents / 100).toFixed(2)}" data-field="amount"></td>
      <td>${row.currency}</td>
      <td><select data-field="category_id">${categoryOptions}</select></td>
      <td><button type="button" data-action="delete">Löschen</button></td>
    `;
    tbody.appendChild(tr);
  });
}

async function savePendingRow(tr) {
  const id = tr.dataset.id;
  const date = tr.querySelector('[data-field="date"]').value;
  const description = tr.querySelector('[data-field="description"]').value;
  const amount = parseFloat(tr.querySelector('[data-field="amount"]').value);
  const categoryId = parseInt(tr.querySelector('[data-field="category_id"]').value, 10);

  await fetch(`/api/pending/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      date,
      description,
      amount_cents: Math.round(amount * 100),
      currency: "CHF",
      category_id: categoryId,
    }),
  });
}

document.getElementById("scan-btn").addEventListener("click", async () => {
  await fetch("/api/scan", { method: "POST" });
  await loadPending();
});

document.getElementById("confirm-btn").addEventListener("click", async () => {
  const rows = document.querySelectorAll("#pending-table tbody tr");
  for (const tr of rows) {
    await savePendingRow(tr);
  }
  await fetch("/api/import/confirm", { method: "POST" });
  await loadPending();
  await refreshDashboard();
});

document.querySelector("#pending-table tbody").addEventListener("click", async (event) => {
  if (event.target.dataset.action === "delete") {
    const tr = event.target.closest("tr");
    await fetch(`/api/pending/${tr.dataset.id}`, { method: "DELETE" });
    tr.remove();
  }
});
```

Update the `init()` function at the bottom of the file to also load pending rows on page load:

```javascript
(async function init() {
  await loadCategories();
  await loadSources();
  await loadPending();
  await refreshDashboard();
})();
```

- [ ] **Step 2: Manually verify in the browser**

With the Flask dev server running and `statements/` empty of already-imported files:
1. Copy a sample CSV/PDF into `statements/`.
2. Reload the page, click "Neue Dateien importieren" — the "Zu prüfende Buchungen" section appears with the parsed rows.
3. Change a row's category via the dropdown, edit an amount, delete one row.
4. Click "Import bestätigen" — the section disappears, and the transactions table/charts update to include the confirmed rows.
5. Click "Neue Dateien importieren" again — confirm no new pending rows appear for the same file (dedup works).

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat: add pending-transaction correction workflow to dashboard"
```

---

### Task 12: README & Sample Data for First-Run Setup

**Files:**
- Create: `README.md` (replace existing practice-repo content)
- Create: `statements/.gitkeep`
- Create: `data/.gitkeep`

**Interfaces:** none (documentation + folder scaffolding only)

- [ ] **Step 1: Create placeholder folders**

```bash
mkdir -p statements data
touch statements/.gitkeep data/.gitkeep
```

- [ ] **Step 2: Write `README.md`**

```markdown
# Budget Tracker (privat, lokal)

Lokaler Budget-Tracker: Kontoauszüge (PDF/CSV) in `statements/` ablegen,
im Dashboard importieren/prüfen, Ausgaben nach Kategorie und Zeitraum
auswerten. Läuft komplett lokal, keine Cloud-Anbindung.

## Einrichtung

```bash
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash; unter PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Starten

Vom Projekt-Hauptverzeichnis aus (wichtig: `-m server.app`, nicht `python server/app.py`,
damit das `server`-Package korrekt aufgelöst wird):

```bash
python -m server.app
```

Dashboard öffnen: http://127.0.0.1:5000/

## Monatlicher Workflow

1. Neue Kontoauszüge (PDF/CSV) nach `statements/` kopieren (Unterordner pro
   Bank/Konto sind erlaubt, z.B. `statements/ZKB/`).
2. Im Dashboard auf "Neue Dateien importieren" klicken.
3. Erkannte Buchungen in der Korrektur-Tabelle prüfen, Kategorie/Betrag/Datum
   bei Bedarf anpassen oder Fehlzeilen löschen.
4. "Import bestätigen" klicken — Buchungen erscheinen im Dashboard.

## Tests ausführen

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Bekannte Grenzen (MVP)

- PDF-Erkennung ist heuristisch (Datum + Betrag pro Zeile) und nicht für
  jedes Bank-Layout perfekt — die Korrektur-Tabelle fängt Fehler ab.
- Keine Wechselkursumrechnung; Fremdwährungsbeträge werden im Original
  gespeichert.
- Kein Export, keine Jahresvergleiche, keine Budgets/Sparziele (geplante
  spätere Erweiterungen).
```

- [ ] **Step 3: Verify the documented setup works end-to-end**

Run through the README steps in a clean shell (deactivate/reactivate venv) and confirm `pytest -v` passes and the server starts without errors.

- [ ] **Step 4: Commit**

```bash
git add README.md statements/.gitkeep data/.gitkeep
git commit -m "docs: add setup instructions and workflow README"
git push
```

---

## Post-MVP Backlog (explicitly deferred, per spec)

- Live exchange-rate conversion for foreign-currency transactions
- Export (PDF/Excel) of filtered views
- Year-over-year comparison view
- Budgets / savings goals with warnings
- Automatic folder watching (currently manual "scan" trigger only)
