import io

from reportlab.pdfgen import canvas

from server.parsers.pdf_parser import (
    parse_pdf,
    _parse_line,
    _find_columns,
    _parse_columned_row,
    _group_words_into_rows,
    _to_iso_date,
    _extract_statement_month_year,
    _match_batch_header,
    _parse_batch_detail_row,
)


def _word(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def test_parse_line_extracts_date_description_amount():
    result = _parse_line("01.03.2026 Migros Zürich 45.90")
    assert result == {
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
    }


def test_parse_line_explicit_minus_sign_is_a_refund_not_a_debit():
    # Verified against a real Swisscard statement: a bare amount on these
    # card-statement lines always means a debit (see the unsigned-negation
    # branch above), so an explicit "-" is the opposite of that default — a
    # partial merchant refund — and must be stored positive. The statement's
    # own printed "Total der Transaktionen" subtotal only reconciles when
    # this line is netted in as a credit, not double-counted as a debit.
    result = _parse_line("01.04.2026 Spreebok EU, Amsterdam -231.20")
    assert result["amount_cents"] == 23120


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


def test_parse_line_treats_zahlung_as_a_debit_like_any_other_unsigned_line():
    # "Ihre Zahlung – Besten Dank" is the cardholder's own payment toward the
    # card balance, printed with the same unsigned format as a purchase. An
    # earlier version treated it as a credit (positive), matching the card's
    # own Gutschrift-side bookkeeping — but that made it render as income in
    # the ledger, even though it's real money Kevin sent out, not received.
    # It's still tagged "Kreditkarten-Ausgleich" (see the "ihre zahlung"
    # categorization rule) and excluded from every total either way; only
    # the sign — and therefore how it displays — has changed.
    result = _parse_line("29.05.2026 IHRE ZAHLUNG-BESTEN DANK 148.45")
    assert result == {
        "date": "2026-05-29",
        "description": "IHRE ZAHLUNG-BESTEN DANK",
        "amount_cents": -14845,
        "currency": "CHF",
    }


def test_parse_line_respects_explicit_sign_when_present():
    # If a line does carry an explicit sign, that always wins over the
    # unsigned-defaults-to-debit convention above.
    assert _parse_line("01.03.2026 Lohn Maerz +5200.00")["amount_cents"] == 520000


def test_parse_line_ignores_cashback_overview_line():
    # "Stand Ihres Cashbacks per Rechnungsdatum 11.12.2025 CHF 81.81" is a
    # summary line printed near the end of every Swisscard statement — it
    # happens to contain both a date and a trailing amount, so it looks
    # like a transaction to the regex, but it's a running cashback balance,
    # not a booking, and must not be imported as one.
    assert _parse_line("StandIhresCashbacksperRechnungsdatum 11.12.2025 CHF 81.81") is None
    assert _parse_line("Stand Ihres Cashbacks per Rechnungsdatum 11.12.2025 CHF 81.81") is None


def test_parse_line_ignores_saldovortrag_line():
    # "Saldovortrag" (balance carried forward from the previous statement) is
    # not a new booking — it must be skipped at parse time, not merely
    # excluded-from-totals after import, so it never reaches the pending
    # queue at all.
    assert _parse_line("01.06.2026 Saldovortrag 368.70") is None


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
    c.drawString(50, 780, "01.03.2026 Migros Zuerich 45.90")
    c.drawString(50, 760, "03.03.2026 Spotify Stockholm 22.50")
    c.drawString(50, 740, "05.03.2026 Ihre Zahlung-Besten Dank 5200.00")
    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Spotify Stockholm", "amount_cents": -2250, "currency": "CHF"},
        {"date": "2026-03-05", "description": "Ihre Zahlung-Besten Dank", "amount_cents": -520000, "currency": "CHF"},
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


def test_parse_columned_row_ihre_zahlung_in_gutschrift_column_is_still_a_debit():
    # Cornercard's own PDF prints "Ihre Zahlung" under an actual Gutschrift
    # column (unlike Swisscard, which has no columns at all — see the
    # line-based fallback tests above). Real bug: the card statement's own
    # bookkeeping shows a bill payment as a credit (it reduces the balance
    # owed), but it's money Kevin sent FROM his checking account, never
    # money he received — it must land negative regardless of which column
    # the PDF prints it under.
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("06.05.2026", 56.7, 96.7, 320.0),
        _word("Ihre", 102.0, 120.0, 320.0),
        _word("Zahlung", 122.0, 150.0, 320.0),
        _word("4'752.95", 651.9, 688.8, 320.0),
    ]

    result = _parse_columned_row(row, columns)

    assert result == {
        "date": "2026-05-06",
        "description": "Ihre Zahlung",
        "amount_cents": -475295,
        "currency": "CHF",
    }


def test_parse_columned_row_einzahlung_in_gutschrift_column_stays_a_credit():
    # Regression guard: the payment-detection keywords are "ihre zahlung"/
    # "ihrezahlung" specifically, not a bare "zahlung" — ZKB's own
    # "Einzahlung" (a real cash deposit, a genuine credit) contains
    # "zahlung" as a substring and must NOT be flipped to a debit.
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("20.06.2026", 56.7, 96.7, 320.0),
        _word("Einzahlung", 102.0, 150.0, 320.0),
        _word("Noten", 152.0, 180.0, 320.0),
        _word("3'260.00", 651.9, 688.8, 320.0),
    ]

    result = _parse_columned_row(row, columns)

    assert result == {
        "date": "2026-06-20",
        "description": "Einzahlung Noten",
        "amount_cents": 326000,
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


def test_parse_columned_row_skips_saldovortrag_row():
    # "Saldovortrag" is the credit-card statement's own opening-balance line
    # (carried forward from the previous month), not a new booking — it must
    # be skipped at parse time so it never reaches the pending queue, rather
    # than being imported and merely excluded from totals afterwards.
    columns = _find_columns([HEADER_ROW])
    row = [
        _word("01.06.2026", 56.7, 96.7, 300.0),
        _word("Saldovortrag", 102.0, 160.0, 300.0),
        _word("368.70", 592.9, 617.9, 300.0),
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


# --- Cornercard support: plural column labels, year-less short dates,
# encrypted (empty-password) PDFs pdfminer/pdfplumber can't open directly ---

def test_to_iso_date_uses_full_date_when_year_present():
    assert _to_iso_date("01.06.2026") == "2026-06-01"


def test_to_iso_date_infers_year_from_statement_period_same_month_or_earlier():
    # Statement issued in July 2026 — a June transaction belongs to 2026.
    assert _to_iso_date("01.06", statement_period=(7, 2026)) == "2026-06-01"


def test_to_iso_date_infers_previous_year_when_transaction_month_after_statement_month():
    # Statement issued in January 2026 — a December transaction is from 2025
    # (statement covers the rollover from the previous year).
    assert _to_iso_date("15.12", statement_period=(1, 2026)) == "2025-12-15"


def test_extract_statement_month_year_parses_lugano_issue_date():
    text = "CORNER BANCA SA CORNERCARD\nLugano, 1. Juli 2026\nweitere Zeilen"
    assert _extract_statement_month_year(text) == (7, 2026)


def test_extract_statement_month_year_returns_none_without_match():
    assert _extract_statement_month_year("Kontoauszug ohne Datum") is None


HEADER_ROW_PLURAL = [
    _word("Datum", 56.7, 82.0, 225.7),
    _word("Belastungen", 563.7, 610.0, 225.7),
    _word("CHF", 612.2, 626.9, 225.7),
    _word("Gutschriften", 634.5, 681.7, 225.7),
    _word("CHF", 684.1, 698.7, 225.7),
]


def test_find_columns_accepts_plural_belastungen_gutschriften_labels():
    columns = _find_columns([HEADER_ROW_PLURAL])

    assert columns is not None
    assert columns["datum_x0"] == 56.7
    lo, hi = columns["belastung"]
    assert lo < 563.7 and hi > 626.9
    lo, hi = columns["gutschrift"]
    assert lo < 634.5 and hi > 698.7


def test_parse_columned_row_uses_statement_period_for_short_dates():
    columns = _find_columns([HEADER_ROW_PLURAL])
    row = [
        _word("01.06", 56.7, 86.7, 300.0),
        _word("Migros", 102.0, 130.0, 300.0),
        _word("Zuerich", 132.0, 160.0, 300.0),
        _word("64.30", 592.9, 617.9, 300.0),
    ]

    result = _parse_columned_row(row, columns, statement_period=(7, 2026))

    assert result == {
        "date": "2026-06-01",
        "description": "Migros Zuerich",
        "amount_cents": -6430,
        "currency": "CHF",
    }


def test_parse_pdf_handles_plural_labels_and_year_less_dates(tmp_path):
    pdf_path = tmp_path / "cornercard.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(950, 700))
    c.setFont("Helvetica", 7)

    c.drawString(50, 680, "CORNER BANCA SA CORNERCARD, VIA CANOVA 16, 6901 LUGANO, CH")
    c.drawString(50, 665, "Lugano, 1. Juli 2026")

    header_y = 650
    c.drawString(50, header_y, "Datum")
    c.drawString(150, header_y, "Buchungstext")
    c.drawString(550, header_y, "Belastungen")
    c.drawString(610, header_y, "CHF")
    c.drawString(660, header_y, "Gutschriften")
    c.drawString(720, header_y, "CHF")

    row_y = 630
    c.drawString(50, row_y, "01.06")
    c.drawString(150, row_y, "Migros Zuerich")
    c.drawString(560, row_y, "64.30")

    row_y = 610
    c.drawString(50, row_y, "23.06")
    c.drawString(150, row_y, "Restaurant Bern")
    c.drawString(560, row_y, "18.00")

    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-06-01", "description": "Migros Zuerich", "amount_cents": -6430, "currency": "CHF"},
        {"date": "2026-06-23", "description": "Restaurant Bern", "amount_cents": -1800, "currency": "CHF"},
    ]


def test_parse_pdf_excludes_saldovortrag_row(tmp_path):
    # "Saldovortrag" is always the first line of a Cornercard statement — the
    # balance carried forward from the previous month, not a new June
    # expense. It must be dropped entirely at parse time, not merely
    # recategorized, so it's never imported or counted at all.
    pdf_path = tmp_path / "cornercard.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(950, 700))
    c.setFont("Helvetica", 7)

    c.drawString(50, 680, "CORNER BANCA SA CORNERCARD, VIA CANOVA 16, 6901 LUGANO, CH")
    c.drawString(50, 665, "Lugano, 1. Juli 2026")

    header_y = 650
    c.drawString(50, header_y, "Datum")
    c.drawString(150, header_y, "Buchungstext")
    c.drawString(550, header_y, "Belastungen")
    c.drawString(610, header_y, "CHF")
    c.drawString(660, header_y, "Gutschriften")
    c.drawString(720, header_y, "CHF")

    row_y = 630
    c.drawString(50, row_y, "01.06")
    c.drawString(150, row_y, "Saldovortrag")
    c.drawString(560, row_y, "368.70")

    row_y = 610
    c.drawString(50, row_y, "10.06")
    c.drawString(150, row_y, "Migros Zuerich")
    c.drawString(560, row_y, "64.30")

    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-06-10", "description": "Migros Zuerich", "amount_cents": -6430, "currency": "CHF"},
    ]


def test_parse_pdf_falls_back_to_pypdf_decrypt_when_pdfplumber_open_fails(tmp_path, monkeypatch):
    # Simulates the real-world Cornercard bug: pdfplumber.open() raises for
    # an empty-password-"encrypted" PDF (a pdfminer.six crypt-filter
    # limitation), but the file itself is perfectly readable once decrypted
    # and reserialized via pypdf. Here we force that failure path via
    # monkeypatching (the actual pdfminer bug isn't easily reproducible with
    # a synthetic PDF) and verify parse_pdf recovers via the pypdf fallback.
    pdf_path = tmp_path / "statement.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(50, 800, "Kontoauszug März 2026")
    c.drawString(50, 780, "01.03.2026 Migros Zuerich 45.90")
    c.save()

    import pdfplumber as pdfplumber_module

    real_open = pdfplumber_module.open

    def flaky_open(target, *args, **kwargs):
        if isinstance(target, io.BytesIO):
            return real_open(target, *args, **kwargs)
        raise TypeError("'PDFObjRef' object is not subscriptable")

    monkeypatch.setattr(pdfplumber_module, "open", flaky_open)

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
    ]


# --- Collective bookings ("Belastungen Dauerauftrag (3) Auftrags-Nr. ...")
# ---
#
# ZKB sometimes bundles several standing-order/eBill/mobile-banking debits
# (or credits) that were collected together into one summary line printed
# with a "(n)" count instead of a single recipient, followed by n detail
# lines (recipient + a glued "CHF<amount>" token, no leading date of their
# own). The summary line's own total must never be imported as a
# transaction — only the n detail lines, each as its own booking dated the
# same as the summary line.

def test_match_batch_header_returns_debit_and_count():
    row = [
        _word("26.06.2026", 56.7, 96.7, 300.0),
        _word("Belastungen", 102.0, 143.8, 300.0),
        _word("Dauerauftrag", 146.0, 191.4, 300.0),
        _word("(3)", 193.6, 202.5, 300.0),
        _word("Auftrags-Nr.Z261778996812", 204.8, 306.6, 300.0),
        _word("3'100.00", 586.8, 617.9, 300.0),
    ]
    assert _match_batch_header(row) == (True, 3)


def test_match_batch_header_recognizes_gutschriften_as_credit():
    row = [
        _word("26.06.2026", 56.7, 96.7, 300.0),
        _word("Gutschriften", 102.0, 143.8, 300.0),
        _word("eBill", 146.0, 170.0, 300.0),
        _word("(2)", 172.0, 181.0, 300.0),
        _word("Auftrags-Nr.Z1", 183.0, 250.0, 300.0),
    ]
    assert _match_batch_header(row) == (False, 2)


def test_match_batch_header_returns_none_for_a_normal_transaction_row():
    row = [
        _word("26.06.2026", 56.7, 96.7, 300.0),
        _word("Migros", 102.0, 130.0, 300.0),
    ]
    assert _match_batch_header(row) is None


def test_match_batch_header_returns_none_without_a_count_in_parens():
    # The single-recipient form ("Belastung Dauerauftrag: Name, ...") has no
    # "(n)" at all — that's the existing/already-correct single-transaction
    # path and must not be treated as a collective booking.
    row = [
        _word("26.06.2026", 56.7, 96.7, 300.0),
        _word("Belastung", 102.0, 130.0, 300.0),
        _word("Dauerauftrag:", 132.0, 190.0, 300.0),
        _word("Otto", 192.0, 210.0, 300.0),
        _word("Markwalder,", 212.0, 260.0, 300.0),
    ]
    assert _match_batch_header(row) is None


def test_parse_batch_detail_row_extracts_recipient_and_amount():
    row = [
        _word("AMAG", 102.0, 130.0, 314.9),
        _word("Leasing", 132.0, 165.0, 314.9),
        _word("AG,", 167.0, 180.0, 314.9),
        _word("Alte", 182.0, 200.0, 314.9),
        _word("Steinhauserstrasse", 202.0, 280.0, 314.9),
        _word("12,", 282.0, 295.0, 314.9),
        _word("6330", 297.0, 315.0, 314.9),
        _word("Cham,", 317.0, 340.0, 314.9),
        _word("CH", 342.0, 355.0, 314.9),
        _word("CHF622.60", 499.5, 547.1, 314.9),
    ]

    result = _parse_batch_detail_row(row, "2026-06-26", is_debit=True)

    assert result == {
        "date": "2026-06-26",
        "description": "AMAG Leasing AG, Alte Steinhauserstrasse 12, 6330 Cham, CH",
        "amount_cents": -62260,
        "currency": "CHF",
    }


def test_parse_batch_detail_row_is_positive_for_credits():
    row = [
        _word("Some", 102.0, 130.0, 314.9),
        _word("Employer", 132.0, 165.0, 314.9),
        _word("CHF1'500.00", 499.5, 547.1, 314.9),
    ]

    result = _parse_batch_detail_row(row, "2026-06-26", is_debit=False)

    assert result["amount_cents"] == 150000


def test_parse_batch_detail_row_returns_none_without_a_chf_amount():
    row = [_word("Zuercher", 102.0, 130.0, 314.9), _word("Kantonalbank", 132.0, 190.0, 314.9)]
    assert _parse_batch_detail_row(row, "2026-06-26", is_debit=True) is None


ZKB_HEADER_ROW = [
    _word("Datum", 50.0, 82.0, 650.0),
    _word("Buchungstext", 150.0, 210.0, 650.0),
    _word("Belastung", 550.0, 590.0, 650.0),
    _word("CHF", 592.0, 610.0, 650.0),
    _word("Gutschrift", 660.0, 700.0, 650.0),
    _word("CHF", 702.0, 720.0, 650.0),
    _word("Valuta", 780.0, 810.0, 650.0),
    _word("Saldo", 850.0, 880.0, 650.0),
    _word("CHF", 900.0, 920.0, 650.0),
]


def test_parse_pdf_resolves_collective_booking_into_individual_transactions(tmp_path):
    pdf_path = tmp_path / "kontoauszug.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(950, 700))
    c.setFont("Helvetica", 7)

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

    # A normal transaction before the batch — must still parse as usual.
    row_y = 630
    c.drawString(50, row_y, "25.06.2026")
    c.drawString(150, row_y, "Migros Zuerich")
    c.drawString(560, row_y, "45.90")

    # The collective-booking header — its own printed total (922.60) must
    # NEVER be imported as a transaction.
    row_y = 610
    c.drawString(50, row_y, "26.06.2026")
    c.drawString(150, row_y, "Belastungen Dauerauftrag (2) Auftrags-Nr.Z261428714356")
    c.drawString(560, row_y, "922.60")

    # The two detail rows: no leading date, recipient text ending in a
    # glued "CHF<amount>" token — real ZKB layout.
    row_y = 590
    c.drawString(150, row_y, "AMAG Leasing AG, Alte Steinhauserstrasse 12, 6330 Cham, CH CHF622.60")
    row_y = 570
    c.drawString(150, row_y, "Swiss Life AG, General-Guisan-Quai 40, 8002 Zuerich, CH CHF300.00")

    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-06-25", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
        {
            "date": "2026-06-26",
            "description": "AMAG Leasing AG, Alte Steinhauserstrasse 12, 6330 Cham, CH",
            "amount_cents": -62260,
            "currency": "CHF",
        },
        {
            "date": "2026-06-26",
            "description": "Swiss Life AG, General-Guisan-Quai 40, 8002 Zuerich, CH",
            "amount_cents": -30000,
            "currency": "CHF",
        },
    ]


def test_parse_pdf_resolves_collective_booking_across_a_page_break(tmp_path):
    # Real-world edge case: the collective-booking header lands near the
    # bottom of a page, so its detail rows continue on the next page, after
    # page-footer chrome that must be skipped over rather than mistaken for
    # a missing detail row.
    pdf_path = tmp_path / "kontoauszug.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(950, 700))
    c.setFont("Helvetica", 7)

    def draw_table_header():
        c.drawString(50, 650, "Datum")
        c.drawString(150, 650, "Buchungstext")
        c.drawString(550, 650, "Belastung")
        c.drawString(610, 650, "CHF")
        c.drawString(660, 650, "Gutschrift")
        c.drawString(720, 650, "CHF")
        c.drawString(780, 650, "Valuta")
        c.drawString(850, 650, "Saldo")
        c.drawString(900, 650, "CHF")

    draw_table_header()
    c.drawString(50, 100, "26.06.2026")
    c.drawString(150, 100, "Belastungen Dauerauftrag (2) Auftrags-Nr.Z261428714356")
    c.drawString(560, 100, "922.60")
    c.drawString(50, 60, "Zuercher Kantonalbank")
    c.showPage()

    c.setFont("Helvetica", 7)
    draw_table_header()
    c.drawString(150, 630, "AMAG Leasing AG, Alte Steinhauserstrasse 12, 6330 Cham, CH CHF622.60")
    c.drawString(150, 610, "Swiss Life AG, General-Guisan-Quai 40, 8002 Zuerich, CH CHF300.00")
    c.drawString(50, 590, "27.06.2026")
    c.drawString(150, 590, "Coop Zuerich")
    c.drawString(560, 590, "12.00")

    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {
            "date": "2026-06-26",
            "description": "AMAG Leasing AG, Alte Steinhauserstrasse 12, 6330 Cham, CH",
            "amount_cents": -62260,
            "currency": "CHF",
        },
        {
            "date": "2026-06-26",
            "description": "Swiss Life AG, General-Guisan-Quai 40, 8002 Zuerich, CH",
            "amount_cents": -30000,
            "currency": "CHF",
        },
        {"date": "2026-06-27", "description": "Coop Zuerich", "amount_cents": -1200, "currency": "CHF"},
    ]
