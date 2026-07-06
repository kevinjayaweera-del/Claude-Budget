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
