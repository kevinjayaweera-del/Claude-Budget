import sys
from pathlib import Path

from server.file_hash import hash_file
from server.parsers.csv_parser import parse_csv
from server.parsers.pdf_parser import parse_pdf
from server.categorize import RuleBasedCategorizer


def _existing_counts(conn, keys):
    """For each (date, description, amount_cents, currency) key, count how many
    rows already exist across both transactions and pending_transactions.

    Source is deliberately excluded from the key: re-exporting/re-downloading
    the same statement often produces a different filename (the ZKB PDF this
    was built against even bakes a generation timestamp into its own
    filename), and source is derived from the filename for root-level files —
    keying on source would silently defeat dedup on exactly the re-download
    scenario this feature exists for.
    """
    counts = {}
    for key in keys:
        date, description, amount_cents, currency = key
        row = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM transactions WHERE date = ? AND description = ? "
            " AND amount_cents = ? AND currency = ?) + "
            "(SELECT COUNT(*) FROM pending_transactions WHERE date = ? AND description = ? "
            " AND amount_cents = ? AND currency = ?) AS total",
            (date, description, amount_cents, currency,
             date, description, amount_cents, currency),
        ).fetchone()
        counts[key] = row["total"]
    return counts


def scan_and_parse(conn, statements_dir):
    statements_dir = Path(statements_dir)
    created = 0
    duplicates_skipped = 0
    categorizer = RuleBasedCategorizer(conn)

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

            keys = [
                (row["date"], row["description"], row["amount_cents"], row["currency"])
                for row in rows
            ]
            existing_counts = _existing_counts(conn, set(keys))
            matched_so_far = {}

            file_created = 0
            file_duplicates = 0
            for row, key in zip(rows, keys):
                already_matched = matched_so_far.get(key, 0)
                if already_matched < existing_counts.get(key, 0):
                    # This row's (date, description, amount, currency) combination
                    # already accounts for an existing row we haven't matched yet —
                    # treat it as a re-import of that same transaction and skip it.
                    # Counting (rather than a blanket "seen before" flag) means a
                    # third occurrence of an otherwise-identical transaction is
                    # still correctly imported as new, not silently dropped.
                    matched_so_far[key] = already_matched + 1
                    file_duplicates += 1
                    continue

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
                file_created += 1
        except Exception as exc:
            conn.rollback()
            print(f"Skipping {path.name}: {exc}", file=sys.stderr)
            continue

        conn.commit()
        created += file_created
        duplicates_skipped += file_duplicates

    return {"created": created, "duplicates_skipped": duplicates_skipped}
