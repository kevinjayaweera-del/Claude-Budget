import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def test_list_rules_includes_confidence_and_stats(client):
    rules = client.get("/api/rules").get_json()

    migros = next(r for r in rules if r["keyword"] == "migros")
    assert migros["category_name"] == "Lebensmittel"
    assert migros["match_count"] == 0
    assert migros["correction_count"] == 0
    assert migros["is_seeded"] == 1
    assert migros["confidence"] == pytest.approx(1 / 2)


def test_update_rule_changes_target_category(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put(f"/api/rules/{migros['id']}", json={"category_id": categories["Sonstiges"]})

    assert response.status_code == 200
    updated = next(r for r in client.get("/api/rules").get_json() if r["id"] == migros["id"])
    assert updated["category_id"] == categories["Sonstiges"]


def test_update_rule_with_missing_body_returns_400(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")

    response = client.put(f"/api/rules/{migros['id']}")

    assert response.status_code == 400


def test_update_nonexistent_rule_returns_404(client):
    response = client.put("/api/rules/99999", json={"category_id": 1})

    assert response.status_code == 404


def test_delete_rule_removes_it(client):
    rules = client.get("/api/rules").get_json()
    migros = next(r for r in rules if r["keyword"] == "migros")

    response = client.delete(f"/api/rules/{migros['id']}")

    assert response.status_code == 200
    remaining = client.get("/api/rules").get_json()
    assert not any(r["id"] == migros["id"] for r in remaining)


def test_delete_nonexistent_rule_returns_404(client):
    response = client.delete("/api/rules/99999")

    assert response.status_code == 404
