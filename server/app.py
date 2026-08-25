import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Flask, abort, current_app, jsonify, request, send_from_directory

from server.db import get_connection, init_db, reset_db, reset_imported_data
from server.import_service import scan_and_parse
from server.categorize import RuleBasedCategorizer, compute_confidence
from server.normalize import normalize_description

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

# Groups transaction descriptions from the same merchant that otherwise
# differ only by a card-suffix/reference/postal-code digit run (e.g. "Coop-
# 5307 ZH Hauptbhf" vs "Coop-1122 Bern") — the same digit-stripping trick
# used ad-hoc during this project's manual categorization audits, promoted
# here so the Analyse tab's merchant breakdown and recurring-payment
# detector both group merchants identically to how a human auditor would.
_MERCHANT_DIGIT_RUN_RE = re.compile(r"\b\d{3,6}\b")


def _merchant_key(description):
    key = normalize_description(description)
    key = _MERCHANT_DIGIT_RUN_RE.sub("", key)
    return re.sub(r"\s+", " ", key).strip()


# Loose enough to tolerate a small price change (e.g. an FX-driven or
# annual subscription price bump) without losing the merchant, tight enough
# to exclude unrelated same-merchant purchases that just happen to recur
# (e.g. grocery runs) from being flagged as a "subscription".
_RECURRING_MIN_MONTHS = 3
_RECURRING_MAX_AMOUNT_RATIO = 1.3


def _detect_recurring_merchants(rows):
    """rows: dict-likes with date/description/amount_cents/category_name,
    already restricted to expenses within the lookback window. A merchant
    counts as recurring if it appears in at least _RECURRING_MIN_MONTHS
    distinct calendar months and its amount stays within
    _RECURRING_MAX_AMOUNT_RATIO peak-to-trough."""
    groups = {}
    for r in rows:
        groups.setdefault(_merchant_key(r["description"]), []).append(r)

    result = []
    for key, txns in groups.items():
        months = {t["date"][:7] for t in txns}
        if len(months) < _RECURRING_MIN_MONTHS:
            continue
        amounts = [abs(t["amount_cents"]) for t in txns]
        if min(amounts) == 0 or max(amounts) / min(amounts) > _RECURRING_MAX_AMOUNT_RATIO:
            continue
        dates = sorted(t["date"] for t in txns)
        descriptions = [t["description"] for t in txns]
        label = max(set(descriptions), key=descriptions.count)
        category_names = [t["category_name"] for t in txns if t["category_name"]]
        category_name = max(set(category_names), key=category_names.count) if category_names else None
        result.append({
            "merchant": label,
            "merchant_key": key,
            "category_name": category_name,
            "avg_amount_cents": int(round(sum(amounts) / len(amounts))),
            "occurrences": len(txns),
            "distinct_months": len(months),
            "last_date": dates[-1],
            "first_date": dates[0],
        })
    result.sort(key=lambda r: -r["avg_amount_cents"])
    return result


def _backup_database(db_path):
    """Copy the sqlite file into a <db_path>/../backups/ dir with a
    timestamped name. Best-effort: silently skipped if the source doesn't
    exist yet (e.g. a brand-new in-memory-only test DB)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path

# A flat key/value store rather than dedicated columns (see server/db.py's
# DEFAULT_SETTINGS) — this is the single place that knows how each key's
# text value maps to a real Python type, so a new setting only needs an
# entry here, not a schema migration.
SETTINGS_KEYS = {"auto_categorize_enabled", "confidence_threshold", "default_date_range_days"}


def _parse_setting_value(key, raw_value):
    if key == "auto_categorize_enabled":
        return raw_value == "true"
    if key == "confidence_threshold":
        return float(raw_value)
    if key == "default_date_range_days":
        return int(raw_value) if raw_value else None
    return raw_value


def _serialize_setting_value(key, value):
    if key == "auto_categorize_enabled":
        return "true" if value else "false"
    if key == "default_date_range_days":
        return "" if value is None else str(value)
    return str(value)


def get_db():
    return get_connection(current_app.config["DB_PATH"])


# Each formatter takes a pandas Series of ISO date strings ("YYYY-MM-DD")
# and returns period-label strings suitable for grouping/sorting
# lexicographically in chronological order.
_PERIOD_KEY_FORMATTERS = {
    "day": lambda dates: dates,
    "week": lambda dates: pd.to_datetime(dates).dt.strftime("%G-W%V"),
    "month": lambda dates: dates.str.slice(0, 7),
    "quarter": lambda dates: pd.to_datetime(dates).dt.to_period("Q").astype(str).str.replace("Q", "-Q"),
    "year": lambda dates: dates.str.slice(0, 4),
}


def _period_key(dates, granularity):
    return _PERIOD_KEY_FORMATTERS[granularity](dates)


def _split_multi_value(raw):
    """category_id/account_id/tag_id accept a single id ("5") or a
    comma-separated list ("5,9,12") for multi-select filtering — the Budget
    tab's filter UI lets users pick several categories/accounts/tags at
    once to compare them side by side. Single-id callers (existing tests,
    drill-down clicks) keep working unchanged since a value with no comma
    is just a one-element list."""
    if not raw:
        return []
    # Most callers are query-string values (already strings), but
    # POST /api/transactions/tags/bulk passes a JSON request body straight
    # through as filter_args — a caller sending category_id/account_id as a
    # JSON number (the natural representation for an id) used to crash here
    # with an unhandled AttributeError (int has no .split()).
    if not isinstance(raw, str):
        return [str(raw)]
    return [v for v in raw.split(",") if v]


def _fetch_filtered_transactions(conn, args):
    query = (
        "SELECT t.id, t.date, t.description, t.amount_cents, t.currency, "
        "t.category_id, c.name as category_name, t.source, "
        "t.account_id, a.name as account_name, "
        "COALESCE(c.excluded_from_totals, 0) as excluded_from_totals "
        "FROM transactions t "
        "LEFT JOIN categories c ON t.category_id = c.id "
        "LEFT JOIN accounts a ON t.account_id = a.id WHERE 1=1"
    )
    params = []
    if args.get("start"):
        query += " AND t.date >= ?"
        params.append(args["start"])
    if args.get("end"):
        query += " AND t.date <= ?"
        params.append(args["end"])
    category_ids = _split_multi_value(args.get("category_id"))
    if category_ids:
        query += f" AND t.category_id IN ({','.join('?' for _ in category_ids)})"
        params.extend(category_ids)
    else:
        # Hidden categories (e.g. "Versteckt") are excluded from every
        # default view — ledger, dashboard, budgets. Explicitly filtering by
        # that category_id (above) is the one deliberate way to still see
        # them, e.g. from "Regeln verwalten".
        query += " AND COALESCE(c.is_hidden, 0) = 0"
    if args.get("source"):
        query += " AND t.source = ?"
        params.append(args["source"])
    account_ids = _split_multi_value(args.get("account_id"))
    if account_ids:
        query += f" AND t.account_id IN ({','.join('?' for _ in account_ids)})"
        params.extend(account_ids)
    tag_ids = _split_multi_value(args.get("tag_id"))
    if tag_ids:
        query += (
            " AND EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id "
            f"AND tt.tag_id IN ({','.join('?' for _ in tag_ids)}))"
        )
        params.extend(tag_ids)
    if args.get("type") == "income":
        query += " AND t.amount_cents > 0"
    elif args.get("type") == "expense":
        query += " AND t.amount_cents < 0"
    if args.get("q"):
        query += " AND t.description LIKE ?"
        params.append(f"%{args['q']}%")
    if args.get("min_amount"):
        try:
            min_amount = float(args["min_amount"])
        except ValueError:
            abort(400, description="min_amount must be a number")
        query += " AND t.amount_cents >= ?"
        params.append(round(min_amount * 100))
    if args.get("max_amount"):
        try:
            max_amount = float(args["max_amount"])
        except ValueError:
            abort(400, description="max_amount must be a number")
        query += " AND t.amount_cents <= ?"
        params.append(round(max_amount * 100))
    query += " ORDER BY t.date DESC"
    rows = conn.execute(query, params).fetchall()
    # merchant_key has no SQL-side equivalent (it's a Python regex pipeline),
    # so this filters the already-fetched rows rather than the query itself —
    # used by the Analyse tab's Top-Händler/Wiederkehrende-Zahlungen widgets
    # to drill down into exactly the merchant group a summary row aggregated.
    merchant_key = args.get("merchant_key")
    if merchant_key:
        rows = [r for r in rows if _merchant_key(r["description"]) == merchant_key]
    return rows


def _attach_tags(conn, rows):
    """rows: list of dicts with an "id" key (transaction ids). Adds a
    "tags" key (list of {id, name}, empty if none) to each, via one bulk
    query rather than one-per-row."""
    if not rows:
        return rows
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in ids)
    tag_rows = conn.execute(
        f"SELECT tt.transaction_id, t.id, t.name FROM transaction_tags tt "
        f"JOIN tags t ON tt.tag_id = t.id WHERE tt.transaction_id IN ({placeholders}) "
        f"ORDER BY t.name",
        ids,
    ).fetchall()
    tags_by_transaction = {}
    for tag_row in tag_rows:
        tags_by_transaction.setdefault(tag_row["transaction_id"], []).append(
            {"id": tag_row["id"], "name": tag_row["name"]}
        )
    for row in rows:
        row["tags"] = tags_by_transaction.get(row["id"], [])
    return rows


def create_app(db_path=None, statements_dir=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = Path(db_path) if db_path else BASE_DIR / "data" / "budget.db"
    app.config["STATEMENTS_DIR"] = Path(statements_dir) if statements_dir else BASE_DIR / "statements"
    app.config["STATEMENTS_DIR"].mkdir(parents=True, exist_ok=True)
    init_db(app.config["DB_PATH"]).close()

    register_routes(app)
    return app


def register_routes(app):
    @app.errorhandler(400)
    def handle_bad_request(error):
        message = error.description if getattr(error, "description", None) else "bad request"
        return jsonify({"error": message}), 400

    @app.route("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(WEB_DIR, filename)

    @app.route("/api/database/reset", methods=["POST"])
    def reset_database():
        # Testing/dev convenience, not a normal-operation route — wipes all
        # transactions, pending rows, imported-file records, and learned
        # rules, then reseeds the defaults, so the same statements can be
        # rescanned repeatedly from a clean slate.
        if request.args.get("backup") == "true":
            _backup_database(current_app.config["DB_PATH"])
        reset_db(current_app.config["DB_PATH"]).close()
        return jsonify({"ok": True})

    @app.route("/api/database/reset-imports", methods=["POST"])
    def reset_imports():
        # Test-import convenience: wipes only transactions/pending rows/
        # imported-file records so statements can be rescanned from
        # scratch, but — unlike /api/database/reset — keeps categories,
        # learned rules, budgets and settings intact.
        if request.args.get("backup") == "true":
            _backup_database(current_app.config["DB_PATH"])
        reset_imported_data(current_app.config["DB_PATH"]).close()
        return jsonify({"ok": True})

    @app.route("/api/scan", methods=["POST"])
    def scan():
        # ?dry_run=true previews what a scan would do (new vs. duplicate
        # counts) without writing anything — used to warn about duplicates
        # and let the user cancel before any pending rows are created.
        dry_run = request.args.get("dry_run") == "true"
        conn = get_db()
        result = scan_and_parse(conn, current_app.config["STATEMENTS_DIR"], dry_run=dry_run)
        conn.close()
        return jsonify({
            "new_pending": result["created"],
            "duplicates_skipped": result["duplicates_skipped"],
            "failed_files": result["failed_files"],
            "row_errors": result["row_errors"],
        })

    @app.route("/api/pending", methods=["GET"])
    def list_pending():
        conn = get_db()
        rows = conn.execute(
            "SELECT p.id, p.date, p.description, p.amount_cents, p.currency, "
            "p.category_id, c.name as category_name, p.source, p.category_confidence "
            "FROM pending_transactions p LEFT JOIN categories c ON p.category_id = c.id "
            # Deliberately NOT filtering out is_hidden categories here, unlike
            # every confirmed-transaction view: pending_transactions is a
            # working review queue, not a final ledger. A row landing under a
            # hidden category (e.g. corrected mid-review to "Versteckt")
            # previously vanished from this list — gone from the review
            # table, never confirmable or deletable through the app, with no
            # recovery path. It stays visible here until the user explicitly
            # confirms or deletes it.
            "ORDER BY p.date"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/imported-files", methods=["GET"])
    def list_imported_files():
        # Per-file totals for reconciling against the original statement —
        # combines transactions (already confirmed) and pending_transactions
        # (not yet confirmed) for the same file_id, since a scan's rows can
        # be partially confirmed (confirm_import accepts a subset of ids).
        # Unlike /api/pending's file grouping (by the raw source string,
        # only ever the current unconfirmed batch), this persists for every
        # file ever imported, confirmed or not.
        conn = get_db()
        rows = conn.execute(
            "SELECT f.id, f.filename, f.source, f.imported_at, "
            "COALESCE(t.cnt, 0) + COALESCE(p.cnt, 0) AS transaction_count, "
            "COALESCE(t.income, 0) + COALESCE(p.income, 0) AS income_cents, "
            "COALESCE(t.expense, 0) + COALESCE(p.expense, 0) AS expense_cents "
            "FROM imported_files f "
            "LEFT JOIN ("
            "  SELECT file_id, COUNT(*) AS cnt, "
            "  SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END) AS income, "
            "  SUM(CASE WHEN amount_cents < 0 THEN amount_cents ELSE 0 END) AS expense "
            "  FROM transactions GROUP BY file_id"
            ") t ON t.file_id = f.id "
            "LEFT JOIN ("
            "  SELECT file_id, COUNT(*) AS cnt, "
            "  SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END) AS income, "
            "  SUM(CASE WHEN amount_cents < 0 THEN amount_cents ELSE 0 END) AS expense "
            "  FROM pending_transactions GROUP BY file_id"
            ") p ON p.file_id = f.id "
            "ORDER BY f.imported_at DESC"
        ).fetchall()
        conn.close()
        result = [dict(r) for r in rows]
        for r in result:
            r["net_cents"] = r["income_cents"] + r["expense_cents"]
        return jsonify(result)

    @app.route("/api/pending/<int:pending_id>", methods=["PUT"])
    def update_pending(pending_id):
        data = request.get_json(silent=True)
        required_fields = ("date", "description", "amount_cents", "currency", "category_id")
        if data is None or any(field not in data for field in required_fields):
            return jsonify({"error": "request body must include date, description, "
                                      "amount_cents, currency, and category_id"}), 400

        conn = get_db()
        try:
            category = conn.execute(
                "SELECT id FROM categories WHERE id = ?", (data["category_id"],)
            ).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 400
            cursor = conn.execute(
                "UPDATE pending_transactions SET date=?, description=?, amount_cents=?, "
                "currency=?, category_id=? WHERE id=?",
                (data["date"], data["description"], data["amount_cents"],
                 data["currency"], data["category_id"], pending_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "pending transaction not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/pending/<int:pending_id>", methods=["DELETE"])
    def delete_pending(pending_id):
        conn = get_db()
        try:
            cursor = conn.execute("DELETE FROM pending_transactions WHERE id=?", (pending_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "pending transaction not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/import/confirm", methods=["POST"])
    def confirm_import():
        conn = get_db()
        data = request.get_json(silent=True)
        ids = data.get("ids") if data else None
        if ids is not None and not isinstance(ids, list):
            conn.close()
            return jsonify({"error": "ids must be a list"}), 400
        # DELETE ... RETURNING atomically claims-and-removes the rows in one
        # statement, rather than a separate SELECT followed later by a
        # separate DELETE — the previous two-step version let two concurrent
        # confirm requests both read the same still-present pending rows
        # before either had deleted them, double-inserting into
        # transactions. It also fixes an explicit empty ids list ("ids": [])
        # being treated the same as "ids omitted" (Python: [] is falsy) and
        # silently confirming every pending row instead of none.
        if ids is None:
            rows = conn.execute("DELETE FROM pending_transactions RETURNING *").fetchall()
        elif ids:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"DELETE FROM pending_transactions WHERE id IN ({placeholders}) RETURNING *", ids
            ).fetchall()
        else:
            rows = []
        categorizer = RuleBasedCategorizer(conn)
        # "Unkategorisiert" is the "I don't know" placeholder, never a real
        # answer — confirming a row with it (whether left as the default or
        # actively corrected back to it) must never teach the categorizer a
        # keyword-to-Unkategorisiert rule. Doing so would make the system
        # more confident about predicting "no category" for similar future
        # bookings, which needs MORE manual review, not less.
        unkategorisiert_id = conn.execute(
            "SELECT id FROM categories WHERE name = 'Unkategorisiert'"
        ).fetchone()["id"]
        for row in rows:
            final_category_id = row["category_id"]
            suggested_category_id = row["suggested_category_id"]
            suggested_rule_id = row["suggested_rule_id"]
            manually_corrected = 1 if final_category_id != suggested_category_id else 0
            should_learn = final_category_id is not None and final_category_id != unkategorisiert_id

            if suggested_rule_id is not None:
                if manually_corrected:
                    conn.execute(
                        "UPDATE category_rules SET correction_count = correction_count + 1 WHERE id = ?",
                        (suggested_rule_id,),
                    )
                    if should_learn:
                        suggested_keyword = conn.execute(
                            "SELECT keyword FROM category_rules WHERE id = ?", (suggested_rule_id,)
                        ).fetchone()["keyword"]
                        categorizer.learn(
                            row["description"], final_category_id, was_correction=True,
                            exclude_keyword=suggested_keyword,
                        )
                else:
                    conn.execute(
                        "UPDATE category_rules SET match_count = match_count + 1 WHERE id = ?",
                        (suggested_rule_id,),
                    )
            elif should_learn:
                categorizer.learn(row["description"], final_category_id, was_correction=False)

            conn.execute(
                "INSERT INTO transactions "
                "(date, description, amount_cents, currency, category_id, source, file_id, account_id, manually_corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["description"], row["amount_cents"], row["currency"],
                 final_category_id, row["source"], row["file_id"], row["account_id"], manually_corrected),
            )
        conn.commit()
        imported = len(rows)
        conn.close()
        return jsonify({"imported": imported})

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
            # pending_transactions.suggested_rule_id references this table
            # with no ON DELETE clause — deleting a rule still referenced by
            # an unreviewed pending row used to crash with an unhandled
            # foreign-key constraint error. Clear just the stale suggestion
            # provenance first; the pending row itself and its current
            # category_id are untouched, same as a freshly-imported row
            # that never matched any rule.
            conn.execute(
                "UPDATE pending_transactions SET suggested_rule_id = NULL, "
                "category_confidence = NULL WHERE suggested_rule_id = ?",
                (rule_id,),
            )
            cursor = conn.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "rule not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/budgets", methods=["GET"])
    def list_budgets():
        conn = get_db()
        rows = conn.execute(
            "SELECT b.id, b.category_id, c.name as category_name, b.monthly_limit_cents "
            "FROM budgets b JOIN categories c ON b.category_id = c.id "
            "ORDER BY c.name"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/budgets/<int:category_id>", methods=["PUT"])
    def set_budget(category_id):
        data = request.get_json(silent=True)
        if data is None or "monthly_limit_cents" not in data:
            return jsonify({"error": "request body must include monthly_limit_cents"}), 400
        conn = get_db()
        try:
            category = conn.execute(
                "SELECT id FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 404
            conn.execute(
                "INSERT INTO budgets (category_id, monthly_limit_cents) VALUES (?, ?) "
                "ON CONFLICT(category_id) DO UPDATE SET monthly_limit_cents = excluded.monthly_limit_cents",
                (category_id, data["monthly_limit_cents"]),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/budgets/<int:category_id>", methods=["DELETE"])
    def delete_budget(category_id):
        conn = get_db()
        try:
            cursor = conn.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"error": "budget not found"}), 404
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/settings", methods=["GET"])
    def get_settings():
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = {row["key"]: _parse_setting_value(row["key"], row["value"]) for row in rows}
        db_path = current_app.config["DB_PATH"]
        result["db_info"] = {
            "transaction_count": conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"],
            "pending_count": conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"],
            "category_count": conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"],
            "rule_count": conn.execute("SELECT COUNT(*) c FROM category_rules").fetchone()["c"],
            "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        }
        conn.close()
        return jsonify(result)

    @app.route("/api/settings", methods=["PUT"])
    def update_settings():
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "request body must be a JSON object"}), 400
        unknown = set(data.keys()) - SETTINGS_KEYS
        if unknown:
            return jsonify({"error": f"unknown setting(s): {', '.join(sorted(unknown))}"}), 400
        if "confidence_threshold" in data:
            try:
                threshold = float(data["confidence_threshold"])
            except (TypeError, ValueError):
                return jsonify({"error": "confidence_threshold must be a number"}), 400
            if not (0.0 <= threshold <= 1.0):
                return jsonify({"error": "confidence_threshold must be between 0 and 1"}), 400
        if "default_date_range_days" in data and data["default_date_range_days"] not in (None, ""):
            # Stored as a plain string and re-parsed via int() on every GET
            # /api/settings (see _parse_setting_value) — an unvalidated
            # non-numeric value here used to be accepted with 200 OK and
            # then permanently break every subsequent GET /api/settings
            # call with an unhandled 500, since "" is the only string
            # _parse_setting_value treats as "no default" (see
            # DEFAULT_SETTINGS in server/db.py).
            try:
                int(data["default_date_range_days"])
            except (TypeError, ValueError):
                return jsonify({"error": "default_date_range_days must be a whole number"}), 400
        conn = get_db()
        try:
            for key, value in data.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, _serialize_setting_value(key, value)),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/categories", methods=["GET"])
    def list_categories():
        conn = get_db()
        rows = conn.execute(
            "SELECT id, name, COALESCE(is_hidden, 0) as is_hidden "
            "FROM categories ORDER BY name"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/categories", methods=["POST"])
    def create_category():
        data = request.get_json(silent=True)
        if data is None or not str(data.get("name", "")).strip():
            return jsonify({"error": "request body must include a non-empty name"}), 400
        name = str(data["name"]).strip()
        conn = get_db()
        try:
            existing = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
            if existing is not None:
                return jsonify({"error": "a category with this name already exists"}), 400
            cursor = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
            conn.commit()
            return jsonify({"id": cursor.lastrowid, "name": name}), 201
        finally:
            conn.close()

    @app.route("/api/categories/<int:category_id>", methods=["PUT"])
    def rename_category(category_id):
        data = request.get_json(silent=True)
        if data is None or not str(data.get("name", "")).strip():
            return jsonify({"error": "request body must include a non-empty name"}), 400
        name = str(data["name"]).strip()
        conn = get_db()
        try:
            category = conn.execute("SELECT id, name FROM categories WHERE id = ?", (category_id,)).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 404
            if category["name"] == "Unkategorisiert":
                # Several code paths (import_service.scan_and_parse,
                # confirm_import, update_transaction_category) look this
                # category up by this exact literal name at runtime, not
                # just at seed time — renaming it would silently break
                # every subsequent import. Same protection DELETE already
                # has, extended to rename.
                return jsonify({"error": "Unkategorisiert cannot be renamed — it's the required fallback category"}), 400
            duplicate = conn.execute(
                "SELECT id FROM categories WHERE name = ? AND id != ?", (name, category_id)
            ).fetchone()
            if duplicate is not None:
                return jsonify({"error": "a category with this name already exists"}), 400
            conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/categories/<int:category_id>", methods=["DELETE"])
    def delete_category(category_id):
        conn = get_db()
        try:
            category = conn.execute(
                "SELECT id, name FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 404
            if category["name"] == "Unkategorisiert":
                return jsonify({"error": "Unkategorisiert cannot be deleted — it's the required fallback category"}), 400

            dependency_counts = {
                "transaction_count": conn.execute(
                    "SELECT COUNT(*) c FROM transactions WHERE category_id = ?", (category_id,)
                ).fetchone()["c"],
                "pending_count": conn.execute(
                    "SELECT COUNT(*) c FROM pending_transactions WHERE category_id = ?", (category_id,)
                ).fetchone()["c"],
                "rule_count": conn.execute(
                    "SELECT COUNT(*) c FROM category_rules WHERE category_id = ?", (category_id,)
                ).fetchone()["c"],
                "budget_count": conn.execute(
                    "SELECT COUNT(*) c FROM budgets WHERE category_id = ?", (category_id,)
                ).fetchone()["c"],
            }
            has_dependencies = any(dependency_counts.values())
            confirmed = request.args.get("confirm") == "true"
            if has_dependencies and not confirmed:
                return jsonify(dependency_counts), 409

            if has_dependencies:
                unkategorisiert_id = conn.execute(
                    "SELECT id FROM categories WHERE name = 'Unkategorisiert'"
                ).fetchone()["id"]
                # Reassign rather than delete: transactions/pending rows keep
                # their history under the fallback category, and rules keep
                # their learned match/correction counts instead of losing
                # them — only now suggesting "Unkategorisiert" going forward.
                conn.execute(
                    "UPDATE transactions SET category_id = ? WHERE category_id = ?",
                    (unkategorisiert_id, category_id),
                )
                conn.execute(
                    "UPDATE pending_transactions SET category_id = ? WHERE category_id = ?",
                    (unkategorisiert_id, category_id),
                )
                conn.execute(
                    "UPDATE pending_transactions SET suggested_category_id = NULL, "
                    "suggested_rule_id = NULL, category_confidence = NULL "
                    "WHERE suggested_category_id = ?",
                    (category_id,),
                )
                conn.execute(
                    "UPDATE category_rules SET category_id = ? WHERE category_id = ?",
                    (unkategorisiert_id, category_id),
                )
                conn.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))

            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/tags", methods=["GET"])
    def list_tags():
        conn = get_db()
        rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/tags", methods=["POST"])
    def create_tag():
        data = request.get_json(silent=True)
        if data is None or not str(data.get("name", "")).strip():
            return jsonify({"error": "request body must include a non-empty name"}), 400
        name = str(data["name"]).strip()
        conn = get_db()
        try:
            existing = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            if existing is not None:
                return jsonify({"error": "a tag with this name already exists"}), 400
            cursor = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            conn.commit()
            return jsonify({"id": cursor.lastrowid, "name": name}), 201
        finally:
            conn.close()

    @app.route("/api/tags/<int:tag_id>", methods=["PUT"])
    def rename_tag(tag_id):
        data = request.get_json(silent=True)
        if data is None or not str(data.get("name", "")).strip():
            return jsonify({"error": "request body must include a non-empty name"}), 400
        name = str(data["name"]).strip()
        conn = get_db()
        try:
            tag = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
            if tag is None:
                return jsonify({"error": "tag not found"}), 404
            duplicate = conn.execute(
                "SELECT id FROM tags WHERE name = ? AND id != ?", (name, tag_id)
            ).fetchone()
            if duplicate is not None:
                return jsonify({"error": "a tag with this name already exists"}), 400
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
    def delete_tag(tag_id):
        conn = get_db()
        try:
            tag = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
            if tag is None:
                return jsonify({"error": "tag not found"}), 404
            # transaction_tags has ON DELETE CASCADE, so assignments go too.
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/transactions/<int:transaction_id>/tags", methods=["POST"])
    def assign_tag(transaction_id):
        data = request.get_json(silent=True)
        if data is None or not data.get("tag_id"):
            return jsonify({"error": "request body must include tag_id"}), 400
        conn = get_db()
        try:
            txn = conn.execute("SELECT id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
            if txn is None:
                return jsonify({"error": "transaction not found"}), 404
            tag = conn.execute("SELECT id FROM tags WHERE id = ?", (data["tag_id"],)).fetchone()
            if tag is None:
                return jsonify({"error": "tag not found"}), 404
            conn.execute(
                "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                (transaction_id, data["tag_id"]),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/transactions/<int:transaction_id>/tags/<int:tag_id>", methods=["DELETE"])
    def unassign_tag(transaction_id, tag_id):
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM transaction_tags WHERE transaction_id = ? AND tag_id = ?",
                (transaction_id, tag_id),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/transactions/tags/bulk", methods=["POST"])
    def bulk_assign_tag():
        # Built for "tag every booking from this trip" — a date range (start/
        # end, both required so this can't accidentally sweep the whole
        # ledger) plus any of the usual filters (category_id, account_id,
        # type, amount, q). Reuses _fetch_filtered_transactions so this stays
        # in lockstep with every other filtered view instead of duplicating
        # the WHERE-clause logic.
        data = request.get_json(silent=True)
        if data is None or not data.get("tag_id"):
            return jsonify({"error": "request body must include tag_id"}), 400
        if not data.get("start") or not data.get("end"):
            return jsonify({"error": "request body must include start and end"}), 400
        conn = get_db()
        try:
            tag = conn.execute("SELECT id FROM tags WHERE id = ?", (data["tag_id"],)).fetchone()
            if tag is None:
                return jsonify({"error": "tag not found"}), 404
            # data's own "tag_id" (the tag to ASSIGN) would otherwise collide
            # with _fetch_filtered_transactions's "tag_id" filter (transactions
            # already carrying that tag) — exclude it so this bulk-assigns by
            # date/category/account/type/amount/search only, never by
            # "already has tag X", which isn't a meaningful filter here anyway.
            filter_args = {k: v for k, v in data.items() if k != "tag_id"}
            rows = _fetch_filtered_transactions(conn, filter_args)
            for row in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    (row["id"], data["tag_id"]),
                )
            conn.commit()
            return jsonify({"tagged": len(rows)})
        finally:
            conn.close()

    # Merchant-name vocabulary strongly associated with travel booking/travel
    # itself — deliberately NOT based on the merchant's billing city/country
    # suffix some descriptions carry (e.g. "SPOTIFYCH,STOCKHOLM",
    # "OPENAI*CHATGPTSUBSCR,DUBLIN"): that suffix is usually just the
    # service's corporate billing address and fires on ordinary subscriptions
    # having nothing to do with travel. This keyword list is the precise
    # signal; the scan is a discovery aid to help find trip date ranges; it
    # doesn't tag anything by itself.
    _TRAVEL_KEYWORDS = [
        "hotel", "hostel", "booking.com", "airbnb", "expedia", "trivago",
        "airlines", "airline", "swissintlairlines", "lufthansa", "ryanair",
        "easyjet", "klm", "britishairways", "airfrance", "eurowings",
        "hertz", "sixt", "europcar", "avis", "rentalcars", "mietwagen",
        "skyscanner", "scandic", "marriott", "hilton", "novotel", "ibis",
        "flughafen", "airport",
    ]

    @app.route("/api/transactions/travel-candidates", methods=["GET"])
    def travel_candidates():
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT t.id, t.date, t.description, t.amount_cents, "
                "c.name AS category_name "
                "FROM transactions t LEFT JOIN categories c ON t.category_id = c.id "
                # Consistent with every other data endpoint
                # (_fetch_filtered_transactions) — a hidden-category
                # transaction (e.g. "Versteckt") must not surface here
                # either.
                "WHERE COALESCE(c.is_hidden, 0) = 0 "
                "ORDER BY t.date"
            ).fetchall()
        finally:
            conn.close()

        candidates = []
        for row in rows:
            normalized = normalize_description(row["description"])
            matched = [kw for kw in _TRAVEL_KEYWORDS if kw in normalized]
            if matched:
                candidates.append({
                    "id": row["id"],
                    "date": row["date"],
                    "description": row["description"],
                    "amount_cents": row["amount_cents"],
                    "category_name": row["category_name"],
                    "matched_keywords": matched,
                })
        return jsonify(candidates)

    @app.route("/api/accounts", methods=["GET"])
    def list_accounts():
        conn = get_db()
        rows = conn.execute("SELECT id, source_key, name FROM accounts ORDER BY name").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/accounts/<int:account_id>", methods=["PUT"])
    def rename_account(account_id):
        data = request.get_json(silent=True)
        if data is None or not str(data.get("name", "")).strip():
            return jsonify({"error": "request body must include a non-empty name"}), 400
        name = str(data["name"]).strip()
        conn = get_db()
        try:
            account = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if account is None:
                return jsonify({"error": "account not found"}), 404
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
    def delete_account(account_id):
        conn = get_db()
        try:
            account = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if account is None:
                return jsonify({"error": "account not found"}), 404
            dependency_counts = {
                "transaction_count": conn.execute(
                    "SELECT COUNT(*) c FROM transactions WHERE account_id = ?", (account_id,)
                ).fetchone()["c"],
                "pending_count": conn.execute(
                    "SELECT COUNT(*) c FROM pending_transactions WHERE account_id = ?", (account_id,)
                ).fetchone()["c"],
            }
            if any(dependency_counts.values()):
                return jsonify(dependency_counts), 409
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/transactions", methods=["GET"])
    def list_transactions():
        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        result = _attach_tags(conn, [dict(r) for r in rows])
        conn.close()
        return jsonify(result)

    @app.route("/api/transactions/<int:transaction_id>", methods=["PUT"])
    def update_transaction_category(transaction_id):
        data = request.get_json(silent=True)
        if data is None or "category_id" not in data:
            return jsonify({"error": "request body must include category_id"}), 400
        category_id = data["category_id"]
        conn = get_db()
        try:
            txn = conn.execute(
                "SELECT id, description FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
            if txn is None:
                return jsonify({"error": "transaction not found"}), 404
            category = conn.execute(
                "SELECT id, name FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 404

            conn.execute(
                "UPDATE transactions SET category_id = ?, manually_corrected = 1 WHERE id = ?",
                (category_id, transaction_id),
            )
            # A manual re-categorization from the ledger is always a
            # deliberate correction — feed it back into the rule engine the
            # same way a corrected import-confirm row is (see
            # confirm_import), so future imports of similar descriptions
            # benefit. Never learn "Unkategorisiert" as a target.
            if category["name"] != "Unkategorisiert":
                RuleBasedCategorizer(conn).learn(txn["description"], category_id, was_correction=True)
            conn.commit()
            return jsonify({"ok": True, "category_id": category_id, "category_name": category["name"]})
        finally:
            conn.close()

    @app.route("/api/transactions", methods=["DELETE"])
    def delete_transactions():
        # Bulk delete scoped by the SAME filters GET /api/transactions
        # already supports — deliberately requires at least one to be set,
        # so an accidental unfiltered call can't silently wipe everything
        # (use /api/database/reset for that, a separate, explicit action).
        filter_keys = (
            "start", "end", "category_id", "account_id", "tag_id",
            "source", "type", "q", "min_amount", "max_amount",
        )
        if not any(request.args.get(key) for key in filter_keys):
            return jsonify({
                "error": "at least one filter (start, end, category_id, account_id, tag_id, "
                         "source, type, q, min_amount, max_amount) must be specified — "
                         "use /api/database/reset to wipe everything"
            }), 400

        conn = get_db()
        try:
            rows = _fetch_filtered_transactions(conn, request.args)
            ids = [row["id"] for row in rows]
            if not ids:
                return jsonify({"deleted": 0})
            if request.args.get("backup") == "true":
                conn.close()
                _backup_database(current_app.config["DB_PATH"])
                conn = get_db()
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", ids)
            conn.commit()
            return jsonify({"deleted": len(ids)})
        finally:
            conn.close()

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
        # granularity controls the NEW by_period breakdown's bucket size for
        # the dashboard's flexible time axis; by_month is unchanged (always
        # month-bucketed) so the existing Übersicht trend chart keeps working
        # without a matching change on its end.
        granularity = request.args.get("granularity", "month")
        if granularity not in _PERIOD_KEY_FORMATTERS:
            abort(400, description=f"granularity must be one of {sorted(_PERIOD_KEY_FORMATTERS)}")

        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        categories = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
        # Tags are many-to-many (transaction_tags), unlike category_id, so
        # they can't ride along on the same SELECT — fetched here (while the
        # connection is still open) as a separate id -> [tag names] map for
        # the by_tag breakdown below.
        row_ids = [r["id"] for r in rows]
        tags_by_txn = {}
        if row_ids:
            placeholders = ",".join("?" for _ in row_ids)
            for tr in conn.execute(
                f"SELECT tt.transaction_id, tg.name AS tag_name FROM transaction_tags tt "
                f"JOIN tags tg ON tt.tag_id = tg.id WHERE tt.transaction_id IN ({placeholders})",
                row_ids,
            ):
                tags_by_txn.setdefault(tr["transaction_id"], []).append(tr["tag_name"])
        conn.close()

        if not rows:
            return jsonify({
                "total_income": 0, "total_expense": 0, "by_category": [],
                "by_category_income": [], "by_merchant": [], "by_tag": [], "by_month": [], "by_period": [],
            })

        df = pd.DataFrame([dict(r) for r in rows])
        df["month"] = df["date"].str.slice(0, 7)
        df["period"] = _period_key(df["date"], granularity)
        # Categories like "Kreditkarten-Ausgleich" (paying off a credit card
        # bill from the linked checking account) aren't real income/spending
        # — the money was already counted once, on whichever side the
        # individual transactions were imported from. Counting the
        # settlement too would double it. The full transaction (both sides
        # of it) is still returned by /api/transactions unfiltered, so the
        # ledger itself stays a complete, reconcilable record.
        counted = df[df.excluded_from_totals == 0]

        total_income = int(counted[counted.amount_cents > 0]["amount_cents"].sum())
        total_expense = int(counted[counted.amount_cents < 0]["amount_cents"].sum())

        expenses = counted[counted.amount_cents < 0].copy()
        by_category_list = []
        if not expenses.empty:
            grouped = expenses.groupby("category_id")["amount_cents"].sum().abs().reset_index()
            by_category_list = [
                {"category": categories.get(row.category_id, "Unbekannt"), "amount_cents": int(row.amount_cents)}
                for row in grouped.itertuples()
            ]

        income = counted[counted.amount_cents > 0].copy()
        by_category_income_list = []
        if not income.empty:
            grouped_income = income.groupby("category_id")["amount_cents"].sum().reset_index()
            by_category_income_list = [
                {"category": categories.get(row.category_id, "Unbekannt"), "amount_cents": int(row.amount_cents)}
                for row in grouped_income.itertuples()
            ]

        # Merchant breakdown for the Analyse tab's "Top-Händler" widget —
        # groups expenses the same way the manual categorization audits did
        # (see _merchant_key), so e.g. every "Coop-<branch> ..." row lands
        # under one entry instead of one per branch/card-suffix variant.
        # "label" picks the group's most common raw description as a
        # human-readable representative; merchant_key is what the frontend
        # sends back for drilldown (via /api/transactions?merchant_key=...).
        by_merchant_list = []
        if not expenses.empty:
            merchant_expenses = expenses.copy()
            merchant_expenses["merchant_key"] = merchant_expenses["description"].map(_merchant_key)
            labels = merchant_expenses.groupby("merchant_key")["description"].agg(
                lambda s: s.value_counts().idxmax()
            )
            grouped_merchant = merchant_expenses.groupby("merchant_key").agg(
                amount_cents=("amount_cents", "sum"), count=("merchant_key", "size")
            ).reset_index()
            grouped_merchant["label"] = grouped_merchant["merchant_key"].map(labels)
            grouped_merchant = grouped_merchant.sort_values("amount_cents")
            by_merchant_list = [
                {
                    "merchant": row.label,
                    "merchant_key": row.merchant_key,
                    "amount_cents": int(abs(row.amount_cents)),
                    "count": int(row.count),
                }
                for row in grouped_merchant.itertuples()
            ][:15]

        # Mirrors by_category (expenses only), but a transaction can carry
        # several tags at once — each one gets the full amount, same as
        # filtering the ledger by that tag would show, rather than splitting
        # it between tags.
        tag_totals = {}
        for row in expenses.itertuples():
            for tag_name in tags_by_txn.get(row.id, []):
                tag_totals[tag_name] = tag_totals.get(tag_name, 0) + abs(row.amount_cents)
        by_tag_list = [
            {"tag": name, "amount_cents": int(amount)}
            for name, amount in sorted(tag_totals.items())
        ]

        by_month = counted.groupby("month")["amount_cents"].sum().reset_index()
        by_month_list = [
            {"month": row.month, "amount_cents": int(row.amount_cents)}
            for row in by_month.itertuples()
        ]

        by_period_list = []
        if not counted.empty:
            counted = counted.copy()
            counted["income_cents"] = counted["amount_cents"].where(counted["amount_cents"] > 0, 0)
            counted["expense_cents"] = counted["amount_cents"].where(counted["amount_cents"] < 0, 0)
            grouped = counted.groupby("period")[["income_cents", "expense_cents", "amount_cents"]].sum().reset_index()
            grouped = grouped.sort_values("period")
            by_period_list = [
                {
                    "period": row.period,
                    "income_cents": int(row.income_cents),
                    "expense_cents": int(row.expense_cents),
                    "net_cents": int(row.amount_cents),
                }
                for row in grouped.itertuples()
            ]

        return jsonify({
            "total_income": total_income,
            "total_expense": total_expense,
            "by_category": by_category_list,
            "by_category_income": by_category_income_list,
            "by_merchant": by_merchant_list,
            "by_tag": by_tag_list,
            "by_month": by_month_list,
            "by_period": by_period_list,
        })

    @app.route("/api/recurring", methods=["GET"])
    def recurring():
        # Deliberately its own lookback window rather than the Analyse tab's
        # toolbar period — a subscription's cadence only shows up across
        # several months, so this always looks back lookback_days regardless
        # of what date range the rest of the page is currently filtered to.
        # Non-date filters (account/category/tag/...) still apply, same as
        # every other endpoint the toolbar drives.
        try:
            lookback_days = int(request.args.get("lookback_days", 180))
        except ValueError:
            abort(400, description="lookback_days must be a number")
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        conn = get_db()
        args = dict(request.args)
        args["start"] = cutoff
        args["type"] = "expense"
        rows = _fetch_filtered_transactions(conn, args)
        conn.close()

        rows = [dict(r) for r in rows if not r["excluded_from_totals"]]
        return jsonify(_detect_recurring_merchants(rows))

    @app.route("/api/export", methods=["GET"])
    def export_data():
        # Confirmed transactions, categories, learned rules, budgets, and
        # settings — the durable data a restore would need. Deliberately
        # excludes pending_transactions/imported_files (mid-import scratch
        # state, not meaningful to carry across a restore).
        conn = get_db()
        try:
            data = {
                "exported_at": datetime.now().isoformat(),
                "categories": [dict(r) for r in conn.execute(
                    "SELECT id, name, excluded_from_totals, is_hidden FROM categories ORDER BY id"
                )],
                "category_rules": [dict(r) for r in conn.execute(
                    "SELECT * FROM category_rules ORDER BY id"
                )],
                "transactions": [dict(r) for r in conn.execute(
                    "SELECT * FROM transactions ORDER BY id"
                )],
                "budgets": [dict(r) for r in conn.execute("SELECT * FROM budgets ORDER BY id")],
                "accounts": [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id")],
                "tags": [dict(r) for r in conn.execute("SELECT * FROM tags ORDER BY id")],
                "transaction_tags": [dict(r) for r in conn.execute(
                    "SELECT * FROM transaction_tags ORDER BY transaction_id, tag_id"
                )],
                "settings": {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")},
            }
        finally:
            conn.close()
        response = jsonify(data)
        response.headers["Content-Disposition"] = "attachment; filename=budget-tracker-export.json"
        return response


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
