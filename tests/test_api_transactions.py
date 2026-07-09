import pytest

from server.app import create_app
from server.db import get_connection


@pytest.fixture
def client_with_data(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()

    # Deliberately fictional merchant names (not "Migros"/"Coop" etc.): these
    # tests rely on the rows landing in "Unkategorisiert" by default, which
    # only holds for descriptions that don't match any of init_db()'s
    # pre-seeded DEFAULT_CATEGORY_RULES.
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Musterladen Zürich;-45.90;CHF\n"
        "05.04.2026;Lohn April;5200.00;CHF\n"
        "10.04.2026;Beispielmarkt Zürich;-30.00;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")
    return client


def test_transactions_filtered_by_date_range(client_with_data):
    rows = client_with_data.get("/api/transactions?start=2026-04-01&end=2026-04-30").get_json()

    dates = {r["date"] for r in rows}
    assert dates == {"2026-04-05", "2026-04-10"}


def test_transactions_filtered_by_text_search(client_with_data):
    rows = client_with_data.get("/api/transactions?q=Musterladen").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Musterladen Zürich"


def test_transactions_filtered_by_amount_range(client_with_data):
    rows = client_with_data.get("/api/transactions?min_amount=-40&max_amount=-1").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Beispielmarkt Zürich"


def test_transactions_filtered_by_source(client_with_data):
    rows = client_with_data.get("/api/transactions?source=test").get_json()

    assert len(rows) == 3
    assert all(r["source"] == "test" for r in rows)


def test_sources_endpoint_lists_distinct_sources(client_with_data):
    sources = client_with_data.get("/api/sources").get_json()

    assert sources == ["test"]


def test_transactions_filtered_by_type_income(client_with_data):
    rows = client_with_data.get("/api/transactions?type=income").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Lohn April"


def test_transactions_filtered_by_type_expense(client_with_data):
    rows = client_with_data.get("/api/transactions?type=expense").get_json()

    descriptions = {r["description"] for r in rows}
    assert descriptions == {"Musterladen Zürich", "Beispielmarkt Zürich"}


def test_summary_respects_type_filter(client_with_data):
    summary = client_with_data.get("/api/summary?type=expense").get_json()

    assert summary["total_income"] == 0
    assert summary["total_expense"] == -7590


def test_summary_totals_and_by_month(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    assert summary["total_income"] == 520000
    assert summary["total_expense"] == -7590
    months = {m["month"] for m in summary["by_month"]}
    assert months == {"2026-03", "2026-04"}


def test_summary_by_category_groups_expenses(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    unkategorisiert = next(
        (c for c in summary["by_category"] if c["category"] == "Unkategorisiert"), None
    )
    assert unkategorisiert is not None
    assert unkategorisiert["amount_cents"] == 7590


def test_transactions_with_malformed_min_amount_returns_400(client_with_data):
    response = client_with_data.get("/api/transactions?min_amount=abc")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_summary_with_malformed_max_amount_returns_400(client_with_data):
    response = client_with_data.get("/api/summary?max_amount=xyz")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_summary_respects_category_filter(client_with_data):
    # None of the seeded transactions matches any category_rules, so they all
    # land in "Unkategorisiert". Filtering by an unrelated category must
    # therefore return the empty-result shape, proving category_id narrows
    # the summary (previously only start/end did).
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lebensmittel_id = categories["Lebensmittel"]

    summary = client_with_data.get(f"/api/summary?category_id={lebensmittel_id}").get_json()

    assert summary == {"total_income": 0, "total_expense": 0, "by_category": [], "by_month": []}


@pytest.fixture
def client_with_settlement(tmp_path):
    # A checking-account import (Lohn + Migros) plus a credit-card
    # statement's settlement pair: the card statement's own payment line
    # (a credit) and the checking account's matching collection debit —
    # both must be excluded from totals, or the Migros purchase (already
    # counted once) effectively gets counted again via the settlement.
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()
    conn = get_connection(tmp_path / "test.db")
    categories = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}

    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES (?, ?, ?, 'CHF', ?, 'demo', 0)",
        ("2026-04-01", "Lohn April", 520000, categories["Lohn/Einkommen"]),
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES (?, ?, ?, 'CHF', ?, 'demo', 0)",
        ("2026-04-05", "Migros Zürich", -8500, categories["Lebensmittel"]),
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES (?, ?, ?, 'CHF', ?, 'demo', 0)",
        ("2026-04-25", "IHRE ZAHLUNG - BESTEN DANK", 8500, categories["Kreditkarten-Ausgleich"]),
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES (?, ?, ?, 'CHF', ?, 'demo', 0)",
        ("2026-04-28", "Swisscard AECS GmbH", -8500, categories["Kreditkarten-Ausgleich"]),
    )
    conn.commit()
    conn.close()
    return client


def test_summary_excludes_kreditkarten_ausgleich_from_totals(client_with_settlement):
    summary = client_with_settlement.get("/api/summary").get_json()

    assert summary["total_income"] == 520000  # Lohn only, not the +85 settlement credit
    assert summary["total_expense"] == -8500  # Migros only, not the -85 settlement debit


def test_summary_excludes_kreditkarten_ausgleich_from_by_category(client_with_settlement):
    summary = client_with_settlement.get("/api/summary").get_json()

    categories_in_breakdown = {c["category"] for c in summary["by_category"]}
    assert "Kreditkarten-Ausgleich" not in categories_in_breakdown


def test_summary_excludes_kreditkarten_ausgleich_from_by_month(client_with_settlement):
    summary = client_with_settlement.get("/api/summary").get_json()

    april = next(m for m in summary["by_month"] if m["month"] == "2026-04")
    # Lohn (+520000) + Migros (-8500) only — the settlement pair (+8500/-8500,
    # which cancel out anyway) must not be included even though it nets to
    # zero, since a partial payment wouldn't net to zero and must still be
    # excluded on principle, not by coincidence.
    assert april["amount_cents"] == 520000 - 8500


def test_transactions_still_lists_kreditkarten_ausgleich_rows(client_with_settlement):
    # Excluded from aggregates, but never hidden from the ledger itself —
    # Kevin must still be able to reconcile his checking account balance.
    rows = client_with_settlement.get("/api/transactions").get_json()

    descriptions = {r["description"] for r in rows}
    assert "IHRE ZAHLUNG - BESTEN DANK" in descriptions
    assert "Swisscard AECS GmbH" in descriptions
