from pathlib import Path

import pandas as pd
from flask import Flask, abort, current_app, jsonify, request, send_from_directory

from server.db import get_connection, init_db, reset_db
from server.import_service import scan_and_parse
from server.categorize import RuleBasedCategorizer, compute_confidence

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"


def get_db():
    return get_connection(current_app.config["DB_PATH"])


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
        reset_db(current_app.config["DB_PATH"]).close()
        return jsonify({"ok": True})

    @app.route("/api/scan", methods=["POST"])
    def scan():
        conn = get_db()
        result = scan_and_parse(conn, current_app.config["STATEMENTS_DIR"])
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

    @app.route("/api/categories", methods=["GET"])
    def list_categories():
        conn = get_db()
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

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


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
