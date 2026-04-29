#!/usr/bin/env python3
"""
WEG Server – Mini-REST-API + HTML-Auslieferung
================================================
Verbindet weg_app.html mit weg_protokolle.db über localhost.

Verwendung:
    python3 weg_server.py
    python3 weg_server.py --port 8765
    python3 weg_server.py --db /pfad/zur/weg_protokolle.db

Öffne dann: http://localhost:8765

API-Endpunkte:
    GET  /api/data                          → alle Protokolle + Beschlüsse (statt RAW-Block)
    GET  /api/notizen                       → alle Notizen
    POST /api/notizen                       → Notiz anlegen/aktualisieren
    DELETE /api/notizen/<id>               → Notiz löschen
    GET  /api/kommentare                    → alle Status-Einträge {beschluss_id: {status}}
    POST /api/kommentare                    → Status für Beschluss setzen (upsert)
    GET  /api/edits                         → alle manuellen Edits
    POST /api/edits/<beschluss_id>          → Edit speichern + beschluss_edits befüllen
    DELETE /api/edits/<beschluss_id>        → Edit löschen (Original wiederherstellen)
    GET  /api/belegpruefungen               → alle Belegprüfungen mit Dokumenten
    POST /api/belegpruefungen               → neue Belegprüfung anlegen
    PUT  /api/belegpruefungen/<id>          → Belegprüfung bearbeiten
    DELETE /api/belegpruefungen/<id>        → Belegprüfung löschen
    POST /api/belegpruefungen/<id>/upload              → Datei hochladen → belegpruefung/
    POST /api/belegpruefungen/<id>/dokumente           → Dokument (Link) hinzufügen
    DELETE /api/belegpruefungen/<id>/dokumente/<dok_id> → Dokument löschen
"""

import argparse
import base64
import json
import mimetypes
import re
import sqlite3
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ─── Konfiguration ────────────────────────────────────────────────────────────

DEFAULT_PORT  = 8765
DEFAULT_DB    = Path(__file__).parent / 'weg_protokolle.db'
HTML_FILE     = Path(__file__).parent / 'weg_app.html'
BELEG_DIR     = Path(__file__).parent / 'belegpruefung'
OUTPUT_DIR    = Path(__file__).parent / 'output'
SRC_DIR       = Path(__file__).parent / 'src'

# ─── Datenbankzugriff ─────────────────────────────────────────────────────────

def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads während writes
    # Tabellen anlegen falls noch nicht vorhanden (migrations-sicher)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kommentare (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            beschluss_id   INTEGER REFERENCES beschluesse(id),
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
        -- Notizen-Tabelle (ersetzt localStorage weg_notizen)
        CREATE TABLE IF NOT EXISTS notizen (
            id           TEXT PRIMARY KEY,
            datum        TEXT,
            hv           TEXT,
            objekt       TEXT,
            betreff      TEXT,
            text         TEXT,
            status       TEXT DEFAULT 'offen',
            beschluss_id INTEGER,
            gmail_link   TEXT,
            erstellt_am  TEXT NOT NULL,
            geaendert_am TEXT
        );
        -- Belegprüfungen
        CREATE TABLE IF NOT EXISTS belegpruefungen (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            termin          TEXT,
            objekt          TEXT,
            hausverwaltung  TEXT,
            ort             TEXT,
            notiz           TEXT,
            erstellt_am     TEXT NOT NULL,
            geaendert_am    TEXT
        );
        CREATE TABLE IF NOT EXISTS belegpruefung_dokumente (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            belegpruefung_id   INTEGER REFERENCES belegpruefungen(id),
            name               TEXT,
            link               TEXT,
            erstellt_am        TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ─── API-Handler ──────────────────────────────────────────────────────────────

def api_get_data(db_path: Path) -> dict:
    """Alle Protokolle + Beschlüsse + Edits – ersetzt den RAW-Block in der HTML."""
    conn = get_conn(db_path)

    protokolle = []
    for r in conn.execute(
        "SELECT * FROM protokolle ORDER BY weg_objekt, versammlungs_datum"
    ).fetchall():
        p = dict(r)
        p['anzahl_beschluesse'] = conn.execute(
            "SELECT COUNT(*) FROM beschluesse WHERE protokoll_id=?", (p['id'],)
        ).fetchone()[0]
        p['beirat_count'] = conn.execute(
            "SELECT COUNT(*) FROM beschluesse WHERE protokoll_id=? AND beirat_relevant=1",
            (p['id'],)
        ).fetchone()[0]
        p['pdf_pfad'] = f"output/{p['dateiname']}"
        protokolle.append(p)

    beschluesse = [dict(r) for r in conn.execute("""
        SELECT b.id, b.protokoll_id, b.top_nr, b.top_titel,
               b.beschluss_text, b.ergebnis,
               b.ja_stimmen, b.nein_stimmen, b.enthaltungen,
               b.beirat_relevant, b.seite,
               p.dateiname, p.versammlungs_datum,
               p.hausverwaltung, p.weg_objekt
        FROM beschluesse b JOIN protokolle p ON p.id = b.protokoll_id
        ORDER BY p.weg_objekt, p.versammlungs_datum, CAST(b.top_nr AS REAL), b.top_nr, b.id
    """).fetchall()]

    # Edits als Lookup {beschluss_id: {feld: True, ...}} mitliefern
    edits_raw = conn.execute(
        "SELECT beschluss_id, feld FROM beschluss_edits"
    ).fetchall()
    edits_fields = {}  # {beschluss_id: set of edited fields}
    for r in edits_raw:
        edits_fields.setdefault(r['beschluss_id'], set()).add(r['feld'])

    # Editierte Werte aus beschluesse direkt lesen (dort schon gespeichert)
    # Wir liefern zusätzlich ein edits-Objekt {beschluss_id: [felder]} für Badge-Anzeige
    edits_meta = {bid: list(fields) for bid, fields in edits_fields.items()}

    conn.close()
    return {
        'protokolle':  protokolle,
        'beschluesse': beschluesse,
        'edits_meta':  edits_meta,
    }


def _parse_gmail_links(raw: str | None) -> list:
    """Parst gmail_link-Feld – unterstützt altes String-Format und neues JSON-Array."""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith('['):
        try:
            return json.loads(raw)
        except Exception:
            return []
    # Altes Format: einfacher URL-String → in Array-Format überführen
    return [{'label': '', 'url': raw}]


def api_get_notizen(db_path: Path) -> dict:
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM notizen ORDER BY geaendert_am DESC"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        d = dict(r)
        d['gmail_links'] = _parse_gmail_links(d.get('gmail_link'))
        result[d['id']] = d
    return result


def api_save_notiz(db_path: Path, data: dict) -> dict:
    conn = get_conn(db_path)
    now  = datetime.now().isoformat()
    nid  = data.get('id') or ('n_' + str(int(datetime.now().timestamp() * 1000)))

    # Existiert schon?
    existing = conn.execute(
        "SELECT erstellt_am FROM notizen WHERE id=?", (nid,)
    ).fetchone()
    erstellt_am = existing['erstellt_am'] if existing else now

    # gmail_links als JSON-Array serialisieren (leere URL-Einträge rausfiltern)
    gmail_links = [l for l in data.get('gmail_links', []) if l.get('url', '').strip()]
    gmail_link_raw = json.dumps(gmail_links, ensure_ascii=False) if gmail_links else None

    conn.execute("""
        INSERT OR REPLACE INTO notizen
            (id, datum, hv, objekt, betreff, text, status,
             beschluss_id, gmail_link, erstellt_am, geaendert_am)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        nid,
        data.get('datum'),
        data.get('hv'),
        data.get('objekt'),
        data.get('betreff'),
        data.get('text'),
        data.get('status', 'offen'),
        data.get('beschluss_id'),
        gmail_link_raw,
        erstellt_am,
        now,
    ))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM notizen WHERE id=?", (nid,)).fetchone())
    row['gmail_links'] = _parse_gmail_links(row.get('gmail_link'))
    conn.close()
    return row


def api_get_kommentare(db_path: Path) -> dict:
    """Alle Status-Einträge als {beschluss_id: {status, geaendert_am}}."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT beschluss_id, status, geaendert_am FROM kommentare ORDER BY geaendert_am ASC"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        bid = r['beschluss_id']
        result[bid] = {'status': r['status'], 'geaendert_am': r['geaendert_am']}
    return result


def api_save_kommentar(db_path: Path, data: dict) -> dict:
    """Status für einen Beschluss setzen – upsert in kommentare-Tabelle."""
    conn  = get_conn(db_path)
    now   = datetime.now().isoformat()
    bid   = int(data['beschluss_id'])
    status = data.get('status', 'offen')

    existing = conn.execute(
        "SELECT id FROM kommentare WHERE beschluss_id=?", (bid,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE kommentare SET status=?, geaendert_am=? WHERE beschluss_id=?",
            (status, now, bid)
        )
    else:
        conn.execute(
            "INSERT INTO kommentare (beschluss_id, status, erstellt_am, geaendert_am) VALUES (?,?,?,?)",
            (bid, status, now, now)
        )
    conn.commit()
    conn.close()
    return {'beschluss_id': bid, 'status': status, 'ok': True}


def api_delete_notiz(db_path: Path, nid: str) -> bool:
    conn = get_conn(db_path)
    conn.execute("DELETE FROM notizen WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    return True


def api_save_edit(db_path: Path, beschluss_id: int, data: dict) -> dict:
    """
    Speichert einen manuellen Edit:
    1. Felder in beschluesse updaten
    2. Editierte Felder in beschluss_edits eintragen (für Rebuild-Schutz)
    """
    conn  = get_conn(db_path)
    now   = datetime.now().isoformat()

    # Feld-Mapping: API-Namen → DB-Spaltennamen
    field_map = {
        'text':      'beschluss_text',
        'ergebnis':  'ergebnis',
        'ja':        'ja_stimmen',
        'nein':      'nein_stimmen',
        'enth':      'enthaltungen',
    }

    update_cols = {}
    for api_key, db_col in field_map.items():
        if api_key in data:
            update_cols[db_col] = data[api_key]

    if update_cols:
        set_clause = ', '.join(f"{k}=?" for k in update_cols)
        conn.execute(
            f"UPDATE beschluesse SET {set_clause} WHERE id=?",
            (*update_cols.values(), beschluss_id)
        )
        # Editierte Felder in beschluss_edits eintragen
        for db_col in update_cols:
            conn.execute("""
                INSERT OR REPLACE INTO beschluss_edits (beschluss_id, feld, editiert_am)
                VALUES (?,?,?)
            """, (beschluss_id, db_col, now))

    conn.commit()
    # Aktuellen Stand zurückgeben
    row = dict(conn.execute(
        "SELECT * FROM beschluesse WHERE id=?", (beschluss_id,)
    ).fetchone())
    edited_fields = [r['feld'] for r in conn.execute(
        "SELECT feld FROM beschluss_edits WHERE beschluss_id=?", (beschluss_id,)
    ).fetchall()]
    conn.close()
    return {'beschluss': row, 'edited_fields': edited_fields}


def api_delete_edit(db_path: Path, beschluss_id: int) -> bool:
    """
    Edit löschen: beschluss_edits-Einträge entfernen.
    Die Felder in beschluesse bleiben auf dem manuell gesetzten Wert —
    beim nächsten --rebuild werden sie dann mit dem neu extrahierten Wert überschrieben.
    """
    conn = get_conn(db_path)
    conn.execute(
        "DELETE FROM beschluss_edits WHERE beschluss_id=?", (beschluss_id,)
    )
    conn.commit()
    conn.close()
    return True



# ─── Import-API ───────────────────────────────────────────────────────────────

def api_import_protokoll(db_path: Path, data: dict) -> dict:
    """Neues Protokoll manuell anlegen."""
    conn = get_conn(db_path)
    now  = datetime.now().isoformat()
    cur  = conn.execute("""
        INSERT INTO protokolle
            (dateiname, pdf_pfad, versammlungs_datum, hausverwaltung,
             weg_objekt, ort, importiert_am)
        VALUES (?,?,?,?,?,?,?)
    """, (
        data.get('dateiname', ''),
        data.get('pdf_pfad', ''),
        data.get('versammlungs_datum'),
        data.get('hausverwaltung'),
        data.get('weg_objekt'),
        data.get('ort'),
        now,
    ))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM protokolle WHERE id=?", (cur.lastrowid,)
    ).fetchone())
    conn.close()
    return row


def api_import_beschluss(db_path: Path, data: dict) -> dict:
    """Neuen Beschluss manuell anlegen."""
    conn = get_conn(db_path)
    cur  = conn.execute("""
        INSERT INTO beschluesse
            (protokoll_id, top_nr, top_titel, beschluss_text,
             ja_stimmen, nein_stimmen, enthaltungen,
             ergebnis, beirat_relevant, seite)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        data['protokoll_id'],
        data.get('top_nr'),
        data.get('top_titel'),
        data.get('beschluss_text'),
        data.get('ja_stimmen'),
        data.get('nein_stimmen'),
        data.get('enthaltungen'),
        data.get('ergebnis'),
        data.get('beirat_relevant', 0),
        data.get('seite'),
    ))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM beschluesse WHERE id=?", (cur.lastrowid,)
    ).fetchone())
    conn.close()
    return row


def api_update_beschluss(db_path: Path, beschluss_id: int, data: dict) -> dict:
    """Bestehenden Beschluss aktualisieren + geänderte Felder in beschluss_edits eintragen."""
    conn = get_conn(db_path)

    # Alten Stand lesen um Änderungen zu erkennen
    old = conn.execute(
        "SELECT * FROM beschluesse WHERE id=?", (beschluss_id,)
    ).fetchone()

    # Felder die sich geändert haben → rebuild-geschützt markieren
    PROTECTED = ('beschluss_text', 'ergebnis', 'ja_stimmen', 'nein_stimmen', 'enthaltungen')
    field_map  = {
        'beschluss_text': data.get('beschluss_text'),
        'ergebnis':       data.get('ergebnis'),
        'ja_stimmen':     data.get('ja_stimmen'),
        'nein_stimmen':   data.get('nein_stimmen'),
        'enthaltungen':   data.get('enthaltungen'),
    }
    if old:
        from datetime import datetime
        now = datetime.now().isoformat(timespec='seconds')
        for feld, new_val in field_map.items():
            old_val = old[feld] if old[feld] is not None else None
            new_val_n = new_val if new_val not in ('', None) else None
            if str(old_val or '') != str(new_val_n or ''):
                conn.execute(
                    "INSERT OR REPLACE INTO beschluss_edits (beschluss_id, feld, editiert_am) VALUES (?,?,?)",
                    (beschluss_id, feld, now)
                )

    conn.execute("""
        UPDATE beschluesse SET
            top_nr=?, top_titel=?, beschluss_text=?,
            ja_stimmen=?, nein_stimmen=?, enthaltungen=?,
            ergebnis=?, beirat_relevant=?, seite=?
        WHERE id=?
    """, (
        data.get('top_nr'),
        data.get('top_titel'),
        data.get('beschluss_text'),
        data.get('ja_stimmen'),
        data.get('nein_stimmen'),
        data.get('enthaltungen'),
        data.get('ergebnis'),
        data.get('beirat_relevant', 0),
        data.get('seite'),
        beschluss_id,
    ))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM beschluesse WHERE id=?", (beschluss_id,)
    ).fetchone())
    conn.close()
    return row


def api_delete_beschluss(db_path: Path, beschluss_id: int) -> bool:
    conn = get_conn(db_path)
    conn.execute("DELETE FROM beschluss_edits WHERE beschluss_id=?", (beschluss_id,))
    conn.execute("DELETE FROM beschluesse WHERE id=?", (beschluss_id,))
    conn.commit()
    conn.close()
    return True


def api_delete_protokoll(db_path: Path, protokoll_id: int) -> dict:
    """Löscht ein Protokoll samt aller zugehörigen Beschlüsse und Edits."""
    conn = get_conn(db_path)
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM beschluesse WHERE protokoll_id=?", (protokoll_id,)
    ).fetchall()]
    for bid in ids:
        conn.execute("DELETE FROM beschluss_edits WHERE beschluss_id=?", (bid,))
    if ids:
        conn.execute(
            f"DELETE FROM beschluesse WHERE id IN ({','.join('?'*len(ids))})", ids
        )
    conn.execute("DELETE FROM protokolle WHERE id=?", (protokoll_id,))
    conn.commit()
    conn.close()
    return {'ok': True, 'beschluesse_deleted': len(ids)}

# ─── PDF-Analyse-API ──────────────────────────────────────────────────────────

def api_analyse_pdf(db_path: Path, data: dict) -> dict:
    """
    Analysiert hochgeladene PDF (base64) und gibt extrahierte Daten zurück.
    Schreibt NICHTS in die DB – nur Analyse.
    Input:  {pdf_data: base64, filename: str, use_llm: bool}
    Output: {protokoll: {...}, beschluesse: [...], already_exists: bool, existing_id: int|null}
    """
    raw_name  = re.sub(r'[^\w.\-]', '_', data.get('filename', 'upload.pdf'))
    # Dateiname für output/: immer _durchsuchbar.pdf
    stem      = raw_name[:-4] if raw_name.endswith('.pdf') else raw_name
    if not stem.endswith('_durchsuchbar'):
        stem = stem + '_durchsuchbar'
    filename  = stem + '.pdf'
    pdf_bytes = base64.b64decode(data['pdf_data'])
    use_llm   = data.get('use_llm', True)

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        from weg_to_db import analyse_pdf  # type: ignore

        result = analyse_pdf(tmp_path, filename_hint=filename, use_llm=use_llm)

        # Prüfen ob Protokoll mit gleichem Datum + Objekt schon in DB ist
        conn = get_conn(db_path)
        datum  = result['protokoll'].get('versammlungs_datum')
        objekt = result['protokoll'].get('weg_objekt')
        existing = None
        if datum and objekt:
            existing = conn.execute(
                "SELECT id FROM protokolle WHERE versammlungs_datum=? AND weg_objekt=?",
                (datum, objekt)
            ).fetchone()
        if not existing:
            existing = conn.execute(
                "SELECT id FROM protokolle WHERE dateiname=?", (filename,)
            ).fetchone()
        conn.close()

        result['already_exists'] = existing is not None
        result['existing_id']    = existing['id'] if existing else None
        result['filename_safe']  = filename
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


def _make_durchsuchbar(pdf_bytes: bytes, dest: Path) -> Path:
    """
    Erzeugt eine durchsuchbare PDF via weg_protokoll_processor.process_pdf():
    - Maschinenlesbar → nur Beirat-Hervorhebungen ergänzen
    - Gescannt        → OCR via ocrmypdf + Beirat-Hervorhebungen
    Gibt den tatsächlichen Zielpfad zurück.
    """
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)
    try:
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        from weg_protokoll_processor import process_pdf, detect_tesseract_lang, check_ocrmypdf  # type: ignore
        lang        = detect_tesseract_lang()
        use_ocrmypdf = check_ocrmypdf()
        process_pdf(tmp_path, dest, lang, use_ocrmypdf)
    except Exception as e:
        print(f'  weg_protokoll_processor Fehler: {e} – speichere Original')
        dest.write_bytes(pdf_bytes)
    finally:
        tmp_path.unlink(missing_ok=True)
    return dest


def api_import_protokoll_komplett(db_path: Path, data: dict) -> dict:
    """
    Importiert Protokoll + alle Beschlüsse in einem Schritt.
    Erzeugt eine durchsuchbare PDF (_durchsuchbar.pdf) via ocrmypdf.
    Input:  {protokoll: {...}, beschluesse: [...], pdf_data: base64 (optional)}
    """
    proto    = data['protokoll']
    beschl   = data.get('beschluesse', [])
    filename = re.sub(r'[^\w.\-]', '_', proto.get('dateiname', ''))

    # Sicherstellen dass Dateiname auf _durchsuchbar.pdf endet
    if filename and not filename.endswith('_durchsuchbar.pdf'):
        stem = filename[:-4] if filename.endswith('.pdf') else filename
        filename = stem + '_durchsuchbar.pdf'
        proto['dateiname'] = filename

    # PDF verarbeiten: ocrmypdf → _durchsuchbar.pdf in output/
    if data.get('pdf_data') and filename:
        OUTPUT_DIR.mkdir(exist_ok=True)
        dest = OUTPUT_DIR / filename
        if not dest.exists():
            print(f'  ocrmypdf: {filename} …')
            _make_durchsuchbar(base64.b64decode(data['pdf_data']), dest)

    conn = get_conn(db_path)
    now  = datetime.now().isoformat()

    cur = conn.execute("""
        INSERT INTO protokolle (dateiname, pdf_pfad, versammlungs_datum, hausverwaltung,
             weg_objekt, ort, importiert_am)
        VALUES (?,?,?,?,?,?,?)
    """, (
        filename,
        proto.get('pdf_pfad', f'output/{filename}'),
        proto.get('versammlungs_datum'),
        proto.get('hausverwaltung'),
        proto.get('weg_objekt'),
        proto.get('ort'),
        now,
    ))
    protokoll_id = cur.lastrowid

    count = 0
    for b in beschl:
        if not b.get('top_nr'):
            continue
        conn.execute("""
            INSERT INTO beschluesse (protokoll_id, top_nr, top_titel, beschluss_text,
                ja_stimmen, nein_stimmen, enthaltungen, ergebnis, beirat_relevant, seite)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            protokoll_id,
            b.get('top_nr'), b.get('top_titel'), b.get('beschluss_text'),
            b.get('ja_stimmen'), b.get('nein_stimmen'), b.get('enthaltungen'),
            b.get('ergebnis'), b.get('beirat_relevant', 0), b.get('seite'),
        ))
        count += 1

    conn.commit()
    row = dict(conn.execute("SELECT * FROM protokolle WHERE id=?", (protokoll_id,)).fetchone())
    conn.close()
    return {'protokoll': row, 'protokoll_id': protokoll_id, 'beschluesse_count': count}


# ─── Belegprüfung-API ─────────────────────────────────────────────────────────

def api_get_belegpruefungen(db_path: Path) -> list:
    """Alle Belegprüfungen mit zugehörigen Dokumenten."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM belegpruefungen ORDER BY termin DESC"
    ).fetchall()
    result = []
    for r in rows:
        bp = dict(r)
        bp['dokumente'] = [dict(d) for d in conn.execute(
            "SELECT * FROM belegpruefung_dokumente WHERE belegpruefung_id=? ORDER BY id",
            (bp['id'],)
        ).fetchall()]
        result.append(bp)
    conn.close()
    return result


def api_save_belegpruefung(db_path: Path, data: dict) -> dict:
    """Neue Belegprüfung anlegen."""
    conn = get_conn(db_path)
    now  = datetime.now().isoformat()
    cur  = conn.execute("""
        INSERT INTO belegpruefungen (termin, objekt, hausverwaltung, ort, notiz, erstellt_am, geaendert_am)
        VALUES (?,?,?,?,?,?,?)
    """, (
        data.get('termin'),
        data.get('objekt'),
        data.get('hausverwaltung'),
        data.get('ort'),
        data.get('notiz'),
        now, now,
    ))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM belegpruefungen WHERE id=?", (cur.lastrowid,)
    ).fetchone())
    row['dokumente'] = []
    conn.close()
    return row


def api_update_belegpruefung(db_path: Path, bp_id: int, data: dict) -> dict:
    """Bestehende Belegprüfung aktualisieren."""
    conn = get_conn(db_path)
    now  = datetime.now().isoformat()
    conn.execute("""
        UPDATE belegpruefungen
        SET termin=?, objekt=?, hausverwaltung=?, ort=?, notiz=?, geaendert_am=?
        WHERE id=?
    """, (
        data.get('termin'),
        data.get('objekt'),
        data.get('hausverwaltung'),
        data.get('ort'),
        data.get('notiz'),
        now, bp_id,
    ))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM belegpruefungen WHERE id=?", (bp_id,)
    ).fetchone())
    row['dokumente'] = [dict(d) for d in conn.execute(
        "SELECT * FROM belegpruefung_dokumente WHERE belegpruefung_id=? ORDER BY id", (bp_id,)
    ).fetchall()]
    conn.close()
    return row


def api_delete_belegpruefung(db_path: Path, bp_id: int) -> bool:
    """Belegprüfung + alle Dokumente löschen."""
    conn = get_conn(db_path)
    conn.execute("DELETE FROM belegpruefung_dokumente WHERE belegpruefung_id=?", (bp_id,))
    conn.execute("DELETE FROM belegpruefungen WHERE id=?", (bp_id,))
    conn.commit()
    conn.close()
    return True


def api_add_belegdokument(db_path: Path, bp_id: int, data: dict) -> dict:
    """Dokument zu einer Belegprüfung hinzufügen."""
    conn = get_conn(db_path)
    now  = datetime.now().isoformat()
    cur  = conn.execute("""
        INSERT INTO belegpruefung_dokumente (belegpruefung_id, name, link, erstellt_am)
        VALUES (?,?,?,?)
    """, (bp_id, data.get('name'), data.get('link') or None, now))
    conn.commit()
    row = dict(conn.execute(
        "SELECT * FROM belegpruefung_dokumente WHERE id=?", (cur.lastrowid,)
    ).fetchone())
    conn.close()
    return row


def api_delete_belegdokument(db_path: Path, dok_id: int) -> bool:
    """Einzelnes Dokument löschen."""
    conn = get_conn(db_path)
    conn.execute("DELETE FROM belegpruefung_dokumente WHERE id=?", (dok_id,))
    conn.commit()
    conn.close()
    return True


def api_upload_belegdokument(db_path: Path, bp_id: int, data: dict) -> dict:
    """
    Datei-Upload: base64-kodierte Datei in BELEG_DIR speichern,
    Datensatz in belegpruefung_dokumente anlegen.
    """
    BELEG_DIR.mkdir(exist_ok=True)
    name     = re.sub(r'[^\w.\-]', '_', data['name'])  # Dateinamen bereinigen
    raw      = base64.b64decode(data['data'])
    dest     = BELEG_DIR / name
    dest.write_bytes(raw)
    link     = f'/belegpruefung/{name}'
    return api_add_belegdokument(db_path, bp_id, {'name': data['name'], 'link': link})


# ─── HTTP-Handler ─────────────────────────────────────────────────────────────

class WEGHandler(BaseHTTPRequestHandler):

    db_path: Path = DEFAULT_DB  # wird in main() gesetzt

    def log_message(self, fmt, *args):
        # Nur Fehler und nicht-200 loggen, sonst zu viel Rauschen
        if args and len(args) >= 2 and str(args[1]) not in ('200', '204'):
            print(f"  [{self.address_string()}] {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({'error': message}, status)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')

        # ── HTML ausliefern ──────────────────────────────────────────────────
        if path in ('', '/'):
            if not HTML_FILE.exists():
                self.send_error_json(404, f'weg_app.html nicht gefunden: {HTML_FILE}')
                return
            html = HTML_FILE.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        # ── Import-Tool ausliefern ───────────────────────────────────────────
        if path == '/import':
            import_file = Path(__file__).parent / 'weg_import.html'
            if not import_file.exists():
                self.send_error_json(404, 'weg_import.html nicht gefunden')
                return
            html = import_file.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        # ── Statische Dateien ausliefern (CSS, JS) ──────────────────────────
        if path in ('/weg_app.css', '/weg_app.js'):
            static_file = Path(__file__).parent / path.lstrip('/')
            if not static_file.exists():
                self.send_error_json(404, f'{path} nicht gefunden')
                return
            data = static_file.read_bytes()
            ct = 'text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
            return

        # ── Beleg-Dateien ausliefern ─────────────────────────────────────────
        if path.startswith('/belegpruefung/'):
            file_path = Path(__file__).parent / path.lstrip('/')
            if file_path.exists() and file_path.is_file():
                ct, _ = mimetypes.guess_type(str(file_path))
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', ct or 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Disposition',
                                 f'inline; filename="{file_path.name}"')
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error_json(404, 'Datei nicht gefunden')
            return

        # ── PDF-Dateien ausliefern ───────────────────────────────────────────
        if path.startswith('/output/'):
            pdf_path = Path(__file__).parent / path.lstrip('/')
            if pdf_path.exists() and pdf_path.suffix == '.pdf':
                data = pdf_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error_json(404, 'PDF nicht gefunden')
            return

        # ── API ──────────────────────────────────────────────────────────────
        if path == '/api/data':
            try:
                self.send_json(api_get_data(self.db_path))
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/notizen':
            try:
                self.send_json(api_get_notizen(self.db_path))
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/kommentare':
            try:
                self.send_json(api_get_kommentare(self.db_path))
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/edits':
            # Alle Edits als {beschluss_id: [felder]}
            try:
                conn = get_conn(self.db_path)
                rows = conn.execute(
                    "SELECT beschluss_id, feld FROM beschluss_edits"
                ).fetchall()
                conn.close()
                result = {}
                for r in rows:
                    result.setdefault(str(r['beschluss_id']), []).append(r['feld'])
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/belegpruefungen':
            try:
                self.send_json(api_get_belegpruefungen(self.db_path))
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/belegpruefung/open-folder':
            try:
                BELEG_DIR.mkdir(exist_ok=True)
                subprocess.Popen(['open', str(BELEG_DIR)])
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        self.send_error_json(404, f'Unbekannter Endpunkt: {path}')

    def do_HEAD(self):
        """HEAD-Anfragen für Existenzprüfung (z.B. PDF-Auto-Load)"""
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')
        if path.startswith('/output/'):
            pdf_path = Path(__file__).parent / path.lstrip('/')
            if pdf_path.exists() and pdf_path.suffix == '.pdf':
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(pdf_path.stat().st_size))
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(405)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')

        if path == '/api/notizen':
            try:
                data   = self.read_body()
                result = api_save_notiz(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if path == '/api/kommentare':
            try:
                data   = self.read_body()
                result = api_save_kommentar(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/edits/<beschluss_id>
        m = re.match(r'^/api/edits/(\d+)$', path)
        if m:
            try:
                beschluss_id = int(m.group(1))
                data         = self.read_body()
                result       = api_save_edit(self.db_path, beschluss_id, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/import/analyse  (PDF hochladen + analysieren, kein DB-Write)
        if path == '/api/import/analyse':
            try:
                data   = self.read_body()
                result = api_analyse_pdf(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/import/protokoll-komplett  (Protokoll + alle Beschlüsse in einem Schritt)
        if path == '/api/import/protokoll-komplett':
            try:
                data   = self.read_body()
                result = api_import_protokoll_komplett(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/protokoll/<id>/replace-pdf  (PDF austauschen)
        m = re.match(r'^/api/protokoll/(\d+)/replace-pdf$', path)
        if m:
            proto_id = int(m.group(1))
            try:
                data = self.read_body()
                conn = get_conn(self.db_path)
                row  = conn.execute('SELECT dateiname FROM protokolle WHERE id=?', (proto_id,)).fetchone()
                if not row:
                    self.send_error_json(404, f'Protokoll {proto_id} nicht gefunden')
                    return
                dateiname = row['dateiname']
                OUTPUT_DIR.mkdir(exist_ok=True)
                dest = OUTPUT_DIR / dateiname
                print(f'  PDF tauschen: {dateiname}')
                _make_durchsuchbar(base64.b64decode(data['pdf_data']), dest)
                self.send_json({'ok': True, 'dateiname': dateiname})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/import/protokoll
        if path == '/api/import/protokoll':
            try:
                data   = self.read_body()
                result = api_import_protokoll(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/import/beschluss
        if path == '/api/import/beschluss':
            try:
                data   = self.read_body()
                result = api_import_beschluss(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/belegpruefungen
        if path == '/api/belegpruefungen':
            try:
                data   = self.read_body()
                result = api_save_belegpruefung(self.db_path, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/belegpruefungen/<id>/upload  (base64-Datei-Upload)
        m = re.match(r'^/api/belegpruefungen/(\d+)/upload$', path)
        if m:
            try:
                bp_id  = int(m.group(1))
                data   = self.read_body()
                result = api_upload_belegdokument(self.db_path, bp_id, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # POST /api/belegpruefungen/<id>/dokumente
        m = re.match(r'^/api/belegpruefungen/(\d+)/dokumente$', path)
        if m:
            try:
                bp_id  = int(m.group(1))
                data   = self.read_body()
                result = api_add_belegdokument(self.db_path, bp_id, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        self.send_error_json(404, f'Unbekannter Endpunkt: {path}')

    def do_PUT(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')

        # PUT /api/import/beschluss/<id>
        m = re.match(r'^/api/import/beschluss/(\d+)$', path)
        if m:
            try:
                beschluss_id = int(m.group(1))
                data         = self.read_body()
                result       = api_update_beschluss(self.db_path, beschluss_id, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # PUT /api/belegpruefungen/<id>
        m = re.match(r'^/api/belegpruefungen/(\d+)$', path)
        if m:
            try:
                bp_id  = int(m.group(1))
                data   = self.read_body()
                result = api_update_belegpruefung(self.db_path, bp_id, data)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        self.send_error_json(404, f'Unbekannter Endpunkt: {path}')

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')

        # DELETE /api/notizen/<id>
        m = re.match(r'^/api/notizen/(.+)$', path)
        if m:
            try:
                nid = m.group(1)
                api_delete_notiz(self.db_path, nid)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # DELETE /api/edits/<beschluss_id>
        m = re.match(r'^/api/edits/(\d+)$', path)
        if m:
            try:
                beschluss_id = int(m.group(1))
                api_delete_edit(self.db_path, beschluss_id)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # DELETE /api/import/beschluss/<id>
        m = re.match(r'^/api/import/beschluss/(\d+)$', path)
        if m:
            try:
                beschluss_id = int(m.group(1))
                api_delete_beschluss(self.db_path, beschluss_id)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # DELETE /api/import/protokoll/<id>
        m = re.match(r'^/api/import/protokoll/(\d+)$', path)
        if m:
            try:
                protokoll_id = int(m.group(1))
                result = api_delete_protokoll(self.db_path, protokoll_id)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # DELETE /api/belegpruefungen/<id>
        m = re.match(r'^/api/belegpruefungen/(\d+)$', path)
        if m:
            try:
                bp_id = int(m.group(1))
                api_delete_belegpruefung(self.db_path, bp_id)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # DELETE /api/belegpruefungen/<id>/dokumente/<dok_id>
        m = re.match(r'^/api/belegpruefungen/(\d+)/dokumente/(\d+)$', path)
        if m:
            try:
                dok_id = int(m.group(2))
                api_delete_belegdokument(self.db_path, dok_id)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        self.send_error_json(404, f'Unbekannter Endpunkt: {path}')


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='WEG Mini-Server – REST-API + HTML-Auslieferung',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python3 weg_server.py
  python3 weg_server.py --port 9000
  python3 weg_server.py --db /pfad/zur/weg_protokolle.db
        """
    )
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--db',   default=str(DEFAULT_DB))
    parser.add_argument('--no-browser', action='store_true',
                        help='Browser nicht automatisch öffnen')
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"✗  Datenbank nicht gefunden: {db_path}")
        print(f"   Erstelle mit: python3 src/weg_to_db.py output/")
        sys.exit(1)

    if not HTML_FILE.exists():
        print(f"✗  weg_app.html nicht gefunden: {HTML_FILE}")
        sys.exit(1)

    # DB-Pfad in Handler-Klasse setzen (einfachste thread-safe Methode)
    WEGHandler.db_path = db_path

    # Beleg-Ordner anlegen falls nicht vorhanden
    BELEG_DIR.mkdir(exist_ok=True)

    # Tabellen anlegen falls noch nicht vorhanden
    conn = get_conn(db_path)
    conn.close()

    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  WEG Server gestartet")
    print(f"  ────────────────────────────────────")
    print(f"  URL:       {url}")
    print(f"  Datenbank: {db_path}")
    print(f"  HTML:      {HTML_FILE}")
    print(f"  ────────────────────────────────────")
    print(f"  Stoppen: Ctrl+C")
    print(f"")

    if not args.no_browser:
        import threading
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    server = HTTPServer(('localhost', args.port), WEGHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  Server gestoppt.")


if __name__ == '__main__':
    main()
