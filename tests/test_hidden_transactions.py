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


def _write_csv(tmp_path, name, rows):
    lines = ["Datum;Buchungstext;Betrag;Währung"]
    lines.extend(f"{date};{desc};{amount};CHF" for date, desc, amount in rows)
    (tmp_path / "statements" / name).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_hidden_transaction_never_appears_in_pending_review(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "OnlyFans.com Payment", "-19.99"),
        ("02.03.2026", "Migros Zuerich", "-45.90"),
    ])
    client.post("/api/scan")

    pending = client.get("/api/pending").get_json()

    descriptions = {r["description"] for r in pending}
    assert "OnlyFans.com Payment" not in descriptions
    assert "Migros Zuerich" in descriptions


def test_hidden_transaction_excluded_from_transactions_by_default(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "OnlyFans.com Payment", "-19.99"),
        ("02.03.2026", "Migros Zuerich", "-45.90"),
    ])
    client.post("/api/scan")
    client.post("/api/import/confirm")

    rows = client.get("/api/transactions?start=2026-01-01&end=2026-12-31").get_json()

    descriptions = {r["description"] for r in rows}
    assert "OnlyFans.com Payment" not in descriptions
    assert "Migros Zuerich" in descriptions


def test_hidden_transaction_visible_when_explicitly_filtered_by_its_category(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "OnlyFans.com Payment", "-19.99"),
    ])
    client.post("/api/scan")
    client.post("/api/import/confirm")
    versteckt_id = next(
        c["id"] for c in client.get("/api/categories").get_json() if c["name"] == "Versteckt"
    )

    rows = client.get(f"/api/transactions?category_id={versteckt_id}").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "OnlyFans.com Payment"


def test_hidden_transaction_excluded_from_summary_totals(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "OnlyFans.com Payment", "-19.99"),
        ("02.03.2026", "Migros Zuerich", "-45.90"),
    ])
    client.post("/api/scan")
    client.post("/api/import/confirm")

    summary = client.get("/api/summary?start=2026-01-01&end=2026-12-31").get_json()

    assert summary["total_expense"] == -4590
    categories = {c["category"] for c in summary["by_category"]}
    assert "Versteckt" not in categories


def test_hidden_transaction_still_counted_in_imported_files_total(client, tmp_path):
    # The whole point of imported-files: reconcile the parsed total against
    # the original PDF/CSV, which must still include hidden bookings —
    # otherwise a hidden row would look like the parser silently dropped it.
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "OnlyFans.com Payment", "-19.99"),
        ("02.03.2026", "Migros Zuerich", "-45.90"),
    ])
    client.post("/api/scan")

    rows = client.get("/api/imported-files").get_json()

    assert len(rows) == 1
    assert rows[0]["transaction_count"] == 2
    assert rows[0]["expense_cents"] == -1999 - 4590

    # Confirming the (non-hidden) pending row must not change that total —
    # the hidden row was never in pending_transactions to begin with.
    client.post("/api/import/confirm")
    rows = client.get("/api/imported-files").get_json()
    assert rows[0]["transaction_count"] == 2
    assert rows[0]["expense_cents"] == -1999 - 4590
