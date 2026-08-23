import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def test_travel_candidates_matches_travel_keywords(client, tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "11.05.2025;SCANDICBERLINPOTSDAM,BERLIN;-450.35;CHF\n"
        "26.05.2025;SWISSINTLAIRLINES,ZUERICH;-664.10;CHF\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")

    candidates = client.get("/api/transactions/travel-candidates").get_json()

    descriptions = {c["description"] for c in candidates}
    assert descriptions == {"SCANDICBERLINPOTSDAM,BERLIN", "SWISSINTLAIRLINES,ZUERICH"}
    scandic = next(c for c in candidates if "SCANDIC" in c["description"])
    assert "hotel" not in scandic["matched_keywords"]  # sanity: matched via "scandic", not a generic word
    assert "scandic" in scandic["matched_keywords"]
    airline = next(c for c in candidates if "SWISSINTLAIRLINES" in c["description"])
    assert "swissintlairlines" in airline["matched_keywords"]


def test_travel_candidates_excludes_unrelated_subscription_billing_addresses(client, tmp_path):
    # A real false-positive risk found in production data: online-service
    # billing addresses (Spotify -> Stockholm, OpenAI -> Dublin) share no
    # travel vocabulary with the keyword list, so they must not show up here
    # even though they carry a foreign city name.
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "22.05.2025;SPOTIFYCH,STOCKHOLM;-19.40;CHF\n"
        "30.05.2025;OPENAI*CHATGPTSUBSCR,DUBLIN;-22.40;CHF\n",
        encoding="utf-8-sig",
    )
    client.post("/api/scan")
    client.post("/api/import/confirm")

    candidates = client.get("/api/transactions/travel-candidates").get_json()

    assert candidates == []


def test_travel_candidates_returns_empty_list_when_nothing_matches(client):
    assert client.get("/api/transactions/travel-candidates").get_json() == []
