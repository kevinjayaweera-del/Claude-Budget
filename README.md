# Budget Tracker (privat, lokal)

Lokaler Budget-Tracker: Kontoauszüge (PDF/CSV) in `statements/` ablegen,
im Dashboard importieren/prüfen, Ausgaben nach Kategorie und Zeitraum
auswerten. Läuft komplett lokal, keine Cloud-Anbindung.

## Starten (Windows, empfohlen)

Doppelklick auf **`Budget-Tracker-starten.bat`** im Projektordner. Beim ersten
Start wird automatisch eine virtuelle Umgebung angelegt und alle
Abhängigkeiten installiert (dauert einen Moment); danach startet der Server
und das Dashboard öffnet sich automatisch im Browser
(http://127.0.0.1:5000/). Der Server läuft in einem separaten Fenster
("Budget Tracker Server") — zum Beenden dieses Fenster einfach schliessen.

## Einrichtung & Starten (manuell / andere Betriebssysteme)

```bash
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash; unter PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Vom Projekt-Hauptverzeichnis aus (wichtig: `-m server.app`, nicht `python server/app.py`,
damit das `server`-Package korrekt aufgelöst wird):

```bash
python -m server.app
```

Dashboard öffnen: http://127.0.0.1:5000/

## Navigation

Links steht eine feste Seitenleiste mit drei Bereichen: **Übersicht**
(Dashboard mit Buchungen, Filtern und Import), **Budget** (Kennzahlen,
Diagramme und Budgetlimiten pro Kategorie) und **Regeln** (gelernte
Kategorisierungs-Regeln verwalten). Auf schmalen Bildschirmen klappt die
Seitenleiste automatisch zu einer schmalen Icon-Leiste zusammen.

## Monatlicher Workflow

1. Neue Kontoauszüge (PDF/CSV) nach `statements/` kopieren (Unterordner pro
   Bank/Konto sind erlaubt, z.B. `statements/ZKB/`).
2. Im Dashboard auf "Neue Dateien importieren" klicken.
3. Erkannte Buchungen in der Korrektur-Tabelle prüfen und Kategorie/Betrag/
   Datum bei Bedarf anpassen.
4. "Import bestätigen" klicken — Buchungen erscheinen im Dashboard.

## Lernfähige Kategorisierung

Jede bestätigte Kategorie-Zuordnung verbessert künftige Importe: wird ein
automatischer Vorschlag unverändert bestätigt, steigt seine Konfidenz;
wird er korrigiert, sinkt sie und eine neue Regel für die richtige
Kategorie wird gelernt. Buchungen mit hoher Konfidenz (≥75%) tauchen gar
nicht erst in der Korrektur-Tabelle auf — nur unkategorisierte oder
unsichere Buchungen müssen geprüft werden. Unter "Regeln" (Seitenleiste)
lassen sich alle gelernten Regeln einsehen, umlenken oder löschen.

## Budget-Dashboard

Unter "Budget" (Seitenleiste) lassen sich pro Kategorie monatliche
Budgetlimiten festlegen. Das Dashboard zeigt für den gewählten Monat
Einnahmen/Ausgaben/Cashflow als Kennzahlen, die Ausgabenverteilung als
Donut-Chart, die Top-5-Ausgabenkategorien, den Budgetverbrauch pro
Kategorie als Fortschrittsbalken (grün/gelb/rot) sowie die
Cashflow-Entwicklung der letzten 12 Monate als Liniendiagramm.

## Tests ausführen

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Bekannte Grenzen (MVP)

- PDF-Erkennung ist heuristisch (Datum + Betrag pro Zeile) und nicht für
  jedes Bank-Layout perfekt — die Korrektur-Tabelle fängt Fehler ab.
- Keine Wechselkursumrechnung; Fremdwährungsbeträge werden im Original
  gespeichert.
- Kein Export, keine Jahresvergleiche (geplante spätere Erweiterungen).
