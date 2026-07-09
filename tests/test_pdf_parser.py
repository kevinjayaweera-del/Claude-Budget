from reportlab.pdfgen import canvas

from server.parsers.pdf_parser import (
    parse_pdf,
    _parse_line,
    _find_columns,
    _parse_columned_row,
    _group_words_into_rows,
)


def _word(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def test_parse_line_extracts_date_description_amount():
    result = _parse_line("01.03.2026 Migros Zürich -45.90")
    assert result == {
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
    }


def test_parse_line_returns_none_when_no_date():
    assert _parse_line("Kontostand am Monatsende") is None


def test_parse_line_returns_none_when_no_amount():
    assert _parse_line("01.03.2026 Migros Zürich") is None


def test_parse_line_returns_none_when_date_and_amount_overlap():
    # The amount pattern's match starts before the date match ends (it overlaps
    # the tail of the date). This yields an empty description slice
    # (line[date_match.end():amount_match.start()] has start > end), so
    # _parse_line returns None via the empty-description short-circuit rather
    # than the separate start-order guard clause.
    assert _parse_line("Ref 01.03.2026.90") is None


def test_parse_line_defaults_unsigned_amount_to_debit():
    # Real credit-card statements (Swisscard/Cornercard) print every line —
    # both purchases and the cardholder's own payment — as a bare, unsigned
    # number; there is no per-line sign in the source text at all. A purchase
    # must therefore default to a debit (negative/expense), not a credit.
    result = _parse_line("22.05.2026 SPOTIFYCH,STOCKHOLM 22.50")
    assert result == {
        "date": "2026-05-22",
        "description": "SPOTIFYCH,STOCKHOLM",
        "amount_cents": -2250,
        "currency": "CHF",
    }


def test_parse_line_treats_zahlung_as_credit():
    # "Ihre Zahlung – Besten Dank" is the cardholder's own payment toward the
    # card balance, printed with the same unsigned format as a purchase — it
    # must be detected by keyword (matching the "ihre zahlung" categorization
    # rule) and treated as a credit, not a debit.
    result = _parse_line("29.05.2026 IHRE ZAHLUNG-BESTEN DANK 148.45")
    assert result == {
        "date": "2026-05-29",
        "description": "IHRE ZAHLUNG-BESTEN DANK",
        "amount_cents": 14845,
        "currency": "CHF",
    }


def test_parse_line_respects_explicit_sign_when_present():
    # If a line does carry an explicit sign, that always wins over the
    # unsigned-defaults-to-debit convention above.
    assert _parse_line("01.03.2026 Lohn Maerz +5200.00")["amount_cents"] == 520000


def test_parse_pdf_returns_empty_list_for_blank_page(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.showPage()
    c.save()

    assert parse_pdf(pdf_path) == []


def test_parse_pdf_returns_empty_list_when_no_transaction_lines(tmp_path):
    pdf_path = tmp_path / "no_transactions.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(50, 800, "Kontoauszug März 2026")
    c.drawString(50, 780, "Vielen Dank fuer Ihren Besuch")
    c.save()

    assert parse_pdf(pdf_path) == []


def test_parse_pdf_extracts_transactions_from_real_pdf(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(50, 800, "Kontoauszug März 2026")
    c.drawString(50, 780, "01.03.2026 Migros Zuerich -45.90")
    c.drawString(50, 760, "03.03.2026 Spotify Stockholm 22.50")
    c.drawString(50, 740, "05.03.2026 Ihre Zahlung-Besten Dank 5200.00")
    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Spotify Stockholm", "amount_cents": -2250, "currency": "CHF"},
        {"date": "2026-03-05", "description": "Ihre Zahlung-Besten Dank", "amount_cents": 520000, "currency": "CHF"},
    ]


# --- Column-aware parsing (Belastung/Gutschrift bank statement layout) ---
#
# Some banks (e.g. ZKB) print a table with separate debit ("Belastung") and
# credit ("Gutschrift") columns, plus a running balance ("Saldo") as the last
# number on the line. A naive "last number on the line" heuristic picks up
# the Saldo instead of the actual transaction amount. These tests cover the
# word-position-based column detection that fixes this.

HEADER_ROW = [
    _word("Datum", 56.7, 82.0, 225.7),
    _word("Belastung", 563.7, 601.0, 225.7),
    _word("CHF", 603.2, 617.9, 225.7),
    _word("Gutschrift", 634.5, 671.7, 225.7),
    _word("CHF", 674.1, 688.7, 225.7),
    _word("Valuta", 709.6, 733.6, 225.7),
    _word("Saldo", 761.5, 782.4, 225.7),
    _word("CHF", 784.7, 799.3, 225.7),
]


def test_find_columns_locates_belastung_and_gutschrift_ranges():
    columns = _find_columns([HEADER_ROW])

    assert columns["datum_x0"] == 56.7
    lo, hi = columns["belastung"]
    assert lo < 563.7 and hi > 617.9
    lo, hi = columns["gutschrift"]
    assert lo < 634.5 and hi > 688.7


def test_find_columns_returns_none_without_header():
    rows = [[_word("Kontoauszug", 50, 120, 100)]]
    assert _find_columns(rows) is None


def test_parse_columned_row_debit_is_negative():
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("01.06.2026", 56.7, 96.7, 300.0),
        _word("Migros", 102.0, 130.0, 300.0),
        _word("Zuerich", 132.0, 160.0, 300.0),
        _word("64.30", 592.9, 617.9, 300.0),
        _word("03.06.2026", 694.1, 734.1, 300.0),
        _word("1'000.00", 762.0, 799.4, 300.0),
    ]

    result = _parse_columned_row(row, columns)

    assert result == {
        "date": "2026-06-01",
        "description": "Migros Zuerich",
        "amount_cents": -6430,
        "currency": "CHF",
    }


def test_parse_columned_row_credit_is_positive():
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("03.06.2026", 56.7, 96.7, 320.0),
        _word("Lohn", 102.0, 125.0, 320.0),
        _word("Juni", 127.0, 148.0, 320.0),
        _word("5'200.00", 651.9, 688.8, 320.0),
        _word("03.06.2026", 694.1, 734.1, 320.0),
        _word("6'200.00", 762.0, 799.4, 320.0),
    ]

    result = _parse_columned_row(row, columns)

    assert result == {
        "date": "2026-06-03",
        "description": "Lohn Juni",
        "amount_cents": 520000,
        "currency": "CHF",
    }


def test_parse_columned_row_captures_pending_reservation_without_valuta_or_saldo():
    # Some rows (temporary card-reservation holds) have a date and a debit
    # amount but no Valuta/Saldo — they should still be captured.
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("05.06.2026", 56.7, 96.7, 340.0),
        _word("Restaurant", 102.0, 140.0, 340.0),
        _word("Zuerich", 142.0, 170.0, 340.0),
        _word("23.50", 592.9, 617.9, 340.0),
    ]

    result = _parse_columned_row(row, columns)

    assert result == {
        "date": "2026-06-05",
        "description": "Restaurant Zuerich",
        "amount_cents": -2350,
        "currency": "CHF",
    }


def test_parse_columned_row_returns_none_without_leading_date():
    # A continuation/annotation line (e.g. extra recipient info printed under
    # a transaction) has no date in the Datum column and must be ignored,
    # even if it happens to contain an amount-shaped token in range.
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("Zusatzinfo", 102.0, 150.0, 360.0),
        _word("12.00", 592.9, 617.9, 360.0),
    ]

    assert _parse_columned_row(row, columns) is None


def test_group_words_into_rows_groups_by_similar_top():
    words = [
        _word("A", 10, 20, 100.0),
        _word("B", 30, 40, 101.5),
        _word("C", 10, 20, 200.0),
    ]

    rows = _group_words_into_rows(words)

    assert len(rows) == 2
    assert [w["text"] for w in rows[0]] == ["A", "B"]
    assert [w["text"] for w in rows[1]] == ["C"]


def test_parse_pdf_uses_column_layout_for_belastung_gutschrift_statement(tmp_path):
    pdf_path = tmp_path / "kontoauszug.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(950, 700))
    c.setFont("Helvetica", 7)

    # Header row. A small font with generously spaced columns avoids
    # neighbouring words (e.g. "Belastung" and "CHF") rendering close enough
    # to be merged into one token by pdfplumber's word extraction.
    header_y = 650
    c.drawString(50, header_y, "Datum")
    c.drawString(150, header_y, "Buchungstext")
    c.drawString(550, header_y, "Belastung")
    c.drawString(610, header_y, "CHF")
    c.drawString(660, header_y, "Gutschrift")
    c.drawString(720, header_y, "CHF")
    c.drawString(780, header_y, "Valuta")
    c.drawString(850, header_y, "Saldo")
    c.drawString(900, header_y, "CHF")

    # Debit row: amount under Belastung, Saldo at the far right must be ignored.
    row_y = 630
    c.drawString(50, row_y, "01.06.2026")
    c.drawString(150, row_y, "Migros Zuerich")
    c.drawString(560, row_y, "64.30")
    c.drawString(780, row_y, "03.06.2026")
    c.drawString(850, row_y, "1'000.00")

    # Credit row: amount under Gutschrift.
    row_y = 610
    c.drawString(50, row_y, "03.06.2026")
    c.drawString(150, row_y, "Lohn Juni")
    c.drawString(665, row_y, "5'200.00")
    c.drawString(780, row_y, "03.06.2026")
    c.drawString(850, row_y, "6'200.00")

    # Pending reservation: debit amount, no Valuta/Saldo — still captured.
    row_y = 590
    c.drawString(50, row_y, "05.06.2026")
    c.drawString(150, row_y, "Restaurant Zuerich")
    c.drawString(560, row_y, "23.50")

    # Continuation/annotation line with no leading date — must be ignored.
    row_y = 570
    c.drawString(150, row_y, "Zusatzinfo")
    c.drawString(560, row_y, "12.00")

    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-06-01", "description": "Migros Zuerich", "amount_cents": -6430, "currency": "CHF"},
        {"date": "2026-06-03", "description": "Lohn Juni", "amount_cents": 520000, "currency": "CHF"},
        {"date": "2026-06-05", "description": "Restaurant Zuerich", "amount_cents": -2350, "currency": "CHF"},
    ]
