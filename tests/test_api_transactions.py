import pytest

from server.app import create_app


@pytest.fixture
def client_with_data(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()

    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "05.04.2026;Lohn April;5200.00;CHF\n"
        "10.04.2026;Coop Zürich;-30.00;CHF\n",
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
    rows = client_with_data.get("/api/transactions?q=Migros").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Migros Zürich"


def test_transactions_filtered_by_amount_range(client_with_data):
    rows = client_with_data.get("/api/transactions?min_amount=-40&max_amount=-1").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Coop Zürich"


def test_transactions_filtered_by_source(client_with_data):
    rows = client_with_data.get("/api/transactions?source=test").get_json()

    assert len(rows) == 3
    assert all(r["source"] == "test" for r in rows)


def test_sources_endpoint_lists_distinct_sources(client_with_data):
    sources = client_with_data.get("/api/sources").get_json()

    assert sources == ["test"]


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
