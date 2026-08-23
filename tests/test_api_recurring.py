import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client(), tmp_path


def _import_csv(client, tmp_path, rows):
    lines = ["Datum;Buchungstext;Betrag;Währung"] + rows
    (tmp_path / "statements" / "test.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    client.post("/api/scan")
    client.post("/api/import/confirm")


def test_recurring_detects_merchant_across_three_months(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2026;Netflix.com 41.90 CHF;-14.90;CHF",
        "05.02.2026;Netflix.com 41.90 CHF;-14.90;CHF",
        "05.03.2026;Netflix.com 41.90 CHF;-14.90;CHF",
    ])

    recurring = c.get("/api/recurring?lookback_days=3650").get_json()

    assert len(recurring) == 1
    entry = recurring[0]
    assert entry["occurrences"] == 3
    assert entry["distinct_months"] == 3
    assert entry["avg_amount_cents"] == 1490
    assert entry["first_date"] == "2026-01-05"
    assert entry["last_date"] == "2026-03-05"


def test_recurring_requires_at_least_three_distinct_months(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2026;Spotify AB;-12.90;CHF",
        "05.02.2026;Spotify AB;-12.90;CHF",
    ])

    recurring = c.get("/api/recurring?lookback_days=3650").get_json()

    assert recurring == []


def test_recurring_excludes_inconsistent_amounts(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2026;Irregular Shop;-10.00;CHF",
        "05.02.2026;Irregular Shop;-40.00;CHF",
        "05.03.2026;Irregular Shop;-12.00;CHF",
    ])

    recurring = c.get("/api/recurring?lookback_days=3650").get_json()

    assert recurring == []


def test_recurring_respects_lookback_days(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2020;Old Gym AG;-49.00;CHF",
        "05.02.2020;Old Gym AG;-49.00;CHF",
        "05.03.2020;Old Gym AG;-49.00;CHF",
    ])

    recent_only = c.get("/api/recurring?lookback_days=30").get_json()
    full_history = c.get("/api/recurring?lookback_days=3650").get_json()

    assert recent_only == []
    assert len(full_history) == 1


def test_recurring_entry_merchant_key_matches_transactions_filter(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2026;Fitnesspark AG Nr. xxxx 1234;-89.00;CHF",
        "05.02.2026;Fitnesspark AG Nr. xxxx 5678;-89.00;CHF",
        "05.03.2026;Fitnesspark AG Nr. xxxx 9012;-89.00;CHF",
    ])

    recurring = c.get("/api/recurring?lookback_days=3650").get_json()
    assert len(recurring) == 1
    key = recurring[0]["merchant_key"]

    matched = c.get("/api/transactions", query_string={"merchant_key": key}).get_json()
    assert len(matched) == 3


def test_recurring_ignores_income(client):
    c, tmp_path = client
    _import_csv(c, tmp_path, [
        "05.01.2026;Lohn Monatlich;5000.00;CHF",
        "05.02.2026;Lohn Monatlich;5000.00;CHF",
        "05.03.2026;Lohn Monatlich;5000.00;CHF",
    ])

    recurring = c.get("/api/recurring?lookback_days=3650").get_json()

    assert recurring == []
