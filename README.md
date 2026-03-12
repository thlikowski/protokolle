# WEG Protokolle

Verwaltung und Archivierung von Beschlüssen aus Eigentümerversammlungen einer Wohnungseigentümergemeinschaft (WEG).

---

## Projektbeschreibung

Die Anwendung ermöglicht das Scannen, Importieren und Verwalten von WEG-Versammlungsprotokollen. Beschlüsse werden per OCR aus PDF-Dokumenten extrahiert, in einer Datenbank gespeichert und über eine webbasierte Oberfläche durchsucht und bearbeitet.

**Funktionen:**
- OCR-basierter Textimport aus gescannten Protokoll-PDFs
- Strukturierte Speicherung von Beschlüssen in einer SQLite-Datenbank
- Weboberfläche zur Verwaltung, Suche und Filterung von Beschlüssen
- Beirat- und Notizenverwaltung
- Statusverfolgung (Offen / In Arbeit / Erledigt)

---

## Dateistruktur

```
protokolle/
├── src/
│   ├── weg_protokoll_processor.py   # OCR / Textimport
│   └── weg_to_db.py                 # Datenbank-Import
├── input/                           # Eingabe-PDFs (nicht in GitHub)
├── output/                          # Verarbeitete Dateien (nicht in GitHub)
├── analyse/                         # Analysen (nicht in GitHub)
├── weg_server.py                    # Lokaler HTTP-Server
├── weg_app.html                     # Hauptanwendung (Browser)
├── weg_import.html                  # Import-Oberfläche
├── weg_protokolle.db                # SQLite-Datenbank (nicht in GitHub)
├── requirements.txt                 # Python-Abhängigkeiten
├── WEG_Starter.command              # Startmenü im Terminal
└── .gitignore
```

---

## Installation / Setup

### Voraussetzungen
- Python 3.10 oder höher
- pip

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

## Workflow / Bedienung

### 1. Neue Protokolle scannen
Gescannte PDFs in den Ordner `input/` legen.

### 2. OCR / Textimport
Extrahiert Text aus den PDFs und bereitet sie für den Import vor:
```bash
source .venv/bin/activate
python src/weg_protokoll_processor.py input/ output/
```

### 3. Datenbank-Import
Importiert die verarbeiteten Daten in die SQLite-Datenbank:
```bash
python src/weg_to_db.py output/
```

### 4. Server starten
Startet den lokalen Webserver:
```bash
python weg_server.py
```
Die Anwendung ist dann unter [http://localhost:8765](http://localhost:8765) erreichbar.


### Alternativ: Startmenü im Terminal
Doppelklick auf `WEG_Starter.command`

---

## Hinweise

- Die Dateien `weg_protokolle.db`, alle PDFs sowie die Ordner `input/`, `output/` und `analyse/` sind nicht im Repository enthalten, da sie persönliche Daten enthalten.
- Die Anwendung ist für den lokalen Betrieb auf einem Mac ausgelegt.
