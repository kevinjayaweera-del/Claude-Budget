import pytest

from server.app import create_app
from server.db import get_connection


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


def _write_foreign_currency_csv(tmp_path):
    (tmp_path / "statements" / "foreign.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Amazon.de;-20.00;EUR\n",
        encoding="utf-8-sig",
    )


def _write_two_row_csv(tmp_path):
    (tmp_path / "statements" / "two_rows.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Basel;-12.30;CHF\n",
        encoding="utf-8-sig",
    )


def test_scan_endpoint_returns_new_pending_count(client, tmp_path):
    _write_sample_csv(tmp_path)

    response = client.post("/api/scan")

    assert response.status_code == 200
    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 0}


def test_scan_endpoint_reports_duplicates_skipped_on_rescan_under_new_filename(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    # Different byte content (extra row) than test.csv, so file-hash dedup
    # doesn't short-circuit before transaction-level dedup runs — mirrors a
    # re-downloaded statement whose export tool renames/re-timestamps the file.
    (tmp_path / "statements" / "test_redownloaded.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Basel;-12.30;CHF\n",
        encoding="utf-8-sig",
    )
    response = client.post("/api/scan")

    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 1}
    assert len(client.get("/api/pending").get_json()) == 2


def test_scan_endpoint_dry_run_reports_counts_without_creating_pending_rows(client, tmp_path):
    _write_sample_csv(tmp_path)

    response = client.post("/api/scan?dry_run=true")

    assert response.status_code == 200
    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 0}
    assert client.get("/api/pending").get_json() == []


def test_scan_endpoint_dry_run_then_real_scan_still_creates_pending_rows(client, tmp_path):
    _write_sample_csv(tmp_path)

    client.post("/api/scan?dry_run=true")
    response = client.post("/api/scan")

    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 0}
    assert len(client.get("/api/pending").get_json()) == 1


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


def test_confirm_import_carries_account_id_to_transactions(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_account_id = get_connection(tmp_path / "test.db").execute(
        "SELECT account_id FROM pending_transactions"
    ).fetchone()["account_id"]
    assert pending_account_id is not None

    client.post("/api/import/confirm")

    txn_account_id = get_connection(tmp_path / "test.db").execute(
        "SELECT account_id FROM transactions"
    ).fetchone()["account_id"]
    assert txn_account_id == pending_account_id


def test_confirm_import_preserves_non_chf_currency(client, tmp_path):
    """Regression test for the currency-clobbering bug: the PUT that saves a
    pending row's edits must preserve whatever currency it is sent (this is
    the invariant the frontend fix relies on), and confirm_import must carry
    that currency through into the transactions table unchanged."""
    _write_foreign_currency_csv(tmp_path)
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    assert pending["currency"] == "EUR"
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    put_response = client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": "EUR",
        "category_id": categories["Sonstiges"] if "Sonstiges" in categories else list(categories.values())[0],
    })
    assert put_response.status_code == 200

    response = client.post("/api/import/confirm")
    assert response.get_json() == {"imported": 1}

    transactions = client.get("/api/transactions").get_json()
    assert len(transactions) == 1
    assert transactions[0]["currency"] == "EUR"


def test_confirm_import_with_explicit_ids_only_imports_selected_rows(client, tmp_path):
    _write_two_row_csv(tmp_path)
    client.post("/api/scan")
    pending_rows = client.get("/api/pending").get_json()
    assert len(pending_rows) == 2
    first_id = pending_rows[0]["id"]
    second_id = pending_rows[1]["id"]

    response = client.post("/api/import/confirm", json={"ids": [first_id]})

    assert response.get_json() == {"imported": 1}
    transactions = client.get("/api/transactions").get_json()
    assert len(transactions) == 1
    assert transactions[0]["description"] == pending_rows[0]["description"]

    remaining_pending = client.get("/api/pending").get_json()
    assert len(remaining_pending) == 1
    assert remaining_pending[0]["id"] == second_id


def _rule_stats(db_path):
    conn = get_connection(db_path)
    rows = conn.execute("SELECT keyword, match_count, correction_count FROM category_rules").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_confirm_import_reinforces_rule_when_suggestion_accepted(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    migros_before = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")

    client.post("/api/import/confirm")  # accept the suggested category as-is

    migros_after = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")
    assert migros_after["match_count"] == migros_before["match_count"] + 1
    assert migros_after["correction_count"] == migros_before["correction_count"]

    transactions = client.get("/api/transactions").get_json()
    assert transactions[0]["description"] == "Migros Zürich"


def test_confirm_import_penalizes_old_rule_and_learns_new_one_on_correction(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    migros_before = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")

    # Correct the auto-suggested "Lebensmittel" to something else.
    client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": pending["currency"],
        "category_id": categories["Sonstiges"],
    })
    client.post("/api/import/confirm")

    migros_after = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")
    assert migros_after["correction_count"] == migros_before["correction_count"] + 1
    assert migros_after["match_count"] == migros_before["match_count"]  # unchanged, not rewarded

    transactions = client.get("/api/transactions").get_json()
    assert transactions[0]["category_id"] == categories["Sonstiges"]


def test_confirm_import_does_not_learn_a_rule_when_left_uncategorized(client, tmp_path):
    # A row with no rule match defaults to "Unkategorisiert" — if the user
    # confirms it as-is (declining to pick a real category), that must NOT
    # teach the categorizer "this keyword means Unkategorisiert". Otherwise,
    # leaving something unresolved trains the system to more confidently
    # predict "no category" for similar future bookings — the opposite of
    # what learning is for.
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;ZackigerHaendler;-12.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")

    client.post("/api/import/confirm")  # left as the default "Unkategorisiert"

    rules = _rule_stats(tmp_path / "test.db")
    assert not any(r["keyword"] == "zackigerhaendler" for r in rules)


def test_confirm_import_does_not_learn_a_rule_when_corrected_to_uncategorized(client, tmp_path):
    # Same principle when a suggested category is actively corrected BACK to
    # Unkategorisiert — still not a useful keyword-to-category signal.
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    migros_before = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")

    client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": pending["currency"],
        "category_id": categories["Unkategorisiert"],
    })
    client.post("/api/import/confirm")

    migros_after = next(r for r in _rule_stats(tmp_path / "test.db") if r["keyword"] == "migros")
    assert migros_after["correction_count"] == migros_before["correction_count"] + 1
    # No new rule keyed on this description's other words should target Unkategorisiert.
    unkategorisiert_id = categories["Unkategorisiert"]
    conn = get_connection(tmp_path / "test.db")
    poisoned = conn.execute(
        "SELECT COUNT(*) c FROM category_rules WHERE category_id = ? AND keyword != 'migros'",
        (unkategorisiert_id,),
    ).fetchone()["c"]
    conn.close()
    assert poisoned == 0


def test_confirm_import_sets_manually_corrected_flag(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": pending["currency"],
        "category_id": categories["Sonstiges"],
    })
    response = client.post("/api/import/confirm")
    assert response.get_json() == {"imported": 1}


def test_pending_list_includes_category_confidence(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    rows = client.get("/api/pending").get_json()

    assert rows[0]["category_confidence"] is not None
    assert 0.0 <= rows[0]["category_confidence"] <= 1.0
