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


def test_summary_by_period_defaults_to_month_granularity(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    periods = {p["period"] for p in summary["by_period"]}
    assert periods == {"2026-03", "2026-04"}
    april = next(p for p in summary["by_period"] if p["period"] == "2026-04")
    assert april["income_cents"] == 520000
    assert april["expense_cents"] == -3000
    assert april["net_cents"] == 517000


def test_summary_by_period_day_granularity(client_with_data):
    summary = client_with_data.get("/api/summary?granularity=day").get_json()

    periods = {p["period"] for p in summary["by_period"]}
    assert periods == {"2026-03-01", "2026-04-05", "2026-04-10"}


def test_summary_by_period_year_granularity(client_with_data):
    summary = client_with_data.get("/api/summary?granularity=year").get_json()

    periods = {p["period"] for p in summary["by_period"]}
    assert periods == {"2026"}
    year = summary["by_period"][0]
    assert year["income_cents"] == 520000
    assert year["expense_cents"] == -7590


def test_summary_by_period_quarter_granularity(client_with_data):
    summary = client_with_data.get("/api/summary?granularity=quarter").get_json()

    periods = {p["period"] for p in summary["by_period"]}
    assert periods == {"2026-Q1", "2026-Q2"}


def test_summary_by_period_week_granularity(client_with_data):
    summary = client_with_data.get("/api/summary?granularity=week").get_json()

    assert len(summary["by_period"]) == 3
    assert all("-W" in p["period"] for p in summary["by_period"])


def test_summary_invalid_granularity_returns_400(client_with_data):
    response = client_with_data.get("/api/summary?granularity=fortnight")

    assert response.status_code == 400


def test_summary_by_category_groups_expenses(client_with_data):
    summary = client_with_data.get("/api/summary").get_json()

    unkategorisiert = next(
        (c for c in summary["by_category"] if c["category"] == "Unkategorisiert"), None
    )
    assert unkategorisiert is not None
    assert unkategorisiert["amount_cents"] == 7590


def test_summary_by_tag_groups_expenses(client_with_data):
    # A transaction with two tags counts in full under each — filtering the
    # ledger by either tag would show the same amount, so the breakdown
    # shouldn't split it between them.
    rows = client_with_data.get("/api/transactions").get_json()
    musterladen = next(r for r in rows if r["description"] == "Musterladen Zürich")
    beispielmarkt = next(r for r in rows if r["description"] == "Beispielmarkt Zürich")
    urlaub_id = client_with_data.post("/api/tags", json={"name": "Urlaub"}).get_json()["id"]
    einmalig_id = client_with_data.post("/api/tags", json={"name": "Einmalig"}).get_json()["id"]
    client_with_data.post(f"/api/transactions/{musterladen['id']}/tags", json={"tag_id": urlaub_id})
    client_with_data.post(f"/api/transactions/{musterladen['id']}/tags", json={"tag_id": einmalig_id})
    client_with_data.post(f"/api/transactions/{beispielmarkt['id']}/tags", json={"tag_id": einmalig_id})

    by_tag = {t["tag"]: t["amount_cents"] for t in client_with_data.get("/api/summary").get_json()["by_tag"]}

    assert by_tag == {"Urlaub": 4590, "Einmalig": 4590 + 3000}


def test_summary_by_category_income_groups_income(client_with_data):
    # "Lohn April" matches the default "Lohn/Einkommen" category rule (see
    # client_with_data's docstring) — by_category_income should surface it
    # with its positive amount, mirroring by_category's expense breakdown.
    summary = client_with_data.get("/api/summary").get_json()

    lohn = next(
        (c for c in summary["by_category_income"] if c["category"] == "Lohn/Einkommen"), None
    )
    assert lohn is not None
    assert lohn["amount_cents"] == 520000
    # Expense-only categories must not leak into the income breakdown.
    assert all(c["amount_cents"] > 0 for c in summary["by_category_income"])


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

    assert summary == {
        "total_income": 0, "total_expense": 0, "by_category": [], "by_category_income": [],
        "by_merchant": [], "by_tag": [], "by_month": [], "by_period": [],
    }


def test_transactions_filtered_by_multiple_category_ids(client_with_data):
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lebensmittel_id = categories["Lebensmittel"]
    unkategorisiert_id = categories["Unkategorisiert"]
    rows = client_with_data.get("/api/transactions").get_json()
    musterladen = next(r for r in rows if r["description"] == "Musterladen Zürich")
    client_with_data.put(f"/api/transactions/{musterladen['id']}", json={"category_id": lebensmittel_id})

    # Comma-separated category_id is the Budget tab's multi-select filter —
    # a transaction in EITHER selected category should match (OR, not AND).
    filtered = client_with_data.get(
        f"/api/transactions?category_id={lebensmittel_id},{unkategorisiert_id}"
    ).get_json()

    # "Lohn April" matches the default "Lohn/Einkommen" category rule, so it's
    # neither Lebensmittel nor Unkategorisiert and must stay excluded here.
    descriptions = {r["description"] for r in filtered}
    assert descriptions == {"Musterladen Zürich", "Beispielmarkt Zürich"}


def test_update_transaction_category_changes_category_and_flags_manual(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    txn = next(r for r in rows if r["description"] == "Musterladen Zürich")
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lebensmittel_id = categories["Lebensmittel"]

    response = client_with_data.put(
        f"/api/transactions/{txn['id']}", json={"category_id": lebensmittel_id}
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "category_id": lebensmittel_id, "category_name": "Lebensmittel"}
    updated = next(
        r for r in client_with_data.get("/api/transactions").get_json() if r["id"] == txn["id"]
    )
    assert updated["category_id"] == lebensmittel_id

    conn = get_connection(client_with_data.application.config["DB_PATH"])
    manually_corrected = conn.execute(
        "SELECT manually_corrected FROM transactions WHERE id = ?", (txn["id"],)
    ).fetchone()["manually_corrected"]
    conn.close()
    assert manually_corrected == 1


def test_update_transaction_category_learns_a_rule(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    txn = next(r for r in rows if r["description"] == "Musterladen Zürich")
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lebensmittel_id = categories["Lebensmittel"]

    client_with_data.put(f"/api/transactions/{txn['id']}", json={"category_id": lebensmittel_id})

    conn = get_connection(client_with_data.application.config["DB_PATH"])
    rule = conn.execute(
        "SELECT category_id FROM category_rules WHERE keyword = 'musterladen'"
    ).fetchone()
    conn.close()
    assert rule is not None
    assert rule["category_id"] == lebensmittel_id


def test_update_transaction_category_does_not_learn_uncategorized(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    txn = next(r for r in rows if r["description"] == "Lohn April")
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    unkategorisiert_id = categories["Unkategorisiert"]

    conn = get_connection(client_with_data.application.config["DB_PATH"])
    rule_count_before = conn.execute("SELECT COUNT(*) c FROM category_rules").fetchone()["c"]
    conn.close()

    client_with_data.put(f"/api/transactions/{txn['id']}", json={"category_id": unkategorisiert_id})

    # _extract_keyword() picks the longest word in the normalized
    # description ("lohn april" -> "april", 5 chars beats "lohn"'s 4) — the
    # guard must skip learning entirely, so no new rule is created for
    # either word. Checked via a before/after row count rather than
    # asserting keyword IN ('lohn', 'april') is absent, since "lohn" is
    # itself now a legitimate seeded default keyword (see
    # DEFAULT_CATEGORY_KEYWORD_GROUPS's "Lohn/Einkommen") — its mere
    # presence in the table isn't evidence of unwanted learning.
    conn = get_connection(client_with_data.application.config["DB_PATH"])
    rule_count_after = conn.execute("SELECT COUNT(*) c FROM category_rules").fetchone()["c"]
    conn.close()
    assert rule_count_after == rule_count_before


def test_update_transaction_category_missing_body_field_returns_400(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    txn_id = rows[0]["id"]

    response = client_with_data.put(f"/api/transactions/{txn_id}", json={})

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_update_transaction_category_unknown_transaction_returns_404(client_with_data):
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}

    response = client_with_data.put(
        "/api/transactions/999999", json={"category_id": categories["Lebensmittel"]}
    )

    assert response.status_code == 404


def test_update_transaction_category_unknown_category_returns_404(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    txn_id = rows[0]["id"]

    response = client_with_data.put(f"/api/transactions/{txn_id}", json={"category_id": 999999})

    assert response.status_code == 404


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


def test_delete_transactions_without_any_filter_returns_400(client_with_data):
    response = client_with_data.delete("/api/transactions")

    assert response.status_code == 400
    assert len(client_with_data.get("/api/transactions").get_json()) == 3


def test_delete_transactions_by_date_range(client_with_data):
    response = client_with_data.delete("/api/transactions?start=2026-04-01&end=2026-04-30")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 2
    remaining = client_with_data.get("/api/transactions").get_json()
    assert len(remaining) == 1
    assert remaining[0]["description"] == "Musterladen Zürich"


def test_delete_transactions_by_tag_id_alone_is_accepted(client_with_data):
    # Regression test for a medium-severity bug found in the pre-release
    # audit: the "at least one filter required" safety check only
    # recognized (start, end, category_id, source, type, q, min_amount,
    # max_amount) — it rejected a request filtered ONLY by tag_id (or
    # account_id) with a 400, even though _fetch_filtered_transactions
    # (used by this same route to decide what to delete) supports both.
    rows = client_with_data.get("/api/transactions").get_json()
    musterladen = next(r for r in rows if r["description"] == "Musterladen Zürich")
    tag_id = client_with_data.post("/api/tags", json={"name": "Testtag"}).get_json()["id"]
    client_with_data.post(f"/api/transactions/{musterladen['id']}/tags", json={"tag_id": tag_id})

    response = client_with_data.delete(f"/api/transactions?tag_id={tag_id}")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1


def test_delete_transactions_by_account_id_alone_is_accepted(client_with_data):
    rows = client_with_data.get("/api/transactions").get_json()
    account_id = rows[0]["account_id"]

    response = client_with_data.delete(f"/api/transactions?account_id={account_id}")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == len(rows)


def test_delete_transactions_by_type(client_with_data):
    response = client_with_data.delete("/api/transactions?type=income")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1
    remaining = client_with_data.get("/api/transactions").get_json()
    assert all(r["amount_cents"] < 0 for r in remaining)


def test_delete_transactions_by_category(client_with_data):
    categories = {c["name"]: c["id"] for c in client_with_data.get("/api/categories").get_json()}
    lohn_id = categories["Lohn/Einkommen"]
    # Reassign "Lohn April" to Lohn/Einkommen so the category filter has something to match.
    pending_or_confirmed = client_with_data.get("/api/transactions").get_json()
    lohn_row = next(r for r in pending_or_confirmed if r["description"] == "Lohn April")
    conn = get_connection(client_with_data.application.config["DB_PATH"])
    conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (lohn_id, lohn_row["id"]))
    conn.commit()
    conn.close()

    response = client_with_data.delete(f"/api/transactions?category_id={lohn_id}")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1


def test_delete_transactions_with_backup_creates_backup_file(client_with_data):
    response = client_with_data.delete("/api/transactions?type=income&backup=true")

    assert response.status_code == 200
    db_path = client_with_data.application.config["DB_PATH"]
    backup_dir = db_path.parent / "backups"
    assert backup_dir.exists()
    assert list(backup_dir.glob("*.db"))


# ---------- by_merchant / merchant_key (Analyse tab's "Top-Händler" widget) ----------

def _import_csv(client, tmp_path, rows):
    lines = ["Datum;Buchungstext;Betrag;Währung"] + rows
    (tmp_path / "statements" / "test.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    client.post("/api/scan")
    client.post("/api/import/confirm")


def test_summary_by_merchant_groups_descriptions_differing_only_by_digits(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()
    _import_csv(client, tmp_path, [
        "01.03.2026;Coop-5307 ZH Hauptbhf;-20.00;CHF",
        "02.03.2026;Coop-1122 ZH Hauptbhf;-15.50;CHF",
        "03.03.2026;Musterladen Zürich;-45.90;CHF",
    ])

    summary = client.get("/api/summary").get_json()

    coop = next(m for m in summary["by_merchant"] if m["merchant_key"] == "coop- zh hauptbhf")
    assert coop["amount_cents"] == 3550
    assert coop["count"] == 2
    assert coop["merchant"] in ("Coop-5307 ZH Hauptbhf", "Coop-1122 ZH Hauptbhf")
    musterladen = next(m for m in summary["by_merchant"] if m["merchant"] == "Musterladen Zürich")
    assert musterladen["amount_cents"] == 4590
    assert musterladen["count"] == 1


def test_transactions_filtered_by_merchant_key(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    client = app.test_client()
    _import_csv(client, tmp_path, [
        "01.03.2026;Coop-5307 ZH Hauptbhf;-20.00;CHF",
        "02.03.2026;Coop-1122 ZH Hauptbhf;-15.50;CHF",
        "03.03.2026;Musterladen Zürich;-45.90;CHF",
    ])

    rows = client.get("/api/transactions", query_string={"merchant_key": "coop- zh hauptbhf"}).get_json()

    assert len(rows) == 2
    assert all(r["description"].startswith("Coop-") for r in rows)
