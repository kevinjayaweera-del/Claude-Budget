import pytest

from server.app import create_app
from server.db import get_connection


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    app.config["_db_path"] = tmp_path / "test.db"
    return app.test_client()


def _confirm_one_transaction(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    return client.get("/api/transactions").get_json()[0]["id"]


def test_list_tags_starts_empty(client):
    assert client.get("/api/tags").get_json() == []


def test_create_tag(client):
    response = client.post("/api/tags", json={"name": "Urlaub"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["name"] == "Urlaub"
    assert "Urlaub" in {t["name"] for t in client.get("/api/tags").get_json()}


def test_create_tag_with_empty_name_returns_400(client):
    response = client.post("/api/tags", json={"name": "   "})

    assert response.status_code == 400


def test_create_tag_with_duplicate_name_returns_400(client):
    client.post("/api/tags", json={"name": "Urlaub"})

    response = client.post("/api/tags", json={"name": "Urlaub"})

    assert response.status_code == 400


def test_rename_tag(client):
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    response = client.put(f"/api/tags/{tag_id}", json={"name": "Ferien"})

    assert response.status_code == 200
    names = {t["name"] for t in client.get("/api/tags").get_json()}
    assert names == {"Ferien"}


def test_rename_tag_not_found_returns_404(client):
    response = client.put("/api/tags/999", json={"name": "Ferien"})

    assert response.status_code == 404


def test_delete_tag_removes_it_and_its_assignments(client, tmp_path):
    txn_id = _confirm_one_transaction(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]
    client.post(f"/api/transactions/{txn_id}/tags", json={"tag_id": tag_id})

    response = client.delete(f"/api/tags/{tag_id}")

    assert response.status_code == 200
    assert client.get("/api/tags").get_json() == []
    txn = client.get("/api/transactions").get_json()[0]
    assert txn["tags"] == []


def test_assign_and_unassign_tag_on_transaction(client, tmp_path):
    txn_id = _confirm_one_transaction(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    assign_response = client.post(f"/api/transactions/{txn_id}/tags", json={"tag_id": tag_id})
    assert assign_response.status_code == 200
    txn = client.get("/api/transactions").get_json()[0]
    assert txn["tags"] == [{"id": tag_id, "name": "Urlaub"}]

    unassign_response = client.delete(f"/api/transactions/{txn_id}/tags/{tag_id}")
    assert unassign_response.status_code == 200
    txn = client.get("/api/transactions").get_json()[0]
    assert txn["tags"] == []


def test_assign_tag_twice_is_idempotent(client, tmp_path):
    txn_id = _confirm_one_transaction(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    client.post(f"/api/transactions/{txn_id}/tags", json={"tag_id": tag_id})
    response = client.post(f"/api/transactions/{txn_id}/tags", json={"tag_id": tag_id})

    assert response.status_code == 200
    txn = client.get("/api/transactions").get_json()[0]
    assert txn["tags"] == [{"id": tag_id, "name": "Urlaub"}]


def test_assign_tag_to_missing_transaction_returns_404(client):
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    response = client.post("/api/transactions/999/tags", json={"tag_id": tag_id})

    assert response.status_code == 404


def test_filter_transactions_by_tag_id(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Bern;-12.30;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    txns = client.get("/api/transactions").get_json()
    tag_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]
    client.post(f"/api/transactions/{txns[0]['id']}/tags", json={"tag_id": tag_id})

    filtered = client.get(f"/api/transactions?tag_id={tag_id}").get_json()

    assert len(filtered) == 1
    assert filtered[0]["id"] == txns[0]["id"]
