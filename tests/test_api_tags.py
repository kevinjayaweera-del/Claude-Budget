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


def test_filter_transactions_by_multiple_tag_ids(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Bern;-12.30;CHF\n"
        "03.03.2026;Denner Basel;-8.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    txns = client.get("/api/transactions").get_json()
    urlaub_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]
    geschaeft_id = client.post("/api/tags", json={"name": "Geschäft"}).get_json()["id"]
    client.post(f"/api/transactions/{txns[0]['id']}/tags", json={"tag_id": urlaub_id})
    client.post(f"/api/transactions/{txns[1]['id']}/tags", json={"tag_id": geschaeft_id})

    # Comma-separated tag_id is the Budget tab's multi-select filter —
    # a transaction tagged with EITHER selected tag should match (OR, not AND).
    filtered = client.get(f"/api/transactions?tag_id={urlaub_id},{geschaeft_id}").get_json()

    assert {t["id"] for t in filtered} == {txns[0]["id"], txns[1]["id"]}


def test_bulk_assign_tag_by_date_range(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "09.05.2025;Hotel Bern;-200.00;CHF\n"
        "10.05.2025;Restaurant Bern;-45.00;CHF\n"
        "15.05.2025;Migros Zürich;-30.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    txns = {t["description"]: t["id"] for t in client.get("/api/transactions").get_json()}
    urlaub_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    response = client.post(
        "/api/transactions/tags/bulk",
        json={"tag_id": urlaub_id, "start": "2025-05-09", "end": "2025-05-10"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"tagged": 2}
    tagged_txns = client.get(f"/api/transactions?tag_id={urlaub_id}").get_json()
    assert {t["description"] for t in tagged_txns} == {"Hotel Bern", "Restaurant Bern"}
    # Outside the date range must stay untouched.
    migros = client.get("/api/transactions").get_json()
    migros_row = next(t for t in migros if t["description"] == "Migros Zürich")
    assert migros_row["tags"] == []


def test_bulk_assign_tag_is_idempotent_and_combinable_with_other_filters(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "09.05.2025;Hotel Bern;-200.00;CHF\n"
        "09.05.2025;Lohn Mai;3000.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    urlaub_id = client.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]

    # type=expense excludes the salary credit on the same day.
    first = client.post(
        "/api/transactions/tags/bulk",
        json={"tag_id": urlaub_id, "start": "2025-05-09", "end": "2025-05-09", "type": "expense"},
    ).get_json()
    second = client.post(
        "/api/transactions/tags/bulk",
        json={"tag_id": urlaub_id, "start": "2025-05-09", "end": "2025-05-09", "type": "expense"},
    ).get_json()

    assert first == {"tagged": 1}
    assert second == {"tagged": 1}  # re-running doesn't error or duplicate
    tagged = client.get(f"/api/transactions?tag_id={urlaub_id}").get_json()
    assert len(tagged) == 1
    assert tagged[0]["description"] == "Hotel Bern"


def test_bulk_assign_tag_requires_tag_id_and_date_range(client):
    missing_tag = client.post("/api/transactions/tags/bulk", json={"start": "2025-05-01", "end": "2025-05-02"})
    missing_dates = client.post("/api/transactions/tags/bulk", json={"tag_id": 1})

    assert missing_tag.status_code == 400
    assert missing_dates.status_code == 400


def test_bulk_assign_tag_with_unknown_tag_returns_404(client):
    response = client.post(
        "/api/transactions/tags/bulk", json={"tag_id": 999, "start": "2025-05-01", "end": "2025-05-02"}
    )

    assert response.status_code == 404
