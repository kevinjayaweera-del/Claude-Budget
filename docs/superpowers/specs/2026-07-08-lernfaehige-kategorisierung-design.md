# Lernfähige automatische Kategorisierung – Design

Status: Approved
Datum: 2026-07-08

## Ziel

Das bestehende regelbasierte Kategorisierungssystem (Keyword-Substring-Match,
siehe [2026-07-06-budget-tracker-design.md](2026-07-06-budget-tracker-design.md))
soll aus manuellen Kategorisierungen lernen, sodass beim Import neuer
Transaktionen im Zeitverlauf immer weniger manuelle Nacharbeit nötig ist.
Jede Regel bekommt einen aus ihrer Erfolgsgeschichte berechneten
Konfidenzwert; nur bei ausreichend hoher Konfidenz wird eine Kategorie
automatisch übernommen, sonst wird sie als korrigierbarer Vorschlag markiert.

Ausdrücklich referenziert und analysiert für dieses Design: Kevins reale
Kontoauszüge (ZKB CSV/PDF, Swisscard Cashback, Cornercard) — dieselbe
Datengrundlage wie beim Ableiten der 57 Start-Regeln in der vorigen
Iteration.

## Gewählter Ansatz

Von drei diskutierten Ansätzen (Häufigkeits-Lernen pro Empfänger,
erweiterte Keyword-Regeln mit Konfidenz-Tracking, voller
Multi-Feature-Scorer) wurde **erweiterte Keyword-Regeln mit
Konfidenz-Tracking** gewählt: baut auf dem bereits bewährten,
schnellen (O(Regelanzahl) statt O(Transaktionshistorie)) und in der
Regel-Verwaltungsseite direkt darstellbaren `category_rules`-Mechanismus
auf, statt ihn zu ersetzen.

## Datenmodell-Erweiterung

**`category_rules`** — neue Spalten:
- `match_count INTEGER NOT NULL DEFAULT 0` — wie oft die Regel angewendet
  und beim Bestätigen NICHT korrigiert wurde
- `correction_count INTEGER NOT NULL DEFAULT 0` — wie oft eine mit dieser
  Regel vorgeschlagene Kategorie beim Bestätigen manuell überschrieben wurde
- `iban TEXT` — nullable, für später vorbereitet. Aktuell liefert kein
  Dateiformat (ZKB CSV/PDF, Swisscard, Cornercard) eine IBAN pro Buchung;
  die Spalte wird nicht befüllt und beim Matching übersprungen, bis ein
  künftiges Format sie liefert.
- `is_seeded INTEGER NOT NULL DEFAULT 0` — unterscheidet vordefinierte
  Start-Regeln von gelernten Regeln (für die Verwaltungsseite)
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))`

Die 57 vordefinierten Regeln aus der vorigen Iteration werden beim Seeding
mit `is_seeded=1, match_count=3, correction_count=0` angelegt (siehe
Konfidenz-Berechnung unten) statt mit 0/0.

**`pending_transactions`** — neue Spalten:
- `suggested_category_id INTEGER REFERENCES categories(id)` — die
  ursprüngliche automatische Vorhersage zum Scan-Zeitpunkt, unveränderlich.
  Getrennt von `category_id` (das, was am Ende bestätigt wird), damit beim
  Bestätigen erkennbar ist, ob eine Korrektur stattgefunden hat.
- `suggested_rule_id INTEGER REFERENCES category_rules(id)` — nullable,
  die konkrete Regel, die `suggested_category_id` geliefert hat (falls
  eine gematcht hat). Notwendig, damit beim Bestätigen genau *diese*
  Regel belohnt (`match_count`) oder korrigiert (`correction_count`)
  werden kann — `suggested_category_id` allein reicht nicht, da
  theoretisch mehrere Regeln auf dieselbe Kategorie zeigen könnten und
  sonst unklar wäre, welche der Auslöser war.
- `category_confidence REAL` — berechneter Konfidenzwert (0.0–1.0) zum
  Scan-Zeitpunkt, nullable (kein Wert, wenn keine Regel gematcht hat)

**`transactions.manually_corrected`** (existiert bereits seit Task 1,
wurde aber nie gesetzt) wird beim Bestätigen jetzt tatsächlich befüllt:
`1`, wenn `category_id` beim Bestätigen von `suggested_category_id`
abweicht, sonst `0`.

## Normalisierung

Eine `normalize_description(text)`-Funktion wird sowohl beim Lernen einer
neuen Regel als auch beim Matching einer eingehenden Buchung angewendet:

1. Kleinschreibung
2. Deterministische Umlaut-Faltung: ä→ae, ö→oe, ü→ue, ß→ss
3. Bekannte Rauschmuster per Regex entfernen: Kartennummern
   (`nr\.?\s*xxxx\s*\d+`), Referenznummern (`auftrags-nr\.?\s*\S+`)
4. Mehrfache Leerzeichen zu einem zusammenfassen, trimmen

Das löst automatisch Schreibvarianten wie "BAECKEREI BODE" vs.
"Bäckerei ..." (Task der letzten Iteration, dort noch von Hand durch
Duplikat-Regeln gelöst).

## Keyword-Extraktion beim Lernen (ersetzt `_extract_keyword`)

Ersetzt die heutige Logik ("erstes Wort >3 Zeichen roh aus der
Beschreibung" — nachweislich fehleranfällig: bei "Einkauf ZKB Visa Debit
Card Nr. xxxx 7369, Apotheke..." würde bisher "Einkauf" gelernt, ein Wort,
das in praktisch jeder Kartenbuchung vorkommt und damit als Regel
schädlich unspezifisch wäre).

Neue Logik: aus dem normalisierten Text wird eine Stopwortliste entfernt
(Startliste, erweiterbar):
`einkauf, online-einkauf, belastung, gutschrift, zkb, visa, debit, card,
karte, mastercard, mobile, banking, auftraggeber, referenznummer, twint`.
Von den verbleibenden Wörtern (jeweils >3 Zeichen) wird das längste als
Keyword übernommen.

**"twint" bleibt bewusst auf der Stopwortliste für die Lern-Extraktion** —
sonst würde eine Korrektur bei z.B. "TWINT: NEUERHÄNDLER" versehentlich
die generische, aus der vorigen Iteration sorgfältig priorisierte
TWINT-Fallback-Regel (Privatüberweisungen) überschreiben. Als
vordefinierte Systemregel bleibt "twint" selbst weiterhin aktiv und
matchbar — nur die *automatische Lern-Extraktion* darf es nie als neues
Ziel-Keyword wählen.

## Konfidenz-Berechnung

Laplace-geglättete Erfolgsrate pro Regel:

```
confidence = (match_count + 1) / (match_count + correction_count + 2)
```

Eigenschaften: eine nagelneue gelernte Regel (0/0) startet bei 50%; eine
vordefinierte Regel (Seed: 3/0) startet bei 80%; mit wachsender Historie
nähert sich der Wert asymptotisch der beobachteten Erfolgsrate an, ohne
je exakt 0% oder 100% zu erreichen.

Die Kategorie-Auswahl selbst (welche Regel gewinnt bei mehreren
Treffern) bleibt unverändert: längstes passendes Keyword gewinnt
(`ORDER BY LENGTH(keyword) DESC`), wie in der vorigen Iteration
etabliert. Konfidenz ist eine zusätzliche, an die gewinnende Regel
angehängte Eigenschaft, kein zweites Auswahlkriterium.

## Lernen beim Bestätigen (`confirm_import`)

Pro Buchung, beim Bestätigen verglichen mit `suggested_category_id`
(die konkrete Regel wird über `suggested_rule_id` identifiziert):

- **Vorschlag akzeptiert** (`category_id == suggested_category_id` und
  `suggested_rule_id` ist gesetzt): `match_count` der Regel mit dieser
  `suggested_rule_id` +1.
- **Vorschlag korrigiert** (`category_id != suggested_category_id`,
  `suggested_rule_id` ist gesetzt): `correction_count` der Regel mit
  dieser `suggested_rule_id` +1 (ihre Konfidenz für diesen Fall sinkt);
  zusätzlich wird für die neue Kategorie eine Regel gelernt/aktualisiert
  (Keyword-Extraktion wie oben, `match_count` +1 bzw. neu angelegt mit
  `match_count=1`).
- **Vorher unkategorisiert, jetzt manuell zugewiesen** (`suggested_rule_id`
  ist NULL): neue Regel wird gelernt wie im Korrektur-Fall.

Bei Upsert einer bestehenden Regel wird `INSERT ... ON CONFLICT(keyword)
DO UPDATE` verwendet (nicht `INSERT OR REPLACE`), da REPLACE die Zeile
löschen und neu anlegen würde — das würde `match_count`/`correction_count`
bei jeder erneuten Bestätigung auf 0 zurücksetzen und die gesamte
Lernhistorie zerstören.

**Wiederkehrende Buchungen**: bewusst kein separater
Datums-/Betragsabstands-Algorithmus. `match_count` selbst ist das
Wiederkehrend-Signal — eine Regel, die mehrere Monate in Folge trifft,
hat naturgemäß hohe Konfidenz. Deckt den Kernfall (Miete, Abos,
Versicherungen) ab, ohne zusätzliche Komplexität für Datums-/
Betragsnäherung.

## Anwendung beim Import & UI

Schwellenwert (benannte Konstante, keine Magic Number im Code):
`CONFIDENCE_THRESHOLD = 0.75`

- Konfidenz ≥ 75%: Kategorie wird vorausgefüllt, **grün** markiert
  ("sicher").
- Konfidenz < 75% (aber ein Vorschlag existiert): Kategorie wird
  trotzdem vorausgefüllt, **gelb** markiert ("bitte prüfen").
- Kein Regel-Treffer: bleibt "Unkategorisiert" wie bisher, kein Badge.

In der Korrektur-Tabelle (`web/index.html`, `#pending-table`) bekommt
jede Zeile einen kleinen Farbpunkt neben dem Kategorie-Dropdown,
konsistent mit den bestehenden Kategorie-Farbpunkten aus dem
Dashboard-Redesign.

## Regel-Verwaltungsseite

Neue statische Seite `web/regeln.html` (+ `web/regeln.js`, teilt
`web/style.css`), verlinkt über einen Button im Dashboard-Header
(`web/index.html`).

**Zeigt:** Tabelle aller Regeln — Keyword, Zielkategorie, Konfidenz (%),
`match_count`, `correction_count`, Herkunft ("vordefiniert" / "gelernt"
über `is_seeded`).

**Aktionen:** Zielkategorie ändern, Regel löschen. Kein manuelles
Neuanlegen von Regeln in dieser Iteration — neue Regeln entstehen
ausschliesslich durchs Lernen aus bestätigten Kategorisierungen; die
Seite dient der Kontrolle und Korrektur, nicht der Neuanlage.

**Neue Endpunkte:**
- `GET /api/rules` — Liste aller Regeln mit Statistik und berechneter
  Konfidenz
- `PUT /api/rules/<id>` — Zielkategorie (und optional Keyword-Text)
  ändern
- `DELETE /api/rules/<id>` — Regel löschen

## ML-vorbereitete Architektur

Die Kategorisierungs-Logik wird hinter einer Schnittstelle gekapselt,
statt direkt in `app.py`/`import_service.py` verstreut zu bleiben:

```python
class Categorizer:
    def predict(self, description, amount_cents, currency, source) -> tuple[int, float, int | None]:
        """Returns (category_id, confidence, rule_id). rule_id is None
        when no rule matched (falls back to "Unkategorisiert", confidence 0.0)
        — callers persist rule_id as pending_transactions.suggested_rule_id
        so confirm_import can credit/blame the exact rule later."""

    def learn(self, description, category_id, was_correction: bool) -> None:
        ...
```

`RuleBasedCategorizer` ist für diese Iteration die einzige
Implementierung. Aufrufer kennen nur die Schnittstelle. Ein späteres
ML-Modell könnte als zweite Implementierung (`MLCategorizer`) dieselbe
Schnittstelle bedienen, ohne Änderungen an Aufrufstellen. Für jetzt:
reine Schnittstellen-Vorbereitung, kein ML-Code.

## Explizit außerhalb des Scopes dieser Iteration

- Echte Datums-/Betragsabstands-Erkennung für wiederkehrende Buchungen
  (siehe Entscheidung oben — `match_count` reicht als Signal)
- Manuelles Neuanlegen von Regeln auf der Verwaltungsseite (nur
  Ansehen/Bearbeiten/Löschen bestehender Regeln)
- Tatsächliche ML-Modell-Implementierung (nur Schnittstellen-Vorbereitung)
- IBAN-basiertes Matching (Spalte vorbereitet, aber kein Dateiformat
  liefert aktuell eine IBAN pro Buchung)
