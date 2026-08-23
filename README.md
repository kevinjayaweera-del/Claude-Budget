# Budget Tracker (privat, lokal)

Lokaler Budget-Tracker: Kontoauszüge (PDF/CSV) in `statements/` ablegen,
im Dashboard importieren/prüfen, Ausgaben nach Kategorie und Zeitraum
auswerten. Läuft komplett lokal, keine Cloud-Anbindung.

## Starten (Windows, empfohlen)

Doppelklick auf das **"Budget Tracker"**-Icon auf dem Desktop. Der Server
startet unsichtbar im Hintergrund (kein Konsolenfenster — läuft über
`pythonw.exe`) und das Dashboard öffnet sich automatisch im Browser
(http://127.0.0.1:5000/). Läuft der Server bereits (z.B. weil das Icon
versehentlich zweimal angeklickt wurde), öffnet ein erneuter Klick einfach
einen weiteren Browser-Tab statt eines zweiten Servers.

Zum Beenden: Task-Manager öffnen, nach "Python" (bzw. `pythonw.exe`) suchen
und den Prozess beenden — da kein Fenster sichtbar ist, gibt es sonst nichts
zum Schliessen.

Falls das Desktop-Icon fehlt oder neu erstellt werden soll:
`venv\Scripts\pythonw.exe start_app.pyw` als Ziel einer neuen Verknüpfung
verwenden, `app-icon.ico` als Icon.

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

Links steht eine feste Seitenleiste mit fünf Bereichen: **Übersicht**
(Dashboard mit Buchungen und Filtern), **Import** (neue Kontoauszüge
scannen und die Korrektur-Tabelle bestätigen), **Budget** (Kennzahlen,
Diagramme und Budgetlimiten pro Kategorie), **Regeln** (gelernte
Kategorisierungs-Regeln verwalten) und **Einstellungen** (Kategorien,
Automatisierung, Datenverwaltung, Darstellung). Auf schmalen Bildschirmen
klappt die Seitenleiste automatisch zu einer schmalen Icon-Leiste zusammen.

## Monatlicher Workflow

1. Neue Kontoauszüge (PDF/CSV) nach `statements/` kopieren (Unterordner pro
   Bank/Konto sind erlaubt, z.B. `statements/ZKB/`).
2. Unter "Import" (Seitenleiste) auf "Neue Dateien importieren" klicken.
   Bereits importierte Dateien (per Hash erkannt) werden automatisch
   übersprungen. Enthält der Scan zusätzlich einzelne Buchungen, die
   (gleicher Betrag, gleiche Beschreibung, gleiches Datum) schon
   vorhanden sind, erscheint vor dem eigentlichen Import eine Warnung mit
   der Anzahl gefundener Duplikate und neuer Buchungen — "Import
   fortsetzen" importiert nur die neuen Buchungen, "Import abbrechen"
   importiert nichts.
3. Erkannte Buchungen in der Korrektur-Tabelle prüfen und Kategorie/Betrag/
   Datum bei Bedarf anpassen.
4. "Import bestätigen" klicken — Buchungen erscheinen im Dashboard
   ("Übersicht").

## Lernfähige Kategorisierung

Jede bestätigte Kategorie-Zuordnung verbessert künftige Importe: wird ein
automatischer Vorschlag unverändert bestätigt, steigt seine Konfidenz;
wird er korrigiert, sinkt sie und eine neue Regel für die richtige
Kategorie wird gelernt. Buchungen mit hoher Konfidenz (Standard: ≥75%,
einstellbar unter "Einstellungen") tauchen gar nicht erst in der
Korrektur-Tabelle auf — nur unkategorisierte oder unsichere Buchungen
müssen geprüft werden. Die automatische Kategorisierung lässt sich unter
"Einstellungen" komplett deaktivieren. Unter "Regeln" (Seitenleiste)
lassen sich alle gelernten Regeln einsehen, umlenken oder löschen.

## Budget-Dashboard

Unter "Budget" (Seitenleiste) lassen sich pro Kategorie monatliche
Budgetlimiten festlegen. Das Dashboard zeigt für den gewählten Monat
Einnahmen/Ausgaben/Cashflow als Kennzahlen, die Ausgabenverteilung als
Donut-Chart, die Top-5-Ausgabenkategorien, den Budgetverbrauch pro
Kategorie als Fortschrittsbalken (grün/gelb/rot) sowie die
Cashflow-Entwicklung der letzten 12 Monate als Liniendiagramm.

## Einstellungen

Unter "Einstellungen" (Seitenleiste, Zahnrad-Symbol) lassen sich zentral
verwalten:

- **Kategorien**: neue Kategorien anlegen, umbenennen, löschen (bei
  Abhängigkeiten mit Sicherheitsabfrage — Buchungen und Regeln werden dann
  auf "Unkategorisiert" umgestellt, Budgets entfernt).
- **Automatische Kategorisierung**: ein-/ausschalten, Mindest-Konfidenz für
  die automatische Übernahme einstellen, Link zur Regel-Verwaltung.
- **Datenverwaltung**: gefiltert löschen (Zeitraum/Kategorie/Konto/Typ, mit
  optionalem Backup vorher), alle importierten Buchungen löschen (zum
  wiederholten Testen des Imports — Kategorien, gelernte Regeln, Budgets
  und Einstellungen bleiben erhalten) oder die gesamte Datenbank
  zurücksetzen.
- **Import & Export**: alle Daten als JSON exportieren. Import/
  Wiederherstellung ist als spätere Erweiterung vorgesehen.
- **Dashboard**: Standardzeitraum, der beim Öffnen automatisch vorausgewählt wird.
- **Allgemein**: Hell-/Dunkel-/System-Darstellung, Datenbankinformationen
  (Anzahl Buchungen, Kategorien, Regeln, Dateigrösse).

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
- Kein Import/Wiederherstellung aus einer Backup-/Export-Datei, keine
  Jahresvergleiche, keine Kategorie-Icons/Farben (geplante spätere
  Erweiterungen).
