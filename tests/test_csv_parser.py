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


def test_parse_csv_handles_split_belastung_gutschrift_columns(tmp_path):
    # A fuller ZKB CSV export variant mirrors the PDF layout: separate debit/
    # credit columns instead of one signed "Betrag" column, plus extra
    # columns this parser doesn't need (ZKB-Referenz, Betrag Detail, Valuta,
    # Saldo CHF, Zahlungszweck, Details).
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        '"Datum";"Buchungstext";"Whg";"Betrag Detail";"ZKB-Referenz";"Referenznummer";'
        '"Belastung CHF";"Gutschrift CHF";"Valuta";"Saldo CHF";"Zahlungszweck";"Details"\n'
        '"07.07.2026";"Belastung TWINT: SUTERS HOFMART AESCH ZH";"";"";"L113P1-1";"";'
        '"6.50";"";"07.07.2026";"2108.86";"";""\n'
        '"07.07.2026";"Gutschrift TWINT: EGLOFF, JULIA +41788086218";"";"";"L113R2-2";"";'
        '"";"46.55";"07.07.2026";"2115.36";"";""\n',
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {
            "date": "2026-07-07",
            "description": "Belastung TWINT: SUTERS HOFMART AESCH ZH",
            "amount_cents": -650,
            "currency": "CHF",
        },
        {
            "date": "2026-07-07",
            "description": "Gutschrift TWINT: EGLOFF, JULIA +41788086218",
            "amount_cents": 4655,
            "currency": "CHF",
        },
    ]


def test_parse_csv_split_columns_raises_when_row_has_neither_debit_nor_credit(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        '"Datum";"Buchungstext";"Belastung CHF";"Gutschrift CHF"\n'
        '"07.07.2026";"Mystery row";"";""\n',
        encoding="utf-8-sig",
    )

    with pytest.raises(CsvParseError):
        parse_csv(csv_path)


def test_parse_csv_prefers_single_amount_column_when_both_forms_present(tmp_path):
    # If a file somehow has both a plain "Betrag" column and Belastung/
    # Gutschrift columns, the simpler single-column form takes priority so
    # existing single-column exports are never affected by the new fallback.
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Datum;Buchungstext;Betrag;Belastung CHF;Gutschrift CHF\n"
        "01.03.2026;Ambiguous Row;-45.90;999.00;999.00\n",
        encoding="utf-8-sig",
    )

    rows = parse_csv(csv_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Ambiguous Row", "amount_cents": -4590, "currency": "CHF"},
    ]
