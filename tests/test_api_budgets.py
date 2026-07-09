import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def _category_id(client, name):
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    return categories[name]


def test_list_budgets_is_empty_by_default(client):
    response = client.get("/api/budgets")

    assert response.status_code == 200
    assert response.get_json() == []


def test_set_budget_creates_it(client):
    lebensmittel_id = _category_id(client, "Lebensmittel")

    response = client.put(f"/api/budgets/{lebensmittel_id}", json={"monthly_limit_cents": 60000})

    assert response.status_code == 200
    budgets = client.get("/api/budgets").get_json()
    assert len(budgets) == 1
    assert budgets[0]["category_id"] == lebensmittel_id
    assert budgets[0]["category_name"] == "Lebensmittel"
    assert budgets[0]["monthly_limit_cents"] == 60000


def test_set_budget_updates_existing_limit(client):
    lebensmittel_id = _category_id(client, "Lebensmittel")
    client.put(f"/api/budgets/{lebensmittel_id}", json={"monthly_limit_cents": 60000})

    response = client.put(f"/api/budgets/{lebensmittel_id}", json={"monthly_limit_cents": 75000})

    assert response.status_code == 200
    budgets = client.get("/api/budgets").get_json()
    assert len(budgets) == 1
    assert budgets[0]["monthly_limit_cents"] == 75000


def test_set_budget_with_missing_body_returns_400(client):
    lebensmittel_id = _category_id(client, "Lebensmittel")

    response = client.put(f"/api/budgets/{lebensmittel_id}")

    assert response.status_code == 400


def test_set_budget_for_nonexistent_category_returns_404(client):
    response = client.put("/api/budgets/99999", json={"monthly_limit_cents": 1000})

    assert response.status_code == 404


def test_delete_budget_removes_it(client):
    lebensmittel_id = _category_id(client, "Lebensmittel")
    client.put(f"/api/budgets/{lebensmittel_id}", json={"monthly_limit_cents": 60000})

    response = client.delete(f"/api/budgets/{lebensmittel_id}")

    assert response.status_code == 200
    assert client.get("/api/budgets").get_json() == []


def test_delete_nonexistent_budget_returns_404(client):
    response = client.delete("/api/budgets/99999")

    assert response.status_code == 404
