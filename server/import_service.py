import sys
from pathlib import Path

from server.file_hash import hash_file
from server.parsers.csv_parser import parse_csv
from server.parsers.pdf_parser import parse_pdf
from server.categorize import RuleBasedCategorizer
from server.db import get_or_create_account


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


def _auto_categorize_enabled(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()
    return row is None or row["value"] == "true"


def scan_and_parse(conn, statements_dir, dry_run=False):
    """Scan statements_dir for new files and stage their non-duplicate rows
    as pending transactions.

    With dry_run=True, computes and returns the exact same counts (files are
    still parsed, and duplicate-detection still runs against the current
    database state) but performs no writes at all — no imported_files row,
    no pending_transactions rows, no commit. This lets a caller preview a
    scan (e.g. to warn about duplicates and let the user cancel) before
    anything is persisted.
    """
    statements_dir = Path(statements_dir)
    created = 0
    duplicates_skipped = 0
    failed_files = []
    row_errors = []
    categorizer = RuleBasedCategorizer(conn)
    auto_categorize = _auto_categorize_enabled(conn)
    uncategorized_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Unkategorisiert'"
    ).fetchone()["id"]
    # Hidden categories (e.g. "Versteckt") skip pending review entirely —
    # see server/db.py's CATEGORIES_HIDDEN. A matching row is inserted
    # straight into transactions below instead of pending_transactions, so
    # it never shows up in the import KPIs or the review table at all.
    hidden_category_ids = {
        row["id"] for row in conn.execute(
            "SELECT id FROM categories WHERE is_hidden = 1"
        )
    }

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
            for row_error in getattr(rows, "errors", []):
                row_errors.append({"filename": path.name, **row_error})

            source = (
                path.parent.name
                if path.parent.resolve() != statements_dir.resolve()
                else path.stem
            )

            file_id = None
            account_id = None
            if not dry_run:
                cursor = conn.execute(
                    "INSERT INTO imported_files (hash, filename, source, imported_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (file_hash, path.name, source),
                )
                file_id = cursor.lastrowid
                account_id = get_or_create_account(conn, source)

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

                if not dry_run:
                    if auto_categorize:
                        category_id, confidence, rule_id = categorizer.predict(
                            row["description"], row["amount_cents"], row["currency"], source
                        )
                    else:
                        category_id, confidence, rule_id = uncategorized_id, None, None
                    if category_id in hidden_category_ids:
                        conn.execute(
                            "INSERT INTO transactions "
                            "(date, description, amount_cents, currency, category_id, source, "
                            " file_id, account_id, manually_corrected) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                            (row["date"], row["description"], row["amount_cents"],
                             row["currency"], category_id, source, file_id, account_id),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO pending_transactions "
                            "(date, description, amount_cents, currency, category_id, source, file_id, "
                            " account_id, suggested_category_id, suggested_rule_id, category_confidence) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (row["date"], row["description"], row["amount_cents"],
                             row["currency"], category_id, source, file_id, account_id,
                             category_id, rule_id, confidence if rule_id is not None else None),
                        )
                file_created += 1
        except Exception as exc:
            if not dry_run:
                conn.rollback()
            failed_files.append({"filename": path.name, "reason": str(exc)})
            print(f"Skipping {path.name}: {exc}", file=sys.stderr)
            continue

        if not dry_run:
            conn.commit()
        created += file_created
        duplicates_skipped += file_duplicates

    return {
        "created": created,
        "duplicates_skipped": duplicates_skipped,
        "failed_files": failed_files,
        "row_errors": row_errors,
    }
