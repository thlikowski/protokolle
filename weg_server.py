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
"""

import argparse
import json
import re
import sqlite3
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ─── Konfiguration ────────────────────────────────────────────────────────────

DEFAULT_PORT = 8765
DEFAULT_DB   = Path(__file__).parent / 'weg_protokolle.db'
HTML_FILE    = Path(__file__).parent / 'weg_app.html'

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
