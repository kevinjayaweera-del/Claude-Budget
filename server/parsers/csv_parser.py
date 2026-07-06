import csv
from datetime import datetime

DATE_COLUMNS = ["datum", "buchungsdatum", "date", "buchungstag"]
AMOUNT_COLUMNS = ["betrag", "amount", "umsatz"]
DESCRIPTION_COLUMNS = ["text", "verwendungszweck", "beschreibung", "buchungstext", "description"]
CURRENCY_COLUMNS = ["währung", "waehrung", "currency"]

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
    if value.count(",") == 1 and value.count(".") == 0:
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
        amount_col = _find_column(reader.fieldnames, AMOUNT_COLUMNS)
        desc_col = _find_column(reader.fieldnames, DESCRIPTION_COLUMNS)
        currency_col = _find_column(reader.fieldnames, CURRENCY_COLUMNS)

        if not date_col or not amount_col or not desc_col:
            raise CsvParseError(
                f"Konnte Spalten nicht erkennen. Gefunden: {reader.fieldnames}"
            )

        rows = []
        for row in reader:
            rows.append({
                "date": _parse_date(row[date_col]),
                "description": row[desc_col].strip(),
                "amount_cents": _parse_amount_cents(row[amount_col]),
                "currency": (row.get(currency_col) or "").strip() if currency_col else "" ,
            })
        for row in rows:
            if not row["currency"]:
                row["currency"] = "CHF"
        return rows
