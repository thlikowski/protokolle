#!/usr/bin/env python3
"""
WEG Protokoll → SQLite Datenbank (v2 – Hybrid Regex/LLM)
==========================================================
Zwei Verarbeitungs-Pipelines je nach Hausverwaltungsformat:

  FORMAT A (MM-Consult):   Rosengarten, DrKuelzStr
    TOP-Bezeichner:  "X )" oder "X  Y)" für Unter-TOPs
    Beschluss-Beginn: "Beschluss:"
    Abstimmung:      "Anzahl Ja Stimmen: / Er gebnis:"

  FORMAT B (La Casa/Bernhardt):  Frauentor, Mariental
    TOP-Bezeichner:  "zu TOP X:" oder "zu TOP X.Y:"
    Beschluss-Beginn: "Beschlussformulierung:" oder direkt
    Abstimmung:      "JA-Stimmen .../... angenommen/abgelehnt"

GPT-4o-mini wird eingesetzt für:
  1. OCR-Textreinigung (Wort-Splitting beheben: "Er gebnis" → "Ergebnis")
  2. Beschlusstext-Extraktion bei unklaren Grenzen

Verwendung:
    python3 weg_to_db.py output/                    # Ordner mit PDFs
    python3 weg_to_db.py output/ --db weg.db        # eigener DB-Pfad
    python3 weg_to_db.py protokoll.pdf              # einzelne Datei
    python3 weg_to_db.py output/ --rebuild          # DB aktualisieren (editierte Felder geschützt)
    python3 weg_to_db.py output/ --rebuild --force  # alles überschreiben inkl. manuelle Edits
    python3 weg_to_db.py output/ --no-llm           # nur Regex, kein GPT

Voraussetzungen:
    pip install pypdf openai
    export OPENAI_API_KEY="sk-..."
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

# ─── Konfiguration ────────────────────────────────────────────────────────────

DEFAULT_DB  = 'weg_protokolle.db'
LLM_MODEL   = 'gpt-4o-mini'
LLM_MAX_TOK = 2000

BEIRAT_TERMS = ['beirat', 'beiräte', 'verwaltungsbeirat', 'beirats']

OBJEKT_MAP = {
    'DrKuelzStr':  ('Dr.-Külz-Straße', 'MM-Consult'),
    'Rosengarten': ('Rosengarten',     'MM-Consult'),
    'Frauentor':   ('Am Frauentor',    'La Casa Hausverwaltung GmbH'),
    'Mariental':   ('Mariental',       'Bernhardt / La Casa Hausverwaltung GmbH'),
}

# Welches Format hat welches Objekt?
FORMAT_MAP = {
    'DrKuelzStr':  'A',
    'Rosengarten': 'A',
    'Frauentor':   'B',
    'Mariental':   'B',
}

# ─── Datenbankschema ──────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS protokolle (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dateiname           TEXT NOT NULL,
    pdf_pfad            TEXT NOT NULL UNIQUE,
    versammlungs_datum  TEXT,
    hausverwaltung      TEXT,
    weg_objekt          TEXT,
    ort                 TEXT,
    importiert_am       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beschluesse (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    protokoll_id     INTEGER NOT NULL REFERENCES protokolle(id),
    top_nr           TEXT NOT NULL,
    top_titel        TEXT,
    beschluss_text   TEXT,
    ja_stimmen       TEXT,
    nein_stimmen     TEXT,
    enthaltungen     TEXT,
    ergebnis         TEXT,
    beirat_relevant  INTEGER DEFAULT 0,
    seite            INTEGER
);

CREATE TABLE IF NOT EXISTS kommentare (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    beschluss_id   INTEGER NOT NULL REFERENCES beschluesse(id),
    kommentar_text TEXT,
    status         TEXT DEFAULT 'offen',
    link           TEXT,
    erstellt_am    TEXT NOT NULL,
    geaendert_am   TEXT
);

CREATE TABLE IF NOT EXISTS beschluss_edits (
    beschluss_id  INTEGER NOT NULL REFERENCES beschluesse(id),
    feld          TEXT NOT NULL,
    editiert_am   TEXT NOT NULL,
    PRIMARY KEY (beschluss_id, feld)
);
"""

# ─── LLM-Client ───────────────────────────────────────────────────────────────

_openai_client = None

LLM_TIMEOUT = 30  # Sekunden pro API-Aufruf


def get_llm_client():
    global _openai_client
    if _openai_client is None:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(timeout=LLM_TIMEOUT)  # liest OPENAI_API_KEY aus Umgebung
        except ImportError:
            print("  ⚠  openai nicht installiert → pip install openai")
            return None
        except Exception as e:
            print(f"  ⚠  OpenAI-Client Fehler: {e}")
            return None
    return _openai_client


def llm_request(prompt: str, use_llm: bool = True) -> str | None:
    """GPT-4o-mini Anfrage. Gibt None zurück wenn LLM deaktiviert oder Fehler."""
    if not use_llm:
        return None
    client = get_llm_client()
    if not client:
        return None
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0,
            max_tokens=LLM_MAX_TOK,
            timeout=LLM_TIMEOUT,  # zusätzlich auf Request-Ebene
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ⚠  LLM-Fehler: {e}")
        return None


# ─── OCR-Textreinigung per LLM ────────────────────────────────────────────────

def llm_clean_ocr(raw_text: str, use_llm: bool = True) -> str:
    """
    Bereinigt OCR-Artefakte via GPT-4o-mini.
    Fokus: Wort-Splitting ("Er gebnis" → "Ergebnis"),
           Extra-Leerzeichen, kaputte Umlaute.
    Struktur (Zeilenumbrüche, Zahlen) bleibt erhalten.
    """
    if not use_llm or len(raw_text.strip()) < 50:
        return raw_text

    prompt = f"""Du bekommst den rohen OCR-Text eines deutschen WEG-Protokolls (Wohnungseigentümerversammlung).
Der Text enthält typische OCR-Fehler:
- Wörter sind durch Leerzeichen gespalten: "Er gebnis" statt "Ergebnis", "V erwalter" statt "Verwalter", "Jahr esabr echnung" statt "Jahresabrechnung"
- Extra-Leerzeichen zwischen allen Wörtern: "Die  Eigentümer gemeinschaft  beschließt"
- Gelegentlich falsche Zeichen: "m?" statt "m²"

Deine Aufgabe:
1. Korrigiere gespaltene Wörter (erkenne deutsche Wörter die durch Leerzeichen getrennt wurden)
2. Normalisiere mehrfache Leerzeichen auf einfache Leerzeichen
3. Korrigiere offensichtliche Einzelzeichen-Fehler (m? → m²)
4. BEHALTE bei: alle Zeilenumbrüche, alle Zahlen exakt, alle Satzzeichen, die gesamte Struktur
5. VERÄNDERE NICHT: Eigennamen, Zahlen, TOP-Nummern, das Layout

Gib NUR den korrigierten Text zurück, ohne Erklärungen.

OCR-Text:
{raw_text[:3000]}"""

    result = llm_request(prompt, use_llm)
    return result if result else raw_text


# ─── Text-Extraktion aus PDF ──────────────────────────────────────────────────

def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Gibt Liste von (Seitennr, roher_Text) zurück."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ''
        pages.append((i + 1, text))
    return pages


def extract_pages_tesseract(pdf_path: Path) -> list[tuple[int, str]]:
    """Fallback für eingescannte PDFs: Seiten als Bilder → Tesseract-OCR (Deutsch)."""
    from pdf2image import convert_from_path
    import pytesseract
    images = convert_from_path(str(pdf_path), dpi=300)
    pages = []
    for i, img in enumerate(images):
        text = pytesseract.image_to_string(img, lang='deu')
        pages.append((i + 1, text))
    return pages


def pages_to_text(pages: list[tuple[int, str]]) -> str:
    """Fügt Seiten mit Markern zusammen."""
    parts = []
    for nr, text in pages:
        parts.append(f'\n[SEITE {nr}]\n{text}')
    return '\n'.join(parts)


def normalize_spaces(text: str) -> str:
    """
    Basis-Normalisierung: mehrfache Leerzeichen → eins,
    aber Zeilenumbrüche bleiben erhalten (wichtig für Struktur-Erkennung).
    """
    # Seiten-Marker schützen
    parts = re.split(r'(\[SEITE \d+\])', text)
    result = []
    for part in parts:
        if re.match(r'\[SEITE \d+\]', part):
            result.append('\n\n' + part + '\n')
        else:
            # Mehrfache Spaces → eins (aber KEINE Newlines anfassen)
            cleaned = re.sub(r'[ \t]{2,}', ' ', part)
            result.append(cleaned)
    return ''.join(result)


def get_seite_func(text: str):
    """Gibt Funktion zurück die für eine Position die Seitennr liefert."""
    seiten_pos = {}
    for m in re.finditer(r'\[SEITE (\d+)\]', text):
        seiten_pos[m.start()] = int(m.group(1))

    def get_seite(pos: int) -> int:
        seite = 1
        for p, s in sorted(seiten_pos.items()):
            if p <= pos:
                seite = s
        return seite
    return get_seite


# ─── Gemeinsame Hilfsfunktionen ───────────────────────────────────────────────

def is_beirat_relevant(text: str) -> int:
    """1 wenn Beirat-Begriff im Text, aber nicht nur Entlastung."""
    tl = text.lower()
    if not any(t in tl for t in BEIRAT_TERMS):
        return 0
    # Entlastungs-False-Positive rausfiltern
    if re.search(r'erteilt dem (?:Verwaltungs)?beirat.{0,30}Entlastung',
                 text, re.IGNORECASE):
        return 0
    return 1


def clean_beschluss_text(text: str) -> str:
    """Whitespace normalisieren, Abstimmungsblock abschneiden."""
    # Alles ab Abstimmungsblock entfernen
    cut = re.search(
        r'(Anzahl\s+Ja\s+Stimmen|JA.Stimmen|Abstimm(?:er)?gebnis)',
        text, re.IGNORECASE
    )
    if cut:
        text = text[:cut.start()]
    return re.sub(r'\s+', ' ', text).strip()


# ─── FORMAT A: MM-Consult (Rosengarten, DrKuelzStr) ───────────────────────────
#
# TOP-Bezeichner:   "X )" oder "X  Y)" (Leerzeichen inkonsistent!)
# Beschluss-Beginn: "Beschluss:"
# Abstimmung:       "Anzahl Ja Stimmen: XXXX"
#                   "Anzahl Nein Stimmen: XXXX"
#                   "Anzahl Enthaltungen: XXXX"
#                   "Er gebnis: Beschluss angenommen und verkündet"
# Unter-TOPs:       "4 1)", "42)", "4  3)" → normalisieren zu "4.1" etc.

def normalize_top_nr_a(raw: str) -> str:
    """
    Normalisiert MM-Consult TOP-Nummern:
    "4 1" → "4.1", "42" wenn Zeichen 0 eine Zahl > 1stellig ist → "4.2"
    Aber "42" als eigenständige TOP → bleibt "42" (Kontext nötig)
    Einfache Heuristik: wenn 2+ Ziffern und erste Ziffer < zweite Gruppe → Unter-TOP
    """
    raw = raw.strip()
    # "4 1", "4  3" etc. → "4.1", "4.3"
    m = re.match(r'^(\d+)\s+(\d+)$', raw)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return raw


# Regex für TOP-Zeile Format A:
# Matcht: "5 )", "6) ", "4 1)", "42)", "4  3)", "11 A:", "11B:"
# Gruppe 1: Haupt-Nr, Gruppe 2: optional Unter-Nr (1-stellig!) oder Buchstabe, Gruppe 3: Titel
# WICHTIG: Haupt-Nr max 2-stellig (verhindert Jahreszahlen 2018,2019...)
#          Unter-Nr max 1-stellig (verhindert "45" → TOP 45 statt 4.5)
# Regex für TOP-Zeile Format A – drei Varianten für Unter-TOP-Notation:
# Variante 1 (Rohtext):    "4 1)"  "4  3)"  mit Leerzeichen (OCR-Artefakt)
# Variante 2 (Rohtext):    "41)"   ohne Leerzeichen (OCR-Artefakt, Leerzeichen verschluckt)
# Variante 3 (LLM-Output): "4.1)"  "4.1."   mit Punkt (LLM normalisiert zu Punkt-Notation)
# Gruppe 1: Haupt-Nr  |  Gruppe 2: Unter-Nr via Leerzeichen  |  Gruppe 3: Unter-Nr via Punkt
RE_TOP_A = re.compile(
    r'(?m)^[ \t]*'
    r'(\d{1,2})'                         # Haupt-Nummer (max 2 Stellen)
    r'(?:'
        r'\s{0,3}(\d{1}|[AB])'          # Variante 1+2: Leerzeichen-Notation "4 1)" 
        r'|'
        r'\.(\d{1})'                     # Variante 3: Punkt-Notation "4.1)" (LLM-Output)
    r')?'
    r'\s*[).]'                            # ) oder .
    r'[ \t]*([^\n]{0,120})',             # Titel (Rest der Zeile) – Gruppe 4
)

# Jahreszahlen die als Fake-TOPs aus Datumsangaben im Text entstehen
FAKE_TOP_JAHRE = {str(y) for y in range(2010, 2030)}

# Regex für Abstimmung Format A
RE_ABSTIMMUNG_A = re.compile(
    r'Anzahl\s+Ja\s+Stimmen\s*[:\s]+([0-9.,]+).*?'
    r'Anzahl\s+Nein\s+Stimmen\s*[:\s]+([0-9.,]+).*?'
    r'Anzahl\s+Enthaltungen\s*[:\s]+([0-9.,]+).*?'
    r'Er?\s*gebnis\s*[:\s]+([^\n]+)',
    re.IGNORECASE | re.DOTALL
)

# Regex für Beschlusstext-Beginn Format A
RE_BESCHLUSS_A = re.compile(
    r'Beschluss\s*:\s*(.+?)(?='
    r'Anzahl\s+Ja\s+Stimmen'
    r'|\Z)',
    re.IGNORECASE | re.DOTALL
)


def extract_beschluesse_format_a(text: str, use_llm: bool = True) -> list:
    """Extraktion für MM-Consult Format."""
    get_seite = get_seite_func(text)
    results = []

    def is_fake_top(haupt_nr, unter_nr, titel_raw):
        """Gibt True zurück wenn dieser Match kein echter TOP ist."""
        titel_check = titel_raw.strip()
        # Jahreszahlen aus Datumsangaben
        if haupt_nr in FAKE_TOP_JAHRE and not unter_nr:
            return True
        # Datum-Muster im Titel: "04.2009 fortgeschrieben", "11.2025 gekündigt"
        if re.match(r'^\d{1,2}\.\d{2,4}', titel_check):
            return True
        # Titel nur aus Zahlen/Satzzeichen → kein echter TOP-Titel
        if re.match(r'^[\d.,\s/\-€]+$', titel_check) and len(titel_check) < 25:
            return True
        return False

    # Phase 1: Alle rohen Matches sammeln, Fake-TOPs VOR Block-Bildung filtern
    all_raw = list(RE_TOP_A.finditer(text))

    # Phase 2: Ermittle welche Haupt-TOPs tatsächlich Unter-TOPs haben
    # Methode A: Explizite Leerzeichen- oder Punkt-Notation (sicherste Methode)
    #   "4 1)", "4  3)", "4.1)", "4.2)" → direkt erkennbar
    # Methode B: Mehrere zweistellige TOPs XY mit gleicher Haupt-Ziffer X
    #   "41)","42)","43)","44)" → LLM hat Leerzeichen entfernt
    #   Bedingung: X muss AUCH als einstelliger TOP "X)" im Text vorkommen
    #   → verhindert dass echte TOPs "11)","12)","13)" fälschlich zu "1.1","1.2","1.3" werden
    RE_UNTER_EXPLIZIT = re.compile(r'(?m)^\s*(\d)(?:\s+(\d)|\.(\d))\s*[).]')
    haupt_mit_unter = set(m.group(1) for m in RE_UNTER_EXPLIZIT.finditer(text))
    # Methode B: X muss als eigenständiger TOP "X)" existieren (Gliederungs-TOP)
    _einstellige = set(m.group(1) for m in re.finditer(r'(?m)^\s*([1-9])\s*\)', text))
    _von_erste = {}
    for m in re.finditer(r'(?m)^\s*([1-9])([1-9])\s*\)', text):
        _von_erste.setdefault(m.group(1), set()).add(m.group(2))
    for erste, zweite_set in _von_erste.items():
        # Zusatzbedingung: erste Ziffer >= 4
        # (Aus Analyse aller Protokolle: Unter-TOPs gibt es nur unter TOP 4+,
        #  nie unter TOP 1/2/3 — verhindert 11→1.1, 12→1.2, 13→1.3)
        if len(zweite_set) >= 2 and erste in _einstellige and int(erste) >= 4:
            haupt_mit_unter.add(erste)

    matches = []
    for m in all_raw:
        h = m.group(1)
        u = m.group(2) or m.group(3) or ''  # Leerzeichen- oder Punkt-Unter-Nr
        t = (m.group(4) or '').strip()        # Titel ist jetzt Gruppe 4
        if not is_fake_top(h, u, t):
            matches.append(m)

    if not matches:
        return results

    for i, m in enumerate(matches):
        haupt_nr  = m.group(1)
        unter_nr  = m.group(2) or m.group(3) or ''  # Leerzeichen- oder Punkt-Notation
        titel_raw = (m.group(4) or '').strip()

        # Unter-TOP-Auflösung für zweistellige Nummern ohne Leerzeichen
        # "42)" → 4.2  NUR wenn Haupt-TOP "4" bekanntermaßen Unter-TOPs hat
        # "11)" → 11   wenn "1" keine Unter-TOPs hat (echte TOP 11)
        if len(haupt_nr) == 2 and not unter_nr:
            erste, zweite = haupt_nr[0], haupt_nr[1]
            # Zweite Ziffer darf nicht 0 sein ("10" = echter TOP 10)
            if erste in haupt_mit_unter and zweite != '0':
                haupt_nr = erste
                unter_nr = zweite

        # TOP-Nummer normalisieren
        if unter_nr:
            top_nr = f"{haupt_nr}.{unter_nr}"
        else:
            top_nr = haupt_nr

        # Titel bereinigen (OCR-Artefakte)
        titel = re.sub(r'\s+', ' ', titel_raw).strip()
        # Titel endet vor erstem Newline
        titel = titel.split('\n')[0].strip()

        # Block bis nächsten TOP
        start = m.start()
        end   = matches[i+1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        seite = get_seite(start)

        # Beschlusstext extrahieren
        bm = RE_BESCHLUSS_A.search(block)
        beschluss_text = ''
        if bm:
            beschluss_text = clean_beschluss_text(bm.group(1))

        # Abstimmung extrahieren
        ja = nein = enth = ergebnis = ''
        am = RE_ABSTIMMUNG_A.search(block)
        if am:
            ja       = am.group(1).strip()
            nein     = am.group(2).strip()
            enth     = am.group(3).strip()
            ergebnis_raw = re.sub(r'\s+', ' ', am.group(4)).strip()
            # Ergebnis normalisieren
            if re.search(r'angenommen', ergebnis_raw, re.I):
                ergebnis = 'angenommen'
            elif re.search(r'abgelehnt|nicht\s+beschlossen|vertagt', ergebnis_raw, re.I):
                ergebnis = 'abgelehnt'
            else:
                ergebnis = ergebnis_raw[:80]
        else:
            # Fallback: einfachere Muster
            if re.search(r'angenommen', block, re.I):
                ergebnis = 'angenommen'
            elif re.search(r'abgelehnt|nicht\s+beschlossen', block, re.I):
                ergebnis = 'abgelehnt'
            elif re.search(r'zurück\s*ge\s*zogen|zurückgezogen', block, re.I):
                ergebnis = 'zurückgezogen'

        # Nur TOPs mit Beschlussinhalt aufnehmen
        # (TOPs ohne "Beschluss:" und ohne Abstimmung → reine Berichte)
        hat_inhalt = bool(beschluss_text or ja or ergebnis)
        if not hat_inhalt:
            continue

        # LLM-Fallback: wenn Beschlusstext leer aber Block hat Inhalt
        if not beschluss_text and len(block) > 100 and use_llm:
            beschluss_text = llm_extract_beschlusstext(block, top_nr, titel)

        beirat = is_beirat_relevant(block)

        results.append({
            'top_nr':          top_nr,
            'top_titel':       titel,
            'beschluss_text':  beschluss_text,
            'ja_stimmen':      ja,
            'nein_stimmen':    nein,
            'enthaltungen':    enth,
            'ergebnis':        ergebnis,
            'beirat_relevant': beirat,
            'seite':           seite,
        })

    return results


# ─── FORMAT B: La Casa / Bernhardt (Frauentor, Mariental) ─────────────────────
#
# TOP-Bezeichner:   "zu TOP X:" oder "zu TOP X.Y:" (mit/ohne Leerzeichen)
#                   auch "TOP X:" ohne "zu"
# Beschluss-Beginn: "Beschlussformulierung:" oder direkt Fließtext
# Abstimmung:       "JA-Stimmen  XXX/YYY  Der Beschlussantrag wurde somit"
#                   "NEIN-Stimmen  ........  x angenommen"
#                   "Stimmenthaltungen  ....  □ abgelehnt"
# Unter-TOPs:       "zu TOP 2.1:", "zu TOP 2. 2:", "Beschlussformulierung 6.1:"

RE_TOP_B = re.compile(
    r'(?m)^[ \t]*'
    r'(?:zu\s+)?'                           # optionales "zu "
    r'T\s*O\s*P\s*'                          # "TOP" (mit OCR-Spaces, auch ohne Leerzeichen davor)
    r'(\d+(?:\s*[.\s]\s*\d+)?)'             # Nummer: "1", "2.1", "2. 2"
    r'\s*[:\-]'                             # Trennzeichen
    r'[ \t]*([^\n]{0,150})',                # Titel
)

# Unter-TOP via "Beschlussformulierung X.Y:"
RE_UNTER_TOP_B = re.compile(
    r'(?m)^[ \t]*'
    r'Beschlussformulierung\s+'
    r'(\d+\.\d+)\s*[:\-]'
    r'[ \t]*([^\n]{0,150})',
    re.IGNORECASE
)

RE_ABSTIMMUNG_B = re.compile(
    r'J\s*A\s*[-–]\s*Stimmen\s+'           # "JA-Stimmen" (mit OCR-Spaces)
    r'([0-9.,/]+)'                          # Anzahl: "771/771" oder "846/846"
    r'.*?'
    r'(?:'
    r'(x|X)\s+angenommen'                  # "x angenommen"
    r'|angenommen'
    r')',
    re.IGNORECASE | re.DOTALL
)

RE_ABSTIMMUNG_B_ABGELEHNT = re.compile(
    r'J\s*A\s*[-–]\s*Stimmen.*?'
    r'(?:x|X)\s+abgelehnt',
    re.IGNORECASE | re.DOTALL
)

RE_BESCHLUSS_B = re.compile(
    r'(?:'
    r'Beschlussformuli?erung\s*(?:\d+\.\d+\s*)?[:\-]\s*'  # "Beschlussformulierung:"
    r'|beschlie[ßs]t\s*,?\s*'                              # "beschließt,"
    r'|Beschluss\s*[:\-]\s*'                               # "Beschluss:"
    r')'
    r'(Die\s+Eigentümer.+?)(?='
    r'J\s*A\s*[-–]\s*Stimmen'
    r'|Abstimm(?:er)?gebnis'
    r'|(?:zu\s+)?T\s*O\s*P\s+\d'
    r'|\Z)',
    re.IGNORECASE | re.DOTALL
)


def normalize_top_nr_b(raw: str) -> str:
    """Normalisiert Format-B TOP-Nummern: "2. 2" → "2.2", "2 1" → "2.1"."""
    raw = re.sub(r'\s+', '', raw)        # alle Spaces raus
    raw = re.sub(r'\.(\d)', r'.\1', raw) # ".1" bleibt ".1"
    return raw


def _clean_stimmen(val: str) -> str:
    """Leert Werte die nur aus Füllpunkten bestehen ('.......' → '')."""
    return '' if re.match(r'^[.\s]+$', val) else val


def extract_abstimmung_b(block: str) -> tuple:
    """Extrahiert Abstimmungsergebnis aus Format-B Block."""
    ja = nein = enth = ergebnis = ''

    # JA-Stimmen Zahl - Format: "771/771" oder "5.897,72/10.000" oder "846/846"
    m_ja = re.search(r'J\s*A\s*[-–]?\s*Stimmen\s+([0-9][0-9.,/]*[0-9])', block, re.I)
    if m_ja:
        ja = m_ja.group(1).strip()

    # NEIN-Stimmen (oft leer/Punkte bei 0)
    m_nein = re.search(r'NEIN\s*[-–]?\s*St[iI]mmen\s+([0-9.,/]+)', block, re.I)
    if m_nein:
        nein = _clean_stimmen(m_nein.group(1).strip())

    # Enthaltungen
    m_enth = re.search(r'Stimmenthaltungen\s+([0-9.,/]+)', block, re.I)
    if m_enth:
        enth = _clean_stimmen(m_enth.group(1).strip())

    # Ergebnis – abgelehnt zuerst prüfen, da "□ angenommen\nx abgelehnt"
    # sonst fälschlich als angenommen erkannt wird
    if RE_ABSTIMMUNG_B_ABGELEHNT.search(block):
        ergebnis = 'abgelehnt'
    elif RE_ABSTIMMUNG_B.search(block):
        ergebnis = 'angenommen'
    elif re.search(r'vertagt|zur[uü]ck\s*ge\s*zogen', block, re.I):
        ergebnis = 'vertagt'
    elif re.search(r'abgelehnt', block, re.I):
        ergebnis = 'abgelehnt'
    elif re.search(r'angenommen', block, re.I):
        ergebnis = 'angenommen'

    return ja, nein, enth, ergebnis


def extract_beschluesse_format_b(text: str, use_llm: bool = True) -> list:
    """Extraktion für La Casa / Bernhardt Format."""
    get_seite = get_seite_func(text)
    results = []

    # Haupt-TOPs + Unter-TOPs sammeln und nach Position sortieren
    all_matches = []

    for m in RE_TOP_B.finditer(text):
        nr_raw = normalize_top_nr_b(m.group(1))
        titel  = re.sub(r'\s+', ' ', m.group(2) or '').strip()
        all_matches.append((m.start(), nr_raw, titel))

    for m in RE_UNTER_TOP_B.finditer(text):
        nr_raw = normalize_top_nr_b(m.group(1))
        titel  = re.sub(r'\s+', ' ', m.group(2) or '').strip()
        all_matches.append((m.start(), nr_raw, titel))

    all_matches.sort(key=lambda x: x[0])
    if not all_matches:
        return results

    for i, (start, top_nr, titel) in enumerate(all_matches):
        end   = all_matches[i+1][0] if i + 1 < len(all_matches) else len(text)
        block = text[start:end]
        seite = get_seite(start)

        # Beschlusstext
        bm = RE_BESCHLUSS_B.search(block)
        beschluss_text = ''
        if bm:
            beschluss_text = clean_beschluss_text(bm.group(1))

        # Abstimmung
        ja, nein, enth, ergebnis = extract_abstimmung_b(block)

        # Nur TOPs mit Beschlussinhalt
        hat_inhalt = bool(beschluss_text or ja or ergebnis)
        if not hat_inhalt:
            continue

        # LLM-Fallback
        if not beschluss_text and len(block) > 100 and use_llm:
            beschluss_text = llm_extract_beschlusstext(block, top_nr, titel)

        beirat = is_beirat_relevant(block)

        results.append({
            'top_nr':          top_nr,
            'top_titel':       titel,
            'beschluss_text':  beschluss_text,
            'ja_stimmen':      ja,
            'nein_stimmen':    nein,
            'enthaltungen':    enth,
            'ergebnis':        ergebnis,
            'beirat_relevant': beirat,
            'seite':           seite,
        })

    return results


# ─── LLM-Fallback: Beschlusstext-Extraktion ───────────────────────────────────

def llm_extract_beschlusstext(block: str, top_nr: str, titel: str,
                               use_llm: bool = True) -> str:
    """Extrahiert Beschlusstext via LLM wenn Regex nichts gefunden hat."""
    if not use_llm:
        return ''

    prompt = f"""Du analysierst einen Abschnitt eines deutschen WEG-Protokolls (Wohnungseigentümerversammlung).

Tagesordnungspunkt: {top_nr} – {titel}

Text des Abschnitts:
\"\"\"
{block[:1500]}
\"\"\"

Aufgabe: Extrahiere NUR den offiziellen Beschlusstext (was die Eigentümergemeinschaft beschlossen hat).
- Beginnt meist mit "Die Eigentümergemeinschaft beschließt..." oder "Der Verwalter wird beauftragt..."
- Endet vor dem Abstimmungsergebnis
- Lass Diskussionsinhalte und Berichte weg
- Wenn kein Beschluss vorhanden: antworte nur mit "KEIN_BESCHLUSS"

Antworte NUR mit dem Beschlusstext, ohne Einleitung."""

    result = llm_request(prompt, use_llm)
    if not result or result.strip() == 'KEIN_BESCHLUSS':
        return ''
    return re.sub(r'\s+', ' ', result).strip()[:500]


# ─── Metadaten ────────────────────────────────────────────────────────────────

def extract_datum(text: str, dateiname: str = '') -> str:
    patterns = [
        r'vom\s+(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})',
        r'am\s+(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})',
        r'(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})',
    ]
    for pat in patterns:
        m = re.search(pat, text[:500], re.IGNORECASE)
        if m:
            return re.sub(r'\s+', '', m.group(1))
    # Fallback aus Dateiname
    parts = Path(dateiname).stem.split('_')
    if len(parts) >= 2:
        bits = parts[1].split('-')
        if len(bits) == 3:
            return f"{bits[2]}.{bits[1]}.{bits[0]}"
    return ''


def extract_ort(text: str) -> str:
    m = re.search(r'in\s+\d{5}\s+([A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)',
                  text[:500])
    return m.group(1) if m else ''


def meta_from_filename(dateiname: str) -> tuple:
    prefix = Path(dateiname).name.split('_')[0]
    return OBJEKT_MAP.get(prefix, ('', ''))


def format_from_filename(dateiname: str) -> str:
    prefix = Path(dateiname).name.split('_')[0]
    return FORMAT_MAP.get(prefix, 'B')  # Default: Format B


# ─── Datenbank ────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ─── Haupt-Verarbeitung ───────────────────────────────────────────────────────

def get_edited_fields(cur, beschluss_id: int) -> set:
    """Gibt die Menge der manuell editierten Felder für einen Beschluss zurück."""
    rows = cur.execute(
        "SELECT feld FROM beschluss_edits WHERE beschluss_id = ?", (beschluss_id,)
    ).fetchall()
    return {r['feld'] for r in rows}


def analyse_pdf(pdf_path: Path, filename_hint: str = '', use_llm: bool = True) -> dict:
    """
    Analysiert eine PDF und gibt extrahierte Daten zurück – ohne DB-Zugriff.
    Returns: {
        'protokoll': {dateiname, versammlungs_datum, hausverwaltung, weg_objekt, ort,
                      format, machine_readable},
        'beschluesse': [{top_nr, top_titel, beschluss_text, ja_stimmen, ...}, ...]
    }
    """
    fname = filename_hint or pdf_path.name

    pages    = extract_pages(pdf_path)
    raw_text = pages_to_text(pages)

    if len(raw_text.strip()) < 200:
        # Eingescannte PDF ohne Textlayer → Tesseract-Fallback
        print(f"  Wenig pypdf-Text ({len(raw_text.strip())} Zeichen), starte Tesseract-OCR...")
        pages    = extract_pages_tesseract(pdf_path)
        raw_text = pages_to_text(pages)
        if len(raw_text.strip()) < 200:
            raise ValueError(
                f'Zu wenig Text nach Tesseract-OCR ({len(raw_text.strip())} Zeichen) – '
                f'PDF unlesbar oder leer?'
            )

    sample        = min(3, len(pages))
    avg_chars     = sum(len(t.strip()) for _, t in pages[:sample]) / sample
    machine_readable = avg_chars >= 200

    if use_llm and not machine_readable:
        print(f"  LLM:    OCR-Bereinigung ({len(pages)} Seiten)...", end=' ', flush=True)
        cleaned_pages = []
        for nr, page_text in pages:
            if len(page_text.strip()) > 50:
                cleaned_pages.append((nr, llm_clean_ocr(page_text, use_llm)))
            else:
                cleaned_pages.append((nr, page_text))
        text = pages_to_text(cleaned_pages)
        print("✓")
    else:
        text = raw_text

    text = normalize_spaces(text)

    objekt, hv = meta_from_filename(fname)
    fmt        = format_from_filename(fname)
    datum      = extract_datum(text, fname)
    ort        = extract_ort(text)

    if fmt == 'A':
        beschluesse = extract_beschluesse_format_a(text, use_llm)
    else:
        beschluesse = extract_beschluesse_format_b(text, use_llm)

    return {
        'protokoll': {
            'dateiname':           Path(fname).name,
            'versammlungs_datum':  datum,
            'hausverwaltung':      hv,
            'weg_objekt':          objekt,
            'ort':                 ort,
            'format':              fmt,
            'machine_readable':    machine_readable,
        },
        'beschluesse': beschluesse,
    }


def process_pdf(pdf_path: Path, db_path: Path,
                rebuild: bool = False, use_llm: bool = True,
                force: bool = False) -> dict:
    conn = init_db(db_path)
    cur  = conn.cursor()

    # Bereits in DB?
    cur.execute("SELECT id FROM protokolle WHERE pdf_pfad = ?", (str(pdf_path),))
    existing = cur.fetchone()

    if existing and not rebuild:
        print(f"  ⚠  Bereits in DB – übersprungen (--rebuild zum Aktualisieren)")
        conn.close()
        return {'skipped': True}

    existing_protokoll_id = existing['id'] if existing else None

    print(f"  Lese:   {pdf_path.name}")

    try:
        result = analyse_pdf(pdf_path, use_llm=use_llm)
    except ValueError as e:
        print(f"  ⚠  {e}")
        conn.close()
        return {'skipped': True, 'reason': 'ocr_failed'}

    meta        = result['protokoll']
    beschluesse = result['beschluesse']

    if meta['machine_readable']:
        print(f"  ℹ  Maschinenlesbar – LLM-OCR-Bereinigung übersprungen")
    print(f"  Datum:  {meta['versammlungs_datum'] or '–'}  |  Objekt: {meta['weg_objekt']}  |  Format: {meta['format']}")

    # Protokoll in DB einfügen oder aktualisieren
    objekt = meta['weg_objekt']
    hv     = meta['hausverwaltung']
    datum  = meta['versammlungs_datum']
    ort    = meta['ort']

    if existing_protokoll_id is None:
        cur.execute("""
            INSERT INTO protokolle
                (dateiname, pdf_pfad, versammlungs_datum, hausverwaltung,
                 weg_objekt, ort, importiert_am)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (pdf_path.name, str(pdf_path), datum, hv, objekt, ort,
              datetime.now().isoformat()))
        protokoll_id = cur.lastrowid
    else:
        protokoll_id = existing_protokoll_id
        cur.execute("""
            UPDATE protokolle SET
                dateiname=?, versammlungs_datum=?, hausverwaltung=?,
                weg_objekt=?, ort=?, importiert_am=?
            WHERE id=?
        """, (pdf_path.name, datum, hv, objekt, ort,
              datetime.now().isoformat(), protokoll_id))

    # Bestehende Beschlüsse aus DB laden (für Rebuild: top_nr → id Mapping)
    existing_map = {}  # top_nr → beschluss_id
    if existing_protokoll_id is not None:
        rows = cur.execute(
            "SELECT id, top_nr FROM beschluesse WHERE protokoll_id = ?",
            (protokoll_id,)
        ).fetchall()
        existing_map = {r['top_nr']: r['id'] for r in rows}

    beirat_count = 0
    for b in beschluesse:
        top_nr = b['top_nr']
        if top_nr in existing_map:
            # Beschluss existiert bereits → UPDATE, editierte Felder schützen
            beschluss_id = existing_map[top_nr]
            if force:
                # --force: alles überschreiben, Edits löschen
                cur.execute("""
                    UPDATE beschluesse SET
                        top_titel=?, beschluss_text=?, ja_stimmen=?,
                        nein_stimmen=?, enthaltungen=?, ergebnis=?,
                        beirat_relevant=?, seite=?
                    WHERE id=?
                """, (b['top_titel'], b['beschluss_text'],
                      b['ja_stimmen'], b['nein_stimmen'], b['enthaltungen'],
                      b['ergebnis'], b['beirat_relevant'], b['seite'],
                      beschluss_id))
                cur.execute(
                    "DELETE FROM beschluss_edits WHERE beschluss_id=?",
                    (beschluss_id,)
                )
            else:
                # Normal rebuild: editierte Felder schützen
                edited = get_edited_fields(cur, beschluss_id)
                # Felder die nicht editiert wurden → aktualisieren
                update_fields = {
                    'top_titel':      b['top_titel'],
                    'beschluss_text': b['beschluss_text'],
                    'ja_stimmen':     b['ja_stimmen'],
                    'nein_stimmen':   b['nein_stimmen'],
                    'enthaltungen':   b['enthaltungen'],
                    'ergebnis':       b['ergebnis'],
                    'beirat_relevant': b['beirat_relevant'],
                    'seite':          b['seite'],
                }
                # Geschützte Felder herausfiltern
                protected = edited & set(update_fields.keys())
                if protected:
                    print(f"    ↺  TOP {top_nr}: {', '.join(sorted(protected))} geschützt (manuell editiert)")
                for field in protected:
                    del update_fields[field]

                if update_fields:
                    set_clause = ', '.join(f"{k}=?" for k in update_fields)
                    cur.execute(
                        f"UPDATE beschluesse SET {set_clause} WHERE id=?",
                        (*update_fields.values(), beschluss_id)
                    )
            # Aus Map entfernen (verarbeitet)
            del existing_map[top_nr]
        else:
            # Neuer Beschluss → INSERT
            cur.execute("""
                INSERT INTO beschluesse
                    (protokoll_id, top_nr, top_titel, beschluss_text,
                     ja_stimmen, nein_stimmen, enthaltungen,
                     ergebnis, beirat_relevant, seite)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (protokoll_id,
                  b['top_nr'], b['top_titel'], b['beschluss_text'],
                  b['ja_stimmen'], b['nein_stimmen'], b['enthaltungen'],
                  b['ergebnis'], b['beirat_relevant'], b['seite']))

        if b['beirat_relevant']:
            beirat_count += 1

    # Beschlüsse die im neuen Lauf nicht mehr vorkommen → nur löschen wenn force
    if existing_map and force:
        for top_nr, bid in existing_map.items():
            cur.execute("DELETE FROM beschluss_edits WHERE beschluss_id=?", (bid,))
            cur.execute("DELETE FROM beschluesse WHERE id=?", (bid,))
            print(f"    🗑  TOP {top_nr} entfernt (--force)")

    conn.commit()
    conn.close()

    print(f"  → {len(beschluesse)} Beschlüsse  |  {beirat_count} Beirat-relevant")
    return {'beschluesse': len(beschluesse), 'beirat': beirat_count}


# ─── HTML-Export ──────────────────────────────────────────────────────────────

def update_app_html(db_path: Path):
    """Früher: RAW-Block in weg_app.html aktualisieren.
    Seit Umstellung auf weg_server.py lädt die App Daten per API –
    ein HTML-Update ist nicht mehr nötig.
    """
    print(f"  ℹ  HTML-Update übersprungen (App lädt Daten jetzt per API via weg_server.py)")
    return


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='WEG Protokoll PDFs → SQLite (v2 Hybrid Regex/LLM)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voraussetzungen:
  pip install pypdf openai
  export OPENAI_API_KEY="sk-..."

Beispiele:
  python3 weg_to_db.py output/
  python3 weg_to_db.py output/ --db ~/weg_protokolle.db
  python3 weg_to_db.py output/ --rebuild
  python3 weg_to_db.py output/ --rebuild --force
  python3 weg_to_db.py protokoll.pdf
  python3 weg_to_db.py output/ --no-llm      (nur Regex, kein API-Aufruf)
        """
    )
    parser.add_argument('input',      help='Ordner mit PDFs oder einzelne Datei')
    parser.add_argument('--db',       default=DEFAULT_DB)
    parser.add_argument('--rebuild',  action='store_true')
    parser.add_argument('--force',    action='store_true',
                        help='Mit --rebuild: editierte Felder ebenfalls überschreiben')
    parser.add_argument('--no-llm',   action='store_true',
                        help='LLM deaktivieren (kein OPENAI_API_KEY nötig)')
    args = parser.parse_args()

    input_p = Path(args.input)
    db_path = Path(args.db)
    use_llm = not args.no_llm
    force   = args.force

    if force and not args.rebuild:
        print("⚠  --force hat nur Wirkung zusammen mit --rebuild – wird ignoriert\n")

    if use_llm and not os.environ.get('OPENAI_API_KEY'):
        print("⚠  OPENAI_API_KEY nicht gesetzt → LLM deaktiviert")
        print("   export OPENAI_API_KEY='sk-...'")
        print("   Oder: --no-llm für reinen Regex-Modus\n")
        use_llm = False

    print(f"Datenbank: {db_path}{'  (rebuild)' if args.rebuild else ''}{'  (--force)' if force else ''}")
    print(f"LLM:       {'GPT-4o-mini' if use_llm else 'deaktiviert (--no-llm)'}\n")

    if input_p.is_dir():
        pdfs = sorted(p for p in input_p.glob('*.pdf'))
        if not pdfs:
            print(f"Keine PDFs in '{input_p}'."); sys.exit(1)

        print(f"Batch: {len(pdfs)} PDF(s)\n")
        gesamt = {'beschluesse': 0, 'beirat': 0, 'fehler': [], 'uebersprungen': 0}

        for pdf in pdfs:
            print(f"{'─'*56}")
            try:
                s = process_pdf(pdf, db_path, args.rebuild, use_llm, force)
                if s.get('skipped'):
                    gesamt['uebersprungen'] += 1
                else:
                    gesamt['beschluesse'] += s.get('beschluesse', 0)
                    gesamt['beirat']      += s.get('beirat', 0)
            except Exception as e:
                print(f"  ❌ {e}")
                gesamt['fehler'].append(pdf.name)

        print(f"\n{'═'*56}")
        print(f"  Beschlüsse:      {gesamt['beschluesse']}")
        print(f"  Beirat-relevant: {gesamt['beirat']}")
        print(f"  Übersprungen:    {gesamt['uebersprungen']}")
        if gesamt['fehler']:
            print(f"  Fehler:          {', '.join(gesamt['fehler'])}")
        print(f"  Datenbank:       {db_path}")
        print(f"{'═'*56}")

        # weg_app.html automatisch aktualisieren
        update_app_html(db_path)

    elif input_p.is_file():
        process_pdf(input_p, db_path, args.rebuild, use_llm, force)
    else:
        print(f"Fehler: '{args.input}' nicht gefunden."); sys.exit(1)


if __name__ == '__main__':
    main()
