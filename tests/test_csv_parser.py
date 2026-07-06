import pytest

from server.parsers.csv_parser import parse_csv, CsvParseError


def test_parse_csv_semicolon_delimited(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "03.03.2026;Lohn März;5200.00;CHF\n",
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zürich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Lohn März", "amount_cents": 520000, "currency": "CHF"},
    ]


def test_parse_csv_comma_delimited_english_headers(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Date,Description,Amount\n"
        "2026-03-01,Coffee Shop,-4.50\n",
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Coffee Shop", "amount_cents": -450, "currency": "CHF"},
    ]


def test_parse_csv_raises_when_columns_unrecognized(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text("Foo,Bar\n1,2\n", encoding="utf-8-sig")

    with pytest.raises(CsvParseError):
        parse_csv(csv_path)
