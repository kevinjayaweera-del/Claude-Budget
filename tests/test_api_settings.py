import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", statements_dir=tmp_path / "statements")
    app.config["TESTING"] = True
    return app.test_client()


def test_get_settings_returns_defaults(client):
    response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.get_json()
    assert data["auto_categorize_enabled"] is True
    assert data["confidence_threshold"] == 0.75
    assert data["default_date_range_days"] is None


def test_get_settings_includes_db_info(client):
    data = client.get("/api/settings").get_json()

    info = data["db_info"]
    assert info["transaction_count"] == 0
    assert info["category_count"] > 0
    assert info["rule_count"] > 0
    assert info["db_size_bytes"] > 0


def test_put_settings_updates_auto_categorize_enabled(client):
    response = client.put("/api/settings", json={"auto_categorize_enabled": False})

    assert response.status_code == 200
    assert client.get("/api/settings").get_json()["auto_categorize_enabled"] is False


def test_put_settings_updates_confidence_threshold(client):
    response = client.put("/api/settings", json={"confidence_threshold": 0.9})

    assert response.status_code == 200
    assert client.get("/api/settings").get_json()["confidence_threshold"] == 0.9


def test_put_settings_rejects_out_of_range_confidence_threshold(client):
    response = client.put("/api/settings", json={"confidence_threshold": 1.5})

    assert response.status_code == 400


def test_put_settings_updates_default_date_range_days(client):
    response = client.put("/api/settings", json={"default_date_range_days": 30})

    assert response.status_code == 200
    assert client.get("/api/settings").get_json()["default_date_range_days"] == 30


def test_put_settings_can_clear_default_date_range_days(client):
    client.put("/api/settings", json={"default_date_range_days": 30})

    response = client.put("/api/settings", json={"default_date_range_days": None})

    assert response.status_code == 200
    assert client.get("/api/settings").get_json()["default_date_range_days"] is None


def test_put_settings_partial_update_leaves_other_keys_unchanged(client):
    client.put("/api/settings", json={"confidence_threshold": 0.9})

    client.put("/api/settings", json={"auto_categorize_enabled": False})

    data = client.get("/api/settings").get_json()
    assert data["auto_categorize_enabled"] is False
    assert data["confidence_threshold"] == 0.9


def test_put_settings_with_missing_body_returns_400(client):
    response = client.put("/api/settings")

    assert response.status_code == 400


def test_put_settings_with_unknown_key_returns_400(client):
    response = client.put("/api/settings", json={"not_a_real_setting": 1})

    assert response.status_code == 400


def test_put_settings_rejects_non_numeric_default_date_range_days(client):
    # Regression test for a medium-severity bug found in the pre-release
    # audit: an unvalidated non-numeric value here used to be accepted with
    # 200 OK, then permanently broke every subsequent GET /api/settings
    # call with an unhandled 500 (int() raising inside
    # _parse_setting_value).
    response = client.put("/api/settings", json={"default_date_range_days": "not-a-number"})

    assert response.status_code == 400
    # And the setting must not have been written despite the later failure.
    assert client.get("/api/settings").status_code == 200
