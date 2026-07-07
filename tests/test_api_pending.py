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


def _write_sample_csv(tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )


def test_scan_endpoint_returns_new_pending_count(client, tmp_path):
    _write_sample_csv(tmp_path)

    response = client.post("/api/scan")

    assert response.status_code == 200
    assert response.get_json() == {"new_pending": 1}


def test_pending_list_reflects_scanned_rows(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    rows = client.get("/api/pending").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Migros Zürich"
    assert rows[0]["amount_cents"] == -4590


def test_update_pending_row_changes_category(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put(f"/api/pending/{pending_id}", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        "category_id": categories["Lebensmittel"],
    })

    assert response.status_code == 200
    updated = client.get("/api/pending").get_json()[0]
    assert updated["category_id"] == categories["Lebensmittel"]


def test_update_pending_row_with_missing_body_returns_400(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.put(f"/api/pending/{pending_id}")

    assert response.status_code == 400


def test_update_pending_row_with_missing_field_returns_400(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.put(f"/api/pending/{pending_id}", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        # category_id intentionally omitted
    })

    assert response.status_code == 400


def test_update_nonexistent_pending_row_returns_404(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put("/api/pending/99999", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        "category_id": categories["Lebensmittel"],
    })

    assert response.status_code == 404


def test_delete_nonexistent_pending_row_returns_404(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    response = client.delete("/api/pending/99999")

    assert response.status_code == 404


def test_delete_pending_row_removes_it(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.delete(f"/api/pending/{pending_id}")

    assert response.status_code == 200
    assert client.get("/api/pending").get_json() == []


def test_confirm_import_moves_rows_to_transactions_and_clears_pending(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    response = client.post("/api/import/confirm")

    assert response.get_json() == {"imported": 1}
    assert client.get("/api/pending").get_json() == []
