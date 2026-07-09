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
