import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, abort, current_app, jsonify, request, send_from_directory

from server.db import get_connection, init_db, reset_db, reset_imported_data
from server.import_service import scan_and_parse
from server.categorize import RuleBasedCategorizer, compute_confidence

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"


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


def _fetch_filtered_transactions(conn, args):
    query = (
        "SELECT t.id, t.date, t.description, t.amount_cents, t.currency, "
        "t.category_id, c.name as category_name, t.source, "
        "COALESCE(c.excluded_from_totals, 0) as excluded_from_totals "
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
    return conn.execute(query, params).fetchall()


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
        })

    @app.route("/api/pending", methods=["GET"])
    def list_pending():
        conn = get_db()
        rows = conn.execute(
            "SELECT p.id, p.date, p.description, p.amount_cents, p.currency, "
            "p.category_id, c.name as category_name, p.source, p.category_confidence "
            "FROM pending_transactions p LEFT JOIN categories c ON p.category_id = c.id "
            "ORDER BY p.date"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/pending/<int:pending_id>", methods=["PUT"])
    def update_pending(pending_id):
        data = request.get_json(silent=True)
        required_fields = ("date", "description", "amount_cents", "currency", "category_id")
        if data is None or any(field not in data for field in required_fields):
            return jsonify({"error": "request body must include date, description, "
                                      "amount_cents, currency, and category_id"}), 400

        conn = get_db()
        try:
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
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM pending_transactions WHERE id IN ({placeholders})", ids
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pending_transactions").fetchall()
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
                        categorizer.learn(row["description"], final_category_id, was_correction=True)
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
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM pending_transactions WHERE id IN ({placeholders})", ids)
        else:
            conn.execute("DELETE FROM pending_transactions")
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
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
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
            category = conn.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
            if category is None:
                return jsonify({"error": "category not found"}), 404
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

    @app.route("/api/transactions", methods=["GET"])
    def list_transactions():
        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/transactions", methods=["DELETE"])
    def delete_transactions():
        # Bulk delete scoped by the SAME filters GET /api/transactions
        # already supports — deliberately requires at least one to be set,
        # so an accidental unfiltered call can't silently wipe everything
        # (use /api/database/reset for that, a separate, explicit action).
        filter_keys = ("start", "end", "category_id", "source", "type", "q", "min_amount", "max_amount")
        if not any(request.args.get(key) for key in filter_keys):
            return jsonify({
                "error": "at least one filter (start, end, category_id, source, type, q, "
                         "min_amount, max_amount) must be specified — use /api/database/reset to wipe everything"
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
        conn = get_db()
        rows = _fetch_filtered_transactions(conn, request.args)
        categories = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
        conn.close()

        if not rows:
            return jsonify({"total_income": 0, "total_expense": 0, "by_category": [], "by_month": []})

        df = pd.DataFrame([dict(r) for r in rows])
        df["month"] = df["date"].str.slice(0, 7)
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

        by_month = counted.groupby("month")["amount_cents"].sum().reset_index()
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
                    "SELECT id, name, excluded_from_totals FROM categories ORDER BY id"
                )],
                "category_rules": [dict(r) for r in conn.execute(
                    "SELECT * FROM category_rules ORDER BY id"
                )],
                "transactions": [dict(r) for r in conn.execute(
                    "SELECT * FROM transactions ORDER BY id"
                )],
                "budgets": [dict(r) for r in conn.execute("SELECT * FROM budgets ORDER BY id")],
                "settings": {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")},
            }
        finally:
            conn.close()
        response = jsonify(data)
        response.headers["Content-Disposition"] = "attachment; filename=budget-tracker-export.json"
        return response


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
