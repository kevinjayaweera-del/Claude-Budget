import pytest

from server.app import create_app
from server.db import DEFAULT_CATEGORIES


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def _write_sample_csv(tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Musterladen Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )


def test_reset_database_clears_transactions_and_pending(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    client.post("/api/import/confirm")
    assert len(client.get("/api/transactions").get_json()) == 1

    response = client.post("/api/database/reset")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert client.get("/api/transactions").get_json() == []
    assert client.get("/api/pending").get_json() == []


def test_reset_database_reseeds_default_categories(client):
    client.post("/api/database/reset")

    names = {c["name"] for c in client.get("/api/categories").get_json()}
    assert names == set(DEFAULT_CATEGORIES)


def test_reset_database_allows_rescanning_previously_imported_file(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    client.post("/api/import/confirm")

    client.post("/api/database/reset")
    result = client.post("/api/scan").get_json()

    assert result == {"new_pending": 1, "duplicates_skipped": 0}


def test_reset_database_with_backup_creates_backup_file(client):
    response = client.post("/api/database/reset?backup=true")

    assert response.status_code == 200
    db_path = client.application.config["DB_PATH"]
    backup_dir = db_path.parent / "backups"
    assert backup_dir.exists()
    assert list(backup_dir.glob("*.db"))


def test_reset_imports_clears_transactions_and_pending(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    client.post("/api/import/confirm")
    assert len(client.get("/api/transactions").get_json()) == 1

    response = client.post("/api/database/reset-imports")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert client.get("/api/transactions").get_json() == []
    assert client.get("/api/pending").get_json() == []


def test_reset_imports_allows_rescanning_previously_imported_file(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    client.post("/api/import/confirm")

    client.post("/api/database/reset-imports")
    result = client.post("/api/scan").get_json()

    assert result == {"new_pending": 1, "duplicates_skipped": 0}


def test_reset_imports_keeps_learned_rules(client, tmp_path):
    # A corrected pending row learns a new rule (see test_api_pending.py's
    # correction tests) — that's exactly what this reset must preserve.
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    client.put(
        f"/api/pending/{pending[0]['id']}",
        json={
            "date": pending[0]["date"],
            "description": pending[0]["description"],
            "amount_cents": pending[0]["amount_cents"],
            "currency": pending[0]["currency"],
            "category_id": categories["Lebensmittel"],
        },
    )
    client.post("/api/import/confirm")
    rules_before = client.get("/api/rules").get_json()
    assert any(not r["is_seeded"] for r in rules_before)

    client.post("/api/database/reset-imports")

    rules_after = client.get("/api/rules").get_json()
    assert rules_after == rules_before


def test_reset_imports_keeps_categories_budgets_and_settings(client):
    client.post("/api/categories", json={"name": "Haustier"})
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    client.put(
        f"/api/budgets/{categories['Lebensmittel']}",
        json={"monthly_limit_cents": 50000},
    )
    client.put("/api/settings", json={"auto_categorize_enabled": False})

    client.post("/api/database/reset-imports")

    names = {c["name"] for c in client.get("/api/categories").get_json()}
    assert "Haustier" in names
    budgets = client.get("/api/budgets").get_json()
    assert any(b["category_id"] == categories["Lebensmittel"] for b in budgets)
    assert client.get("/api/settings").get_json()["auto_categorize_enabled"] is False


def test_reset_imports_with_backup_creates_backup_file(client):
    response = client.post("/api/database/reset-imports?backup=true")

    assert response.status_code == 200
    db_path = client.application.config["DB_PATH"]
    backup_dir = db_path.parent / "backups"
    assert backup_dir.exists()
    assert list(backup_dir.glob("*.db"))
