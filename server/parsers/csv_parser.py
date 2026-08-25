import csv
from datetime import datetime

from server.parsers import ParsedRows

DATE_COLUMNS = ["datum", "buchungsdatum", "date", "buchungstag"]
AMOUNT_COLUMNS = ["betrag", "amount", "umsatz"]
DEBIT_COLUMNS = ["belastung", "belastung chf"]
CREDIT_COLUMNS = ["gutschrift", "gutschrift chf"]
DESCRIPTION_COLUMNS = ["text", "verwendungszweck", "beschreibung", "buchungstext", "description"]
CURRENCY_COLUMNS = ["währung", "waehrung", "currency", "whg"]

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]


class CsvParseError(Exception):
    pass


def _find_column(fieldnames, candidates):
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _parse_date(value):
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise CsvParseError(f"Unbekanntes Datumsformat: {value}")


def _parse_amount_cents(value):
    value = value.strip().replace("'", "").replace(" ", "")
    if "," in value and "." in value:
        # Both separators present: whichever one appears LAST is the
        # decimal separator, the other is a thousands separator to strip.
        # Handles both "1,234.56"-style (US/international) and
        # "1.234,56"-style (German/Austrian) amounts in the same code path
        # — previously only the first form was handled correctly; the
        # second silently produced a value ~1000x too small (comma
        # stripped, dot left in place as if it were already the decimal
        # point) with no error raised.
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(",") == 1:
        value = value.replace(",", ".")
    else:
        value = value.replace(",", "")
    try:
        return round(float(value) * 100)
    except ValueError:
        raise CsvParseError(f"Unbekanntes Betragsformat: {value}")


def parse_csv(file_path):
    with open(file_path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise CsvParseError("Leere oder unlesbare CSV-Datei")

        date_col = _find_column(reader.fieldnames, DATE_COLUMNS)
        desc_col = _find_column(reader.fieldnames, DESCRIPTION_COLUMNS)
        currency_col = _find_column(reader.fieldnames, CURRENCY_COLUMNS)
        amount_col = _find_column(reader.fieldnames, AMOUNT_COLUMNS)
        debit_col = _find_column(reader.fieldnames, DEBIT_COLUMNS)
        credit_col = _find_column(reader.fieldnames, CREDIT_COLUMNS)

        # Some exports (e.g. a fuller ZKB CSV variant, mirroring the PDF
        # layout) use separate debit/credit columns instead of one signed
        # "Betrag" column. Only fall back to that when no single amount
        # column was found, so existing single-column exports are unaffected.
        use_split_columns = not amount_col and debit_col and credit_col

        if not date_col or not desc_col or not (amount_col or use_split_columns):
            raise CsvParseError(
                f"Konnte Spalten nicht erkennen. Gefunden: {reader.fieldnames}"
            )

        rows = ParsedRows()
        # line_number starts at 2: DictReader's first data row is line 2 of
        # the file (line 1 is the header), matching what a user would see if
        # they opened the CSV in a text editor/Excel.
        for line_number, row in enumerate(reader, start=2):
            if not row[date_col].strip():
                # A combined transaction (e.g. "Belastungen Dauerauftrag (2)")
                # can be followed by recipient-breakdown rows that carry no
                # date and no amount of their own — informational sub-detail,
                # not a separate booking. Skip rather than treating it as a
                # malformed transaction, so the rest of the file still imports.
                continue
            try:
                if use_split_columns:
                    debit = row.get(debit_col, "").strip()
                    credit = row.get(credit_col, "").strip()
                    if debit:
                        amount_cents = -abs(_parse_amount_cents(debit))
                    elif credit:
                        amount_cents = abs(_parse_amount_cents(credit))
                    else:
                        raise CsvParseError(
                            f"Weder Belastung noch Gutschrift gefüllt in Zeile: {row}"
                        )
                else:
                    amount_cents = _parse_amount_cents(row[amount_col])
                date = _parse_date(row[date_col])
            except CsvParseError as exc:
                # A single malformed row (bad date/amount format, or neither
                # Belastung nor Gutschrift filled) must not discard every
                # other, otherwise well-formed row in the same file — only
                # this row is skipped and reported, the rest still imports.
                rows.errors.append({"line": line_number, "reason": str(exc)})
                continue

            currency = (row.get(currency_col) or "").strip() if currency_col else ""
            rows.append({
                "date": date,
                "description": row[desc_col].strip(),
                "amount_cents": amount_cents,
                "currency": currency or "CHF",
            })
        return rows
