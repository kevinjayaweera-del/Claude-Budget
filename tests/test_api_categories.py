import pytest

from server.app import create_app
from server.db import get_connection


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    app.config["_db_path"] = tmp_path / "test.db"
    return app.test_client()


def _category_id(client, name):
    return {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}[name]


def test_create_category(client):
    response = client.post("/api/categories", json={"name": "Haustier"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["name"] == "Haustier"
    assert "Haustier" in {c["name"] for c in client.get("/api/categories").get_json()}


def test_create_category_with_empty_name_returns_400(client):
    response = client.post("/api/categories", json={"name": "   "})

    assert response.status_code == 400


def test_create_category_with_duplicate_name_returns_400(client):
    response = client.post("/api/categories", json={"name": "Lebensmittel"})

    assert response.status_code == 400


def test_create_category_with_missing_body_returns_400(client):
    response = client.post("/api/categories")

    assert response.status_code == 400


def test_rename_category(client):
    category_id = _category_id(client, "Freizeit")

    response = client.put(f"/api/categories/{category_id}", json={"name": "Hobbys"})

    assert response.status_code == 200
    names = {c["name"] for c in client.get("/api/categories").get_json()}
    assert "Hobbys" in names
    assert "Freizeit" not in names


def test_rename_category_to_existing_name_returns_400(client):
    category_id = _category_id(client, "Freizeit")

    response = client.put(f"/api/categories/{category_id}", json={"name": "Lebensmittel"})

    assert response.status_code == 400


def test_rename_nonexistent_category_returns_404(client):
    response = client.put("/api/categories/99999", json={"name": "Irgendwas"})

    assert response.status_code == 404


def test_delete_category_without_dependencies_succeeds(client):
    category_id = _category_id(client, "Freizeit")
    # Remove the pre-seeded rules pointing at it first, so it's genuinely unused.
    conn = get_connection(client.application.config["_db_path"])
    conn.execute("DELETE FROM category_rules WHERE category_id = ?", (category_id,))
    conn.commit()
    conn.close()

    response = client.delete(f"/api/categories/{category_id}")

    assert response.status_code == 200
    assert "Freizeit" not in {c["name"] for c in client.get("/api/categories").get_json()}


def test_delete_category_with_dependencies_requires_confirmation(client):
    category_id = _category_id(client, "Lebensmittel")  # has pre-seeded rules

    response = client.delete(f"/api/categories/{category_id}")

    assert response.status_code == 409
    body = response.get_json()
    assert body["rule_count"] > 0


def test_delete_category_with_confirmation_reassigns_dependencies(client, tmp_path):
    (tmp_path / "statements" / "t.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    category_id = _category_id(client, "Lebensmittel")
    unkategorisiert_id = _category_id(client, "Unkategorisiert")

    response = client.delete(f"/api/categories/{category_id}?confirm=true")

    assert response.status_code == 200
    assert "Lebensmittel" not in {c["name"] for c in client.get("/api/categories").get_json()}

    transactions = client.get("/api/transactions").get_json()
    assert transactions[0]["category_id"] == unkategorisiert_id

    rules = client.get("/api/rules").get_json()
    migros_rule = next(r for r in rules if r["keyword"] == "migros")
    assert migros_rule["category_id"] == unkategorisiert_id


def test_delete_nonexistent_category_returns_404(client):
    response = client.delete("/api/categories/99999?confirm=true")

    assert response.status_code == 404


def test_delete_unkategorisiert_is_forbidden(client):
    category_id = _category_id(client, "Unkategorisiert")

    response = client.delete(f"/api/categories/{category_id}?confirm=true")

    assert response.status_code == 400


def test_rename_unkategorisiert_is_forbidden(client):
    # Regression test: import_service.scan_and_parse, confirm_import, and
    # update_transaction_category all look this category up by this exact
    # literal name at runtime (not just at seed time) — renaming it used to
    # silently break every subsequent import scan.
    category_id = _category_id(client, "Unkategorisiert")

    response = client.put(f"/api/categories/{category_id}", json={"name": "Egal"})

    assert response.status_code == 400
    assert "Unkategorisiert" in {c["name"] for c in client.get("/api/categories").get_json()}
