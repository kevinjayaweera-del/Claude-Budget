import re
from decimal import Decimal

import pdfplumber

DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
AMOUNT_RE = re.compile(r"([+-]?\d{1,3}(?:['’]?\d{3})*[.,]\d{2})\s*$")


def _to_iso_date(value):
    day, month, year = value.split(".")
    return f"{year}-{month}-{day}"


def _amount_to_cents(value):
    normalized = value.replace("’", "").replace("'", "").replace(" ", "")
    normalized = normalized.replace(",", ".")
    cents = Decimal(normalized) * 100
    return int(cents.to_integral_value())


def _parse_line(line):
    date_match = DATE_RE.search(line)
    amount_match = AMOUNT_RE.search(line)
    if not date_match or not amount_match:
        return None
    # Currently redundant with the empty-description check below (whenever this
    # fires, the description slice is already empty), but kept as an explicit,
    # self-documenting invariant in case the surrounding logic changes.
    if amount_match.start() < date_match.end():
        return None

    description = line[date_match.end():amount_match.start()].strip(" -\t")
    if not description:
        return None

    return {
        "date": _to_iso_date(date_match.group(1)),
        "description": description,
        "amount_cents": _amount_to_cents(amount_match.group(1)),
        "currency": "CHF",
    }


def parse_pdf(file_path):
    rows = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                parsed = _parse_line(line)
                if parsed:
                    rows.append(parsed)
    return rows
