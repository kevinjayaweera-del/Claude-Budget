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
# alike, is a bare positive number. An unsigned amount always defaults to a
# debit (expense) here.
#
# The cardholder's own bill payment ("Ihre Zahlung – Besten Dank" /
# "IHREZAHLUNG-BESTENDANK") is always forced to a debit too — on the card's
# own statement it's a Gutschrift (it reduces the balance owed), and
# Cornercard prints it under an actual Gutschrift COLUMN (see
# _parse_columned_row below), not just as an unsigned line — but either way
# it represents real money Kevin sent FROM his checking account, never money
# he received. Storing it positive made it render as income in the ledger,
# even though the matching "ihre zahlung" categorizer rule (see
# DEFAULT_CATEGORY_RULES in server/db.py) already excludes it from every
# total via Kreditkarten-Ausgleich. The corresponding ZKB-side debit that
# actually settles the card is already negative, so this keeps both halves
# of the same real-world payment on the same side of zero. Detected by
# keyword rather than column/sign position, since it's the one line whose
# true direction contradicts how its own statement prints it. Deliberately
# these two specific phrases, not a bare "zahlung" — the same false-positive
# risk the "Kreditkarten-Ausgleich" categorization rule already documents
# (server/db.py): "zahlung" alone would also match ZKB's own "Einzahlung"
# (a real cash deposit, a genuine credit) and "Zahlungszweck"/"Ratenzahlung"-
# style text, none of which are the cardholder's own bill payment.
_PAYMENT_KEYWORDS = ("ihre zahlung", "ihrezahlung")

# "Stand Ihres Cashbacks per Rechnungsdatum 11.12.2025 CHF 81.81" — a running
# cashback-balance summary printed near the end of every Swisscard
# statement. It happens to contain both a date and a trailing amount (so it
# matches DATE_RE/AMOUNT_RE like a real transaction), but it isn't a
# booking — must not be imported as one. "Saldovortrag" (the balance carried
# forward from the previous statement, always the first line of a
# Cornercard statement) is the same kind of non-booking summary line — it
# must be skipped at parse time, not merely categorized/excluded after
# import, so it's never counted or shown at all.
_NON_TRANSACTION_KEYWORDS = ("cashback", "saldovortrag")

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
    if raw_amount[0] in "+-":
        # An explicit sign on these statements marks a refund/partial
        # reversal — e.g. "-231.20" for a merchant crediting back part of an
        # earlier charge — which is the OPPOSITE of the unsigned default
        # below (always a debit), not an already-signed amount in our own
        # convention. Verified against a real Swisscard statement: the
        # printed "Total der Transaktionen" for the card matches only when
        # this line is netted in as a credit, not counted as a second debit.
        amount_cents = abs(_amount_to_cents(raw_amount))
    else:
        amount_cents = -_amount_to_cents(raw_amount)

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


# ZKB sometimes bundles several standing-order/eBill/mobile-banking debits
# (or credits) collected together into one summary line — "Belastungen
# Dauerauftrag (3) Auftrags-Nr. ..." — printed with a "(n)" count instead of
# a single recipient, followed by n detail lines. Each detail line has no
# date of its own (it's dated the same as the summary line) and ends in a
# glued "CHF<amount>" token rather than an amount under the normal
# Belastung/Gutschrift column position. The summary line's own printed
# total must never be imported as a transaction — only its n detail lines.
_BATCH_HEADER_RE = re.compile(r"(Belastungen|Gutschriften)\s+.+?\s*\((\d+)\)\s*Auftrags-Nr\.")
_BATCH_DETAIL_AMOUNT_RE = re.compile(r"^CHF([+-]?\d{1,3}(?:['’]?\d{3})*\.\d{2})$")


def _match_batch_header(row):
    """If `row` is a collective-booking summary line, return
    (is_debit, count); otherwise None."""
    if not row:
        return None
    text = " ".join(w["text"] for w in row)
    match = _BATCH_HEADER_RE.search(text)
    if not match:
        return None
    return match.group(1) == "Belastungen", int(match.group(2))


def _parse_batch_detail_row(row, date_iso, is_debit):
    amount_word = next((w for w in row if _BATCH_DETAIL_AMOUNT_RE.match(w["text"])), None)
    if amount_word is None:
        return None
    description = " ".join(w["text"] for w in row if w["x1"] <= amount_word["x0"]).strip()
    if not description:
        return None
    magnitude = abs(_amount_to_cents(_BATCH_DETAIL_AMOUNT_RE.match(amount_word["text"]).group(1)))
    return {
        "date": date_iso,
        "description": description,
        "amount_cents": -magnitude if is_debit else magnitude,
        "currency": "CHF",
    }


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
    if any(keyword in description.lower() for keyword in _NON_TRANSACTION_KEYWORDS):
        return None

    magnitude = abs(_amount_to_cents(amount_word["text"]))
    # Cornercard prints "Ihre Zahlung" under this same Gutschrift column
    # (see _PAYMENT_KEYWORDS above) — a debit for our purposes despite
    # landing in the credit column, since it's money Kevin sent out, not
    # money he received.
    is_payment = credit is not None and any(
        keyword in description.lower() for keyword in _PAYMENT_KEYWORDS
    )

    return {
        "date": _to_iso_date(first["text"], statement_period),
        "description": description,
        "amount_cents": -magnitude if (debit is not None or is_payment) else magnitude,
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
    column_rows = []
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
                # Deferred to a single flat pass below (rather than parsed
                # immediately here) so a collective booking's detail rows
                # can be found even when the PDF's own page break falls
                # between the summary line and its details.
                column_rows.extend(page_rows)
            else:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    parsed = _parse_line(line)
                    if parsed:
                        rows.append(parsed)

    i = 0
    while i < len(column_rows):
        row = column_rows[i]
        batch = _match_batch_header(row)
        header_date = row[0]["text"] if row and WORD_DATE_RE.match(row[0]["text"]) else None
        if batch is not None and header_date is not None:
            is_debit, count = batch
            date_iso = _to_iso_date(header_date, statement_period)
            j = i + 1
            found = 0
            while j < len(column_rows) and found < count:
                candidate = column_rows[j]
                if candidate and WORD_DATE_RE.match(candidate[0]["text"]):
                    # Ran into the next real transaction row (or another
                    # summary line) before finding all `count` detail rows —
                    # stop rather than misreading unrelated content as one.
                    break
                detail = _parse_batch_detail_row(candidate, date_iso, is_debit)
                if detail:
                    rows.append(detail)
                    found += 1
                j += 1
            i = j
            continue

        parsed = _parse_columned_row(row, columns, statement_period)
        if parsed:
            rows.append(parsed)
        i += 1

    return rows
