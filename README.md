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

## Monatlicher Workflow

1. Neue Kontoauszüge (PDF/CSV) nach `statements/` kopieren (Unterordner pro
   Bank/Konto sind erlaubt, z.B. `statements/ZKB/`).
2. Im Dashboard auf "Neue Dateien importieren" klicken.
3. Erkannte Buchungen in der Korrektur-Tabelle prüfen, Kategorie/Betrag/Datum
   bei Bedarf anpassen oder Fehlzeilen löschen.
4. "Import bestätigen" klicken — Buchungen erscheinen im Dashboard.

## Lernfähige Kategorisierung

Jede bestätigte Kategorie-Zuordnung verbessert künftige Importe: wird ein
automatischer Vorschlag unverändert bestätigt, steigt seine Konfidenz;
wird er korrigiert, sinkt sie und eine neue Regel für die richtige
Kategorie wird gelernt. In der Korrektur-Tabelle zeigt ein grüner Punkt
neben der Kategorie "sicher" (≥75% Konfidenz), ein gelber Punkt "bitte
prüfen". Unter "Regeln verwalten" (Link im Dashboard-Header) lassen sich
alle Regeln einsehen, umlenken oder löschen.

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
- Kein Export, keine Jahresvergleiche, keine Budgets/Sparziele (geplante
  spätere Erweiterungen).
