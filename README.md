# WEG Protokolle

Lokale Webanwendung zur Verwaltung und Archivierung von Beschlüssen aus Eigentümerversammlungen (WEG).

---

## Projektbeschreibung

Die Anwendung ermöglicht den Import, die Verwaltung und die Suche von WEG-Versammlungsprotokollen für mehrere Objekte. Beschlüsse werden per OCR aus PDF-Dokumenten extrahiert, in einer SQLite-Datenbank gespeichert und über eine webbasierte Oberfläche durchsucht, gefiltert und bearbeitet.

**Kernfunktionen:**
- OCR-basierter Textimport aus PDF-Protokollen (pypdf + Tesseract-Fallback für gescannte PDFs)
- Erzeugung durchsuchbarer PDFs mit Textlayer und Beirat-Markierungen
- Weboberfläche mit Suche, Filter, Diff-Viewer und Bearbeitung
- Mehrere Objekte und Hausverwaltungen gleichzeitig verwaltbar
- Beirat-Tracking mit Status (Offen / Erledigt) und Notizen
- Belegprüfungs-Verwaltung mit Dokument-Upload

---

## Dateistruktur

```
protokolle/
├── src/
│   ├── weg_to_db.py                 # PDF → OCR → SQLite-Import
│   ├── weg_protokoll_processor.py   # Erzeugt _durchsuchbar.pdf (OCR + Highlights)
│   └── weg_pdf_dump.py              # Hilfsscript: Rohtext eines PDFs ausgeben
├── input/                           # Eingabe-PDFs (nicht in GitHub)
├── output/                          # Verarbeitete PDFs (nicht in GitHub)
├── belegpruefung/                   # Hochgeladene Belege (nicht in GitHub)
├── analyse/                         # Analysen (nicht in GitHub)
├── weg_server.py                    # Lokaler HTTP-Server + REST-API
├── weg_app.html                     # Hauptanwendung – HTML-Struktur
├── weg_app.css                      # Hauptanwendung – Styles
├── weg_app.js                       # Hauptanwendung – JavaScript
├── weg_import.html                  # Legacy Import-Tool
├── weg_protokolle.db                # SQLite-Datenbank (nicht in GitHub)
├── requirements.txt                 # Python-Abhängigkeiten
├── WEG_Browser.command              # Doppelklick-Starter (macOS)
└── .gitignore
```

---

## Installation / Setup

### Voraussetzungen

- Python 3.10 oder höher
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (für gescannte PDFs)
- [ocrmypdf](https://ocrmypdf.readthedocs.io/) (für durchsuchbare PDFs)

### Einrichtung

```bash
# Repository klonen
git clone https://github.com/thlikowski/protokolle.git
cd protokolle

# Virtuelle Umgebung erstellen und aktivieren
python3 -m venv .venv
source .venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt
```

---

## Server starten

### Doppelklick (empfohlen)
`WEG_Browser.command` doppelklicken — startet Server und öffnet Browser automatisch.

### Manuell im Terminal
```bash
source .venv/bin/activate
python weg_server.py
```

Die Anwendung ist dann unter **http://127.0.0.1:8765** erreichbar.

---

## Workflow: Neues Protokoll importieren

### Variante A — Über die Web-Oberfläche (empfohlen)
1. **Bearbeiten** im oberen Menü → **Import**
2. PDF auswählen und hochladen
3. Server erzeugt automatisch `_durchsuchbar.pdf` (OCR + Textlayer) und importiert in die Datenbank

### Variante B — Kommandozeile
```bash
# Schritt 1: Durchsuchbares PDF erzeugen
python src/weg_protokoll_processor.py input/Frauentor_2026-04-16.pdf output/

# Schritt 2: In Datenbank importieren
python src/weg_to_db.py output/Frauentor_2026-04-16_durchsuchbar.pdf
```

### PDF austauschen (ohne DB-Änderung)
Auf der Protokollkarte den **⇄ PDF**-Button klicken — ersetzt nur die Datei, lässt alle Beschlüsse, Notizen und Status unverändert. Nützlich wenn nachträglich eine bessere Scan-Qualität vorliegt.

---

## Oberfläche

### Oberes Menü
| Bereich | Inhalt |
|---------|--------|
| **Protokolle** | Suche, Beschlüsse, Protokollkarten, Beirat, Notizen |
| **Bearbeiten** | PDF analysieren · Import · Manuell bearbeiten |
| **Belegprüfung** | Termine, Dokumente, Hausverwaltungs-Belege |

### Protokolle-View (innere Tabs)
- **Beschlüsse** — Tabellarische Liste aller Beschlüsse mit Volltext-Suche
- **Protokolle** — Kachelansicht aller Protokolle mit Schnellzugriff
- **Beirat** — Gefilterte Ansicht aller beirat-relevanten Beschlüsse
- **Notizen** — Freie Notizen zu Beschlüssen oder Objekten

### Sidebar-Filter (wirken auf alle inneren Tabs)
- Objekt / Hausverwaltung / Jahr
- Beirat-relevant (Dot-Filter)
- Status: Offen / Erledigt
- Filter zurücksetzen

### Bearbeiten-Tab (3 Modi)
- **PDF analysieren** — Lädt ein PDF und zeigt wortgenauen Diff gegen die Datenbank (keine DB-Änderung)
- **Import** — PDF hochladen → `_durchsuchbar.pdf` erzeugen → DB-Import
- **Manuell** — Beschlüsse einzeln anlegen, bearbeiten oder löschen

---

## OCR & PDF-Verarbeitung

### pypdf + Tesseract-Fallback
`weg_to_db.py` versucht zuerst Text per pypdf zu extrahieren. Wenn weniger als 200 Zeichen erkannt werden (gescanntes PDF), startet automatisch Tesseract OCR mit deutscher Sprachunterstützung.

### Durchsuchbare PDFs (`_durchsuchbar.pdf`)
`weg_protokoll_processor.py` erzeugt PDFs mit:
- OCR-Textlayer (via ocrmypdf)
- Farbiger Markierung beirat-relevanter Passagen

### Rebuild-Schutz
Manuell bearbeitete Beschluss-Felder werden in `beschluss_edits` gespeichert und beim erneuten Import nicht überschrieben. Mit `--force` kann dieser Schutz aufgehoben werden.

---

## Anpassung an eigene Protokoll-Layouts

Die Beschluss-Extraktion in `src/weg_to_db.py` ist auf konkrete Protokoll-Layouts zugeschnitten:

- **Format A / Format B** — zwei Erkennungsstrategien für unterschiedliche Layoutvarianten
- **Reguläre Ausdrücke** für TOP-Nummern, Beschlusstexte, Abstimmungsergebnisse
- **Dateinamens-Konvention:** `Objekt_JJJJ-MM-TT_bezeichnung.pdf`

Empfehlung: `src/weg_pdf_dump.py` nutzen, um den Rohtext eines PDFs zu prüfen, und die Regex-Muster in `weg_to_db.py` entsprechend anpassen.

---

## Technische Details

| Komponente | Technologie |
|-----------|-------------|
| Server | Python `http.server`, Port 8765, bindet auf `127.0.0.1` |
| Datenbank | SQLite (kein ORM) |
| Frontend | Vanilla HTML/CSS/JS (kein Framework) |
| OCR | pypdf + pytesseract + ocrmypdf |
| PDF-Erzeugung | reportlab |

---

## Hinweise

- `weg_protokolle.db`, alle PDFs sowie die Ordner `input/`, `output/`, `belegpruefung/` und `analyse/` sind nicht im Repository enthalten (persönliche Daten).
- Die Anwendung ist für den lokalen Betrieb auf einem Mac ausgelegt.
- Der Server verwendet `127.0.0.1` statt `localhost` um IPv6-Lookup-Verzögerungen auf macOS zu vermeiden.
