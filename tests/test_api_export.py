import json

import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def test_export_returns_json_attachment(client):
    response = client.get("/api/export")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert "attachment" in response.headers.get("Content-Disposition", "")


def test_export_contains_categories_and_settings(client):
    response = client.get("/api/export")

    data = json.loads(response.data)
    assert len(data["categories"]) > 0
    assert data["settings"]["auto_categorize_enabled"] == "true"
    assert data["transactions"] == []
    assert data["budgets"] == []


def test_export_contains_confirmed_transactions(client, tmp_path):
    (tmp_path / "statements" / "t.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Musterladen Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")

    data = json.loads(client.get("/api/export").data)

    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["description"] == "Musterladen Zürich"


def test_export_contains_accounts_tags_and_transaction_tags(client, tmp_path):
    # Regression test for a low-severity bug found in the pre-release
    # audit: the export omitted accounts and tags/transaction_tags
    # entirely (unlike its deliberately-documented exclusion of
    # pending_transactions/imported_files), so a restore built from it
    # would silently lose every custom account name and tag assignment.
    (tmp_path / "statements" / "ZKB" / "t.csv").parent.mkdir(parents=True)
    (tmp_path / "statements" / "ZKB" / "t.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Musterladen Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    txn_id = client.get("/api/transactions").get_json()[0]["id"]
    tag_id = client.post("/api/tags", json={"name": "Testtag"}).get_json()["id"]
    client.post(f"/api/transactions/{txn_id}/tags", json={"tag_id": tag_id})

    data = json.loads(client.get("/api/export").data)

    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["source_key"] == "ZKB"
    assert len(data["tags"]) == 1
    assert data["tags"][0]["name"] == "Testtag"
    assert data["transaction_tags"] == [{"transaction_id": txn_id, "tag_id": tag_id}]
