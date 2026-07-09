import io
import re
from decimal import Decimal

import pdfplumber
import pypdf

# Legacy single-amount-column heuristic (line-based). Used as a fallback for
# PDF layouts that don't expose separate Belastung/Gutschrift (debit/credit)
# columns — e.g. credit card statements with one signed amount per line.
DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
AMOUNT_RE = re.compile(r"([+-]?\d{1,3}(?:['’]?\d{3})*[.,]\d{2})\s*$")

# Real credit-card statements (Swisscard/Cornercard) parsed via this fallback
# carry no per-line sign at all — every listed amount, purchase or payment
# alike, is a bare positive number. An unsigned amount therefore defaults to
# a debit (expense) here; the cardholder's own payment toward the balance
# ("Ihre Zahlung – Besten Dank") is the one exception, detected by keyword —
# the same "ihre zahlung" signal the categorizer already relies on (see
# DEFAULT_CATEGORY_RULES in server/db.py). An explicit "+"/"-" sign, if a
# statement format ever includes one, always overrides this default.
_PAYMENT_KEYWORDS = ("zahlung",)

# "Stand Ihres Cashbacks per Rechnungsdatum 11.12.2025 CHF 81.81" — a running
# cashback-balance summary printed near the end of every Swisscard
# statement. It happens to contain both a date and a trailing amount (so it
# matches DATE_RE/AMOUNT_RE like a real transaction), but it isn't a
# booking — must not be imported as one.
_NON_TRANSACTION_KEYWORDS = ("cashback",)

# Column-aware parsing (word-position based). Used when a page exposes a
# "Datum ... Belastung ... Gutschrift ..." table header (e.g. Swiss bank
# statements like ZKB), where the same-line "last number" is the running
# Saldo, not the transaction amount — the real amount only shows up in the
# Belastung (debit) or Gutschrift (credit) column, identified by its
# horizontal position, not its order in the extracted text.
WORD_DATE_RE = re.compile(r"^\d{2}\.\d{2}(?:\.\d{4})?$")
WORD_AMOUNT_RE = re.compile(r"^[+-]?\d{1,3}(?:['’]?\d{3})*[.,]\d{2}$")
ROW_TOLERANCE = 3  # points; words within this vertical distance count as one row
COLUMN_PADDING = 5  # points; slack added around a header label's x-range

# Cornercard prints transaction dates as "DD.MM" with no year at all — the
# year has to be inferred from the statement's own issue date line
# ("Lugano, 1. Juli 2026"). A transaction in the same month (or earlier)
# as the issue month belongs to the issue year; a later month means it's
# from the year before (e.g. a December transaction on a January statement).
_GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "marz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
_STATEMENT_DATE_RE = re.compile(r"Lugano,\s*\d{1,2}\.\s*(\w+)\s*(\d{4})")

# Some PDF exports (seen from Cornercard) write their encryption/crypt-filter
# dictionary with entries as indirect references instead of resolved dicts —
# a known pdfminer.six limitation (github.com/pdfminer/pdfminer.six#1140-ish),
# not real content protection: the user password is empty. pdfplumber.open()
# raises a TypeError deep inside pdfminer for these files; pypdf handles the
# same structure fine, so it's used as a decrypt-and-reserialize fallback —
# only when the normal path fails, so already-working files (ZKB, Swisscard)
# never touch this code path.


def _to_iso_date(value, statement_period=None):
    parts = value.split(".")
    if len(parts) == 3:
        day, month, year = parts
    else:
        day, month = parts
        statement_month, statement_year = statement_period
        year = str(statement_year if int(month) <= statement_month else statement_year - 1)
    return f"{year}-{month}-{day}"


def _extract_statement_month_year(text):
    match = _STATEMENT_DATE_RE.search(text)
    if not match:
        return None
    month = _GERMAN_MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return month, int(match.group(2))


def _decrypt_pdf_bytes(file_path):
    reader = pypdf.PdfReader(str(file_path))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def _amount_to_cents(value):
    normalized = value.replace("’", "").replace("'", "").replace(" ", "")
    normalized = normalized.replace(",", ".")
    cents = Decimal(normalized) * 100
    return int(cents.to_integral_value())


def _parse_line(line):
    if any(keyword in line.lower() for keyword in _NON_TRANSACTION_KEYWORDS):
        return None
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

    raw_amount = amount_match.group(1)
    amount_cents = _amount_to_cents(raw_amount)
    if raw_amount[0] not in "+-":
        is_payment = any(keyword in description.lower() for keyword in _PAYMENT_KEYWORDS)
        if not is_payment:
            amount_cents = -amount_cents

    return {
        "date": _to_iso_date(date_match.group(1)),
        "description": description,
        "amount_cents": amount_cents,
        "currency": "CHF",
    }


def _group_words_into_rows(words):
    rows = []
    for word in sorted(words, key=lambda w: w["top"]):
        for row in rows:
            if abs(row[0]["top"] - word["top"]) <= ROW_TOLERANCE:
                row.append(word)
                break
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


_DEBIT_LABELS = ("Belastung", "Belastungen")
_CREDIT_LABELS = ("Gutschrift", "Gutschriften")


def _find_columns(rows):
    """Locate a header row with Datum/Belastung(en)/Gutschrift(en) labels and
    return their x-ranges, or None if this page has no such header. Accepts
    both singular (ZKB) and plural (Cornercard) label spellings."""
    for row in rows:
        labels = {word["text"].rstrip(":"): word for word in row}
        debit_label = next((label for label in _DEBIT_LABELS if label in labels), None)
        credit_label = next((label for label in _CREDIT_LABELS if label in labels), None)
        if "Datum" not in labels or debit_label is None or credit_label is None:
            continue

        datum_x0 = labels["Datum"]["x0"]
        belastung_x0 = labels[debit_label]["x0"]
        belastung_x1 = labels[debit_label]["x1"]
        gutschrift_x0 = labels[credit_label]["x0"]
        gutschrift_x1 = labels[credit_label]["x1"]

        # "Belastung"/"Gutschrift" are immediately followed by a "CHF" word;
        # fold it into the column's right edge so amounts under "CHF" match.
        for word in row:
            if word["text"] == "CHF" and belastung_x1 <= word["x0"] <= belastung_x1 + 20:
                belastung_x1 = word["x1"]
            if word["text"] == "CHF" and gutschrift_x1 <= word["x0"] <= gutschrift_x1 + 20:
                gutschrift_x1 = word["x1"]

        return {
            "datum_x0": datum_x0,
            "belastung": (belastung_x0 - COLUMN_PADDING, belastung_x1 + COLUMN_PADDING),
            "gutschrift": (gutschrift_x0 - COLUMN_PADDING, gutschrift_x1 + COLUMN_PADDING),
        }
    return None


def _amount_in_range(word, x_range):
    lo, hi = x_range
    return lo <= word["x0"] and word["x1"] <= hi and WORD_AMOUNT_RE.match(word["text"])


def _parse_columned_row(row, columns, statement_period=None):
    if not row:
        return None
    first = row[0]
    if not WORD_DATE_RE.match(first["text"]):
        return None
    if abs(first["x0"] - columns["datum_x0"]) > COLUMN_PADDING:
        return None

    debit = next((w for w in row if _amount_in_range(w, columns["belastung"])), None)
    credit = next((w for w in row if _amount_in_range(w, columns["gutschrift"])), None)
    amount_word = debit or credit
    if amount_word is None:
        return None

    description = " ".join(
        w["text"] for w in row[1:] if w["x1"] <= columns["belastung"][0]
    ).strip()
    if not description:
        return None

    magnitude = abs(_amount_to_cents(amount_word["text"]))

    return {
        "date": _to_iso_date(first["text"], statement_period),
        "description": description,
        "amount_cents": -magnitude if debit is not None else magnitude,
        "currency": "CHF",
    }


def _open_pdf(file_path):
    try:
        return pdfplumber.open(file_path)
    except Exception:
        return pdfplumber.open(_decrypt_pdf_bytes(file_path))


def parse_pdf(file_path):
    rows = []
    columns = None
    statement_period = None
    with _open_pdf(file_path) as pdf:
        for page in pdf.pages:
            if statement_period is None:
                statement_period = _extract_statement_month_year(page.extract_text() or "")

            words = page.extract_words()
            if not words:
                continue

            page_rows = _group_words_into_rows(words)
            page_columns = _find_columns(page_rows)
            if page_columns:
                columns = page_columns

            if columns:
                for row in page_rows:
                    parsed = _parse_columned_row(row, columns, statement_period)
                    if parsed:
                        rows.append(parsed)
            else:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    parsed = _parse_line(line)
                    if parsed:
                        rows.append(parsed)
    return rows
