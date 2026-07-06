# Budget Tracker (privat, lokal) – Design

Status: Approved (MVP-Scope)
Datum: 2026-07-06

## Ziel

Ein lokaler, privater Budget-Tracker für Zuhause. Monatlich werden Kontoauszüge
(PDF/CSV, verschiedene Banken/Kreditkarten, z.B. ZKB) in einen Ordner gelegt.
Die App liest die Dateien aus, extrahiert Buchungen, kategorisiert sie automatisch
(mit manueller Korrekturmöglichkeit) und stellt sie in einem interaktiven,
optisch ansprechenden Dashboard mit Filtern und Datumseingrenzung dar.

Keine Cloud, kein externer Upload – alles läuft lokal auf dem Rechner des Nutzers,
da es sich um Finanzdaten handelt.

## Tech-Stack

- **Backend:** Python + Flask
- **Speicherung:** SQLite (Datei-basiert, kein separater DB-Server)
- **PDF-Parsing:** pdfplumber (tabellenbewusste Textextraktion)
- **Datenaufbereitung:** pandas
- **Frontend:** HTML/CSS/JS + Chart.js, ausgeliefert über Flask

Begründung: pdfplumber liefert deutlich robustere Ergebnisse bei tabellarisch
strukturierten Bank-PDFs als reine Regex-auf-Rohtext-Ansätze. Python wird bei
Bedarf im Rahmen der Implementierung installiert (winget/offizieller Installer).

## Ordnerstruktur

```
budget-tracker/
  statements/          # Nutzer legt PDFs/CSVs hier ab (Unterordner erlaubt)
  server/              # Flask-Backend
  data/budget.db       # SQLite-DB, wird automatisch angelegt
  web/                 # Dashboard-Frontend (HTML/JS/CSS + Chart.js)
```

## Ablauf (monatlicher Workflow)

1. Nutzer kopiert neue Auszüge (PDF/CSV) nach `statements/`.
2. App lokal starten (bzw. läuft bereits).
3. Im Dashboard "Neue Dateien importieren" klicken → Backend scannt den Ordner,
   erkennt anhand eines Datei-Hashes noch nicht importierte Dateien, parst sie.
4. Erkannte Buchungen erscheinen in einer **Korrektur-Ansicht** (Tabelle) zur
   Prüfung: Kategorie ändern, Betrag/Datum korrigieren, Duplikate/Fehlzeilen
   löschen.
5. Nutzer bestätigt → Buchungen werden in SQLite geschrieben und erscheinen
   sofort im Dashboard.

Datei-Hash (nicht Dateiname) verhindert doppelten Import derselben Datei.

## Datenmodell (SQLite)

- **`imported_files`**: id, hash, dateiname, quelle (z.B. "ZKB", "Kreditkarte"),
  importiert_am
- **`transactions`**: id, datum, beschreibung, betrag_rappen (Integer, keine
  Float-Rundungsfehler), waehrung (CHF/EUR/...), kategorie_id, quelle/konto,
  datei_id (FK auf imported_files), manuell_korrigiert (bool)
- **`categories`**: id, name — feste Startliste (Lebensmittel, Miete/Wohnen,
  Freizeit, Transport, Versicherungen, Gesundheit, Shopping, Abos, Sonstiges,
  Unkategorisiert), erweiterbar
- **`category_rules`**: id, schluesselwort, kategorie_id — wird automatisch
  ergänzt, wenn der Nutzer in der Korrektur-Ansicht eine Kategorie manuell setzt

Mehrwährungsfähig: Fremdwährungsbeträge (z.B. EUR/USD auf Kreditkarte im
Ausland) werden mit Original-Betrag und -Währung übernommen, ohne
Live-Kursumrechnung (Umrechnung ist expliziter Nicht-MVP-Scope).

## Import & Parsing

- **CSV:** Auto-Erkennung gängiger Spaltennamen (Datum/Buchungsdatum,
  Betrag/Amount, Text/Verwendungszweck/Beschreibung, Währung).
- **PDF:** pdfplumber extrahiert Text/Tabellen pro Seite; Heuristik sucht
  Zeilen mit Datumsmuster (DD.MM.YYYY) + Betragsmuster + Restbeschreibung.
  Best-effort, da Layouts je Bank variieren — die Korrektur-Ansicht fängt
  Fehlinterpretationen ab.
- **Kategorisierung:** Schlüsselwort-Matching gegen `category_rules` auf den
  Beschreibungstext. Kein Treffer → "Unkategorisiert", manuelle Zuordnung in
  der Korrektur-Ansicht speichert automatisch eine neue Regel fürs nächste Mal.

## Dashboard & Filter

Eine Hauptseite mit:

- Datums-Range-Picker (Presets wie "letzte 3 Monate", "dieses Jahr", sowie
  freier Zeitraum)
- Filter: Kategorie(n), Konto/Quelle, Betragsbereich, Freitextsuche in
  Beschreibung
- Charts (Chart.js): Ausgaben nach Kategorie (Donut/Bar), Verlauf über Zeit
  (Linie, monatlich), Top-Ausgaben, Summen Einnahmen/Ausgaben/Saldo im
  gewählten Zeitraum
- Sortierbare Tabelle aller gefilterten Buchungen mit Inline-Kategorie-Änderung

Visuelles Design: klar, minimalistisch, eigenes Akzentfarben-Schema statt
Standard-Bootstrap-Look (Feinschliff ggf. via frontend-design-Skill während
der Umsetzung).

## Explizit außerhalb des MVP-Scopes

- Live-Wechselkursumrechnung von Fremdwährungen
- Export-Funktionen (PDF/Excel-Export der Auswertung)
- Jahresvergleiche, Sparziele/Budgets mit Warnungen
- Automatisiertes Ordner-Watching (Import wird manuell per Klick ausgelöst,
  kein Hintergrund-Polling)
- Multi-User / Auth (reines Einzelnutzer-Tool für Zuhause)

Diese Punkte können in einer späteren Iteration ergänzt werden.

## Offene Anpassungen (bewusst MVP)

Kategorienliste und Detailschärfe der PDF-Heuristik werden nach ersten echten
Importen iterativ angepasst (siehe Nutzerentscheidung: "passen wir später an").
