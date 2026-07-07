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
