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

        try:
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

            file_created = 0
            for row in rows:
                category_id = categorize(row["description"], conn)
                conn.execute(
                    "INSERT INTO pending_transactions "
                    "(date, description, amount_cents, currency, category_id, source, file_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["date"], row["description"], row["amount_cents"],
                     row["currency"], category_id, source, file_id),
                )
                file_created += 1
        except Exception:
            conn.rollback()
            continue

        conn.commit()
        created += file_created

    return created
