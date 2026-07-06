from reportlab.pdfgen import canvas

from server.parsers.pdf_parser import parse_pdf, _parse_line


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


def test_parse_pdf_extracts_transactions_from_real_pdf(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(50, 800, "Kontoauszug März 2026")
    c.drawString(50, 780, "01.03.2026 Migros Zuerich -45.90")
    c.drawString(50, 760, "03.03.2026 Lohn Maerz 5200.00")
    c.save()

    rows = parse_pdf(pdf_path)

    assert rows == [
        {"date": "2026-03-01", "description": "Migros Zuerich", "amount_cents": -4590, "currency": "CHF"},
        {"date": "2026-03-03", "description": "Lohn Maerz", "amount_cents": 520000, "currency": "CHF"},
    ]
