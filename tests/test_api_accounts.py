import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def _scan_one_file(client, tmp_path, folder="ZKB"):
    (tmp_path / "statements" / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "statements" / folder / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")


def test_list_accounts_starts_empty(client):
    assert client.get("/api/accounts").get_json() == []


def test_list_accounts_after_scan(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")

    accounts = client.get("/api/accounts").get_json()

    assert len(accounts) == 1
    assert accounts[0]["name"] == "ZKB"
    assert accounts[0]["source_key"] == "ZKB"


def test_rename_account(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")
    account_id = client.get("/api/accounts").get_json()[0]["id"]

    response = client.put(f"/api/accounts/{account_id}", json={"name": "ZKB Hauptkonto"})

    assert response.status_code == 200
    accounts = client.get("/api/accounts").get_json()
    assert accounts[0]["name"] == "ZKB Hauptkonto"
    # source_key (the import-matching identifier) never changes on rename.
    assert accounts[0]["source_key"] == "ZKB"


def test_rename_account_survives_a_second_scan_of_the_same_source(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")
    account_id = client.get("/api/accounts").get_json()[0]["id"]
    client.put(f"/api/accounts/{account_id}", json={"name": "ZKB Hauptkonto"})

    (tmp_path / "statements" / "ZKB" / "second.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n02.03.2026;Coop Bern;-12.30;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")

    accounts = client.get("/api/accounts").get_json()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "ZKB Hauptkonto"


def test_rename_account_not_found_returns_404(client):
    response = client.put("/api/accounts/999", json={"name": "Irgendwas"})

    assert response.status_code == 404


def test_delete_unused_account(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")
    account_id = client.get("/api/accounts").get_json()[0]["id"]
    # Discard the pending row (not confirmed into a real transaction) so
    # the account is genuinely unused, without wiping the accounts table
    # itself the way /api/database/reset-imports would.
    pending_id = client.get("/api/pending").get_json()[0]["id"]
    client.delete(f"/api/pending/{pending_id}")

    response = client.delete(f"/api/accounts/{account_id}")

    assert response.status_code == 200
    assert client.get("/api/accounts").get_json() == []


def test_delete_account_in_use_returns_409(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")
    client.post("/api/import/confirm")
    account_id = client.get("/api/accounts").get_json()[0]["id"]

    response = client.delete(f"/api/accounts/{account_id}")

    assert response.status_code == 409
    assert client.get("/api/accounts").get_json() != []


def test_filter_transactions_by_account_id(client, tmp_path):
    _scan_one_file(client, tmp_path, "ZKB")
    (tmp_path / "statements" / "Cornercard").mkdir(parents=True, exist_ok=True)
    (tmp_path / "statements" / "Cornercard" / "cc.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n02.03.2026;Restaurant Zuerich;-30.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    accounts = {a["name"]: a["id"] for a in client.get("/api/accounts").get_json()}

    filtered = client.get(f"/api/transactions?account_id={accounts['ZKB']}").get_json()

    assert len(filtered) == 1
    assert filtered[0]["description"] == "Migros Zürich"
