#!/usr/bin/env python3
"""
WEG PDF Dump – Roher Text → Markdown
======================================
Gibt den rohen Text aller PDFs aus, wie pypdf ihn liest (VOR jeder Normalisierung).
Dient zur manuellen Analyse der Dokument-Struktur für Regex/LLM-Entwicklung.

Verwendung:
    python3 weg_pdf_dump.py output/                    # Ordner mit PDFs
    python3 weg_pdf_dump.py output/ --out dump.md      # eigener Ausgabepfad
    python3 weg_pdf_dump.py protokoll.pdf              # einzelne Datei
    python3 weg_pdf_dump.py output/ --filter Frauentor # nur best. Dateien

Voraussetzungen:
    pip install pypdf
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

# ─── Konfiguration ────────────────────────────────────────────────────────────

DEFAULT_OUT = 'weg_dump.md'

# ─── Text-Extraktion (bewusst roh, keine Normalisierung) ─────────────────────

def extract_raw(pdf_path: Path) -> list[tuple[int, str]]:
    """
    Gibt Liste von (Seitennummer, roher_Text) zurück.
    Keinerlei Nachbearbeitung – exakt was pypdf liefert.
    """
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ''
        pages.append((i + 1, text))
    return pages


def make_visible(text: str) -> str:
    """
    Macht Steuerzeichen sichtbar für die Analyse:
    - Zeilenumbrüche bleiben erhalten
    - Tabs → [TAB]
    - Andere Steuerzeichen → [0xNN]
    - Mehrfache Leerzeichen markieren (für OCR-Wort-Spacing-Analyse)
    """
    result = []
    for ch in text:
        if ch == '\n':
            result.append('\n')
        elif ch == '\t':
            result.append('[TAB]')
        elif ch == '\r':
            result.append('[CR]')
        elif ord(ch) < 32:
            result.append(f'[0x{ord(ch):02X}]')
        else:
            result.append(ch)
    return ''.join(result)


# ─── Statistiken ─────────────────────────────────────────────────────────────

def analyze_text(pages: list[tuple[int, str]]) -> dict:
    """Schnelle Analyse für den Header-Block jedes PDFs."""
    full_text = '\n'.join(t for _, t in pages)

    # Zeilenumbruch-Muster
    lines = full_text.split('\n')
    single_word_lines = sum(1 for l in lines if len(l.strip().split()) == 1)
    empty_lines = sum(1 for l in lines if not l.strip())
    total_lines = len(lines)

    # TOP-Kandidaten (verschiedene Muster)
    top_patterns = {
        'TOP X:':           r'\bTOP\s+\d+\s*:',
        'zu TOP X:':        r'zu\s+TOP\s+\d+',
        'Tagesordnung X:':  r'Tagesordnungspunkt\s+\d+',
        'Zu X. der TO:':    r'Zu\s+\d+\.\s+der\s+Tagesordnung',
        'X ) Titel':        r'^\d+\s*\)',
        'Beschluss:':       r'\bBeschluss\b',
        'Abstimmung:':      r'\bAbstimmung\b',
        'angenommen':       r'\bangenommen\b',
        'abgelehnt':        r'\babgelehnt\b',
        'JA-Stimmen':       r'JA.Stimmen',
        'NEIN-Stimmen':     r'NEIN.Stimmen',
    }
    hits = {}
    for label, pat in top_patterns.items():
        hits[label] = len(re.findall(pat, full_text, re.IGNORECASE | re.MULTILINE))

    return {
        'seiten': len(pages),
        'zeichen': len(full_text),
        'zeilen_gesamt': total_lines,
        'zeilen_leer': empty_lines,
        'zeilen_einzel_wort': single_word_lines,
        'einzel_wort_anteil': f"{single_word_lines/total_lines*100:.0f}%" if total_lines else "?",
        'muster': hits,
    }


# ─── Markdown-Ausgabe ─────────────────────────────────────────────────────────

def write_markdown(pdfs: list[Path], out_path: Path, show_invisible: bool):
    with open(out_path, 'w', encoding='utf-8') as f:

        # Dokument-Header
        f.write(f"# WEG PDF Dump – Roher Text\n\n")
        f.write(f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"PDFs: {len(pdfs)}\n\n")
        f.write("---\n\n")

        # Inhaltsverzeichnis
        f.write("## Inhaltsverzeichnis\n\n")
        for pdf in pdfs:
            anchor = pdf.name.lower().replace(' ', '-').replace('.', '').replace('_', '-')
            f.write(f"- [{pdf.name}](#{anchor})\n")
        f.write("\n---\n\n")

        for pdf_idx, pdf_path in enumerate(pdfs):
            print(f"  [{pdf_idx+1}/{len(pdfs)}] {pdf_path.name}...")

            pages = extract_raw(pdf_path)
            stats = analyze_text(pages)

            anchor = pdf_path.name.lower().replace(' ', '-').replace('.', '').replace('_', '-')
            f.write(f"## {pdf_path.name}\n\n")
            f.write(f"**Pfad:** `{pdf_path}`\n\n")

            # Statistik-Tabelle
            f.write("### Statistik\n\n")
            f.write("| Kennzahl | Wert |\n")
            f.write("|---|---|\n")
            f.write(f"| Seiten | {stats['seiten']} |\n")
            f.write(f"| Zeichen gesamt | {stats['zeichen']:,} |\n")
            f.write(f"| Zeilen gesamt | {stats['zeilen_gesamt']:,} |\n")
            f.write(f"| Leerzeilen | {stats['zeilen_leer']:,} |\n")
            f.write(f"| Einzel-Wort-Zeilen | {stats['zeilen_einzel_wort']:,} ({stats['einzel_wort_anteil']}) |\n")
            f.write("\n")

            # Muster-Treffer
            f.write("### Gefundene Muster\n\n")
            f.write("| Muster | Treffer |\n")
            f.write("|---|---|\n")
            for label, count in stats['muster'].items():
                marker = " ✓" if count > 0 else ""
                f.write(f"| `{label}` | {count}{marker} |\n")
            f.write("\n")

            # Roher Text pro Seite
            f.write("### Roher Text\n\n")
            for page_nr, raw_text in pages:
                f.write(f"#### Seite {page_nr}\n\n")
                f.write("```\n")
                display = make_visible(raw_text) if show_invisible else raw_text
                # Sicherstellen dass ``` im Text nicht den Block bricht
                display = display.replace('```', "'''")
                f.write(display)
                if not display.endswith('\n'):
                    f.write('\n')
                f.write("```\n\n")

            f.write("---\n\n")

    print(f"\n✅ Dump geschrieben: {out_path}")
    print(f"   Größe: {out_path.stat().st_size / 1024:.0f} KB")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='WEG PDFs → Roher Text als Markdown-Dump',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python3 weg_pdf_dump.py output/
  python3 weg_pdf_dump.py output/ --out analyse/dump.md
  python3 weg_pdf_dump.py output/ --filter Frauentor
  python3 weg_pdf_dump.py protokoll.pdf
  python3 weg_pdf_dump.py output/ --invisible     # Steuerzeichen sichtbar machen
        """
    )
    parser.add_argument('input',
                        help='Ordner mit PDFs oder einzelne PDF-Datei')
    parser.add_argument('--out', '-o', default=DEFAULT_OUT,
                        help=f'Ausgabe-Markdown (Standard: {DEFAULT_OUT})')
    parser.add_argument('--filter', '-f', default='',
                        help='Nur PDFs die diesen String im Dateinamen enthalten')
    parser.add_argument('--invisible', action='store_true',
                        help='Steuerzeichen ([TAB], [CR], ...) sichtbar machen')
    args = parser.parse_args()

    input_p = Path(args.input)
    out_p   = Path(args.out)

    if input_p.is_dir():
        pdfs = sorted(input_p.glob('*.pdf'))
        if args.filter:
            pdfs = [p for p in pdfs if args.filter.lower() in p.name.lower()]
        if not pdfs:
            print(f"Keine PDFs gefunden in '{input_p}'"
                  + (f" mit Filter '{args.filter}'" if args.filter else "") + ".")
            sys.exit(1)
        print(f"Gefunden: {len(pdfs)} PDF(s)")
    elif input_p.is_file():
        pdfs = [input_p]
    else:
        print(f"Fehler: '{args.input}' nicht gefunden.")
        sys.exit(1)

    # Ausgabe-Ordner anlegen falls nötig
    out_p.parent.mkdir(parents=True, exist_ok=True)

    print(f"Ausgabe: {out_p}\n")
    write_markdown(pdfs, out_p, show_invisible=args.invisible)


if __name__ == '__main__':
    main()
