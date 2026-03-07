#!/usr/bin/env python3
"""
WEG Protokoll Processor (v2 – ocrmypdf)
========================================
Verarbeitet gescannte Eigentümerversammlungs-Protokolle:
- OCR + Textlayer: ocrmypdf (deutlich bessere Qualität als reines pytesseract)
- Farbige Hervorhebung von Beirat-Begriffen (via pytesseract HOCR für Positionen)

Voraussetzungen (einmalig installieren):
    macOS:
        brew install ocrmypdf tesseract tesseract-lang ghostscript
        pip install ocrmypdf pypdf pdf2image pytesseract reportlab pillow

    Ubuntu:
        sudo apt install ocrmypdf tesseract-ocr tesseract-ocr-deu ghostscript
        pip install ocrmypdf pypdf pdf2image pytesseract reportlab pillow

Verwendung:
    python3 weg_protokoll_processor.py input.pdf
    python3 weg_protokoll_processor.py input.pdf output.pdf
    python3 weg_protokoll_processor.py input/ output/
    python3 weg_protokoll_processor.py input/ output/ --fallback   # ohne ocrmypdf
"""

import argparse
import io
import shutil
import subprocess
import sys
import re
import tempfile
from pathlib import Path

from pdf2image import convert_from_path
import pytesseract
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DictionaryObject, FloatObject, NameObject,
    NumberObject, create_string_object
)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

# ─── Konfiguration ────────────────────────────────────────────────────────────

HIGHLIGHT_TERMS = ['beirat', 'beiräte', 'verwaltungsbeirat', 'beirats']
HIGHLIGHT_COLOR = (1.0, 0.85, 0.0)
HIGHLIGHT_ALPHA = 0.45
DPI             = 300     # Höher als v1 → bessere OCR-Qualität
JPEG_QUALITY    = 85

# ─── Voraussetzungs-Checks ────────────────────────────────────────────────────

def check_ocrmypdf() -> bool:
    if shutil.which('ocrmypdf'):
        try:
            r = subprocess.run(['ocrmypdf', '--version'], capture_output=True, text=True)
            print(f"  ocrmypdf: {(r.stdout or r.stderr).strip()}")
            return True
        except Exception:
            pass
    print("  ⚠  ocrmypdf nicht gefunden.")
    print("     macOS:  brew install ocrmypdf")
    print("     Ubuntu: sudo apt install ocrmypdf")
    return False


def detect_tesseract_lang() -> str:
    try:
        if 'deu' in pytesseract.get_languages():
            return 'deu'
    except Exception:
        pass
    return 'eng'


# ─── OCR via ocrmypdf ─────────────────────────────────────────────────────────

def run_ocrmypdf(input_path: Path, output_path: Path) -> bool:
    """
    Führt ocrmypdf aus.
    Vorteile gegenüber pytesseract-Einzelwörter:
    - Zeilenweiser Textlayer → pypdf.extract_text() liefert lesbaren Text
    - Automatisches Deskewing (schräge Scans gerade biegen)
    - Bildrauschen-Reduzierung vor OCR
    - Erhält originale Seitengröße und Bildqualität
    """
    cmd = [
        'ocrmypdf',
        '--language', 'deu',
        '--deskew',
        '--clean',
        '--optimize', '1',
        '--output-type', 'pdf',
        '--jobs', '2',
        '--quiet',
        str(input_path),
        str(output_path),
    ]
    print(f"  ocrmypdf läuft... ", end='', flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            print("✓")
            return True
        # Exit 6 = bereits Textlayer vorhanden → mit --force-ocr
        if r.returncode == 6:
            print("(erzwinge Neu-OCR)... ", end='', flush=True)
            r2 = subprocess.run(['ocrmypdf', '--force-ocr'] + cmd[1:],
                                capture_output=True, text=True, timeout=300)
            if r2.returncode == 0:
                print("✓")
                return True
            print(f"✗\n  {r2.stderr[-200:]}")
            return False
        print(f"✗ (exit {r.returncode})\n  {r.stderr[-200:]}")
        return False
    except subprocess.TimeoutExpired:
        print("✗ (Timeout)")
        return False
    except FileNotFoundError:
        print("✗ (nicht gefunden)")
        return False


# ─── Beirat-Highlights via pytesseract HOCR ──────────────────────────────────

def parse_hocr(hocr_bytes: bytes) -> list:
    text = hocr_bytes.decode('utf-8')
    pattern = r"bbox (\d+) (\d+) (\d+) (\d+)[^>]*>([^<]*)</span>"
    return [(int(x1), int(y1), int(x2), int(y2), w.strip())
            for x1, y1, x2, y2, w in re.findall(pattern, text) if w.strip()]


def is_beirat(word: str) -> bool:
    w = word.lower().strip('.,;:()-/')
    return any(t in w for t in HIGHLIGHT_TERMS)


def add_highlights(ocr_pdf_path: Path, output_path: Path, lang: str):
    """
    Liest das fertige ocrmypdf-PDF, erkennt Beirat-Wörter via HOCR
    und merged gelbe Highlight-Overlays auf jede Seite.
    """
    print(f"[3/4] Beirat-Hervorhebungen...")
    images     = convert_from_path(str(ocr_pdf_path), dpi=DPI)
    ocr_reader = PdfReader(str(ocr_pdf_path))
    writer     = PdfWriter()
    total_hits = 0

    for page_num, (img, ocr_page) in enumerate(zip(images, ocr_reader.pages)):
        print(f"      Seite {page_num+1:2d}/{len(images)}...", end='  ')

        img_w, img_h = img.size
        pdf_w = float(ocr_page.mediabox.width)
        pdf_h = float(ocr_page.mediabox.height)
        sx, sy = pdf_w / img_w, pdf_h / img_h

        hocr  = pytesseract.image_to_pdf_or_hocr(img, lang=lang, extension='hocr')
        words = parse_hocr(hocr)
        hits  = [w for w in words if is_beirat(w[4])]
        total_hits += len(hits)
        if hits:
            print(f"→ {len(hits)}x Beirat", end='  ')

        if hits:
            pkt = io.BytesIO()
            c   = canvas.Canvas(pkt, pagesize=(pdf_w, pdf_h))
            for x1, y1, x2, y2, _ in hits:
                c.setFillColorRGB(*HIGHLIGHT_COLOR, alpha=HIGHLIGHT_ALPHA)
                c.setStrokeColorRGB(0.9, 0.65, 0.0)
                c.setLineWidth(0.5)
                c.rect(x1*sx-2, pdf_h-y2*sy-2, (x2-x1)*sx+4, (y2-y1)*sy+4,
                       fill=1, stroke=1)
            c.save()
            pkt.seek(0)
            ocr_page.merge_page(PdfReader(pkt).pages[0])

        writer.add_page(ocr_page)
        print("✓")

    print(f"      Gesamt: {total_hits} Beirat-Treffer")
    print(f"[4/4] Speichere...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        writer.write(f)


# ─── Fallback: reines pytesseract ────────────────────────────────────────────

def _group_to_lines(words: list, tol: int = 4) -> list:
    if not words:
        return []
    line_map = {}
    for w in words:
        x1, y1, x2, y2, word = w
        yc = (y1 + y2) // 2
        key = next((k for k in line_map if abs(k - yc) <= tol), None)
        if key is None:
            key = yc; line_map[key] = []
        line_map[key].append(w)
    result = []
    for yk in sorted(line_map):
        lw = sorted(line_map[yk], key=lambda w: w[0])
        result.append((min(w[1] for w in lw), max(w[3] for w in lw),
                       lw[0][0], ' '.join(w[4] for w in lw), lw))
    return result


def process_fallback(input_path: Path, output_path: Path, lang: str):
    """Fallback ohne ocrmypdf – pytesseract mit zeilenweisem Textlayer."""
    print(f"[1/4] PDF → Bilder (DPI={DPI})...")
    images = convert_from_path(str(input_path), dpi=DPI)
    print(f"      {len(images)} Seiten")
    writer = PdfWriter()

    print(f"[2/4] OCR via pytesseract...")
    for page_num, img in enumerate(images):
        print(f"      Seite {page_num+1:2d}/{len(images)}...", end='  ')
        img_w, img_h = img.size
        PDF_W, PDF_H = A4
        sx, sy = PDF_W / img_w, PDF_H / img_h

        hocr  = pytesseract.image_to_pdf_or_hocr(img, lang=lang, extension='hocr')
        words = parse_hocr(hocr)
        hits  = [w for w in words if is_beirat(w[4])]
        if hits:
            print(f"→ {len(hits)}x Beirat", end='  ')

        pkt = io.BytesIO()
        c   = canvas.Canvas(pkt, pagesize=(PDF_W, PDF_H))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=JPEG_QUALITY)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=PDF_W, height=PDF_H)

        # Zeilenweiser Textlayer
        c.setFillColorRGB(0, 0, 0, alpha=0)
        for y1_px, y2_px, x1_px, line_text, _ in _group_to_lines(words):
            font_size = max(1.0, (y2_px - y1_px) * sy)
            c.setFont("Helvetica", font_size)
            c.drawString(x1_px * sx, PDF_H - y2_px * sy, line_text)

        for x1, y1, x2, y2, _ in hits:
            c.setFillColorRGB(*HIGHLIGHT_COLOR, alpha=HIGHLIGHT_ALPHA)
            c.setStrokeColorRGB(0.9, 0.65, 0.0)
            c.setLineWidth(0.5)
            c.rect(x1*sx-2, PDF_H-y2*sy-2, (x2-x1)*sx+4, (y2-y1)*sy+4, fill=1, stroke=1)

        c.save(); pkt.seek(0)
        writer.add_page(PdfReader(pkt).pages[0])
        print("✓")

    print(f"[3/4] –\n[4/4] Speichere...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        writer.write(f)


# ─── Hauptverarbeitung ────────────────────────────────────────────────────────

def process_pdf(input_path: Path, output_path: Path, lang: str, use_ocrmypdf: bool):
    print(f"\n{'─'*60}")
    print(f"  Eingabe:  {input_path.name}")
    print(f"  Ausgabe:  {output_path.name}")
    print(f"{'─'*60}")

    if use_ocrmypdf:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_ocr = Path(tmp) / 'ocr.pdf'
            print(f"[1/4] Einlesen ({input_path.stat().st_size//1024} KB)...")
            print(f"[2/4] OCR via ocrmypdf (deu, deskew, clean)...")
            ok = run_ocrmypdf(input_path, tmp_ocr)
            if not ok:
                print("  → Fallback auf pytesseract")
                process_fallback(input_path, output_path, lang)
                return
            add_highlights(tmp_ocr, output_path, lang)
    else:
        process_fallback(input_path, output_path, lang)

    mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n  ✅  {output_path.name}  ({mb:.1f} MB)")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='WEG Protokoll → durchsuchbares PDF mit Beirat-Hervorhebung (v2)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python3 weg_protokoll_processor.py protokoll.pdf
  python3 weg_protokoll_processor.py protokoll.pdf ausgabe.pdf
  python3 weg_protokoll_processor.py input/ output/
  python3 weg_protokoll_processor.py input/ output/ --fallback
        """
    )
    parser.add_argument('input',  help='PDF-Datei oder Eingangsordner')
    parser.add_argument('output', nargs='?', help='Ausgabe-PDF oder -Ordner')
    parser.add_argument('--fallback', action='store_true',
                        help='pytesseract statt ocrmypdf verwenden')
    parser.add_argument('--lang', '-l', default=None,
                        help='Tesseract-Sprache für Highlights (Standard: auto)')
    args = parser.parse_args()

    input_p      = Path(args.input)
    lang         = args.lang or detect_tesseract_lang()
    use_ocrmypdf = not args.fallback and check_ocrmypdf()

    if not use_ocrmypdf and not args.fallback:
        print("  → Fallback auf pytesseract")
    print(f"  Modus:   {'ocrmypdf' if use_ocrmypdf else 'pytesseract (Fallback)'}")
    print(f"  Sprache: {lang}")

    if input_p.is_dir():
        if not args.output:
            parser.error("Im Ordner-Modus Ausgabeordner angeben.")
        output_dir = Path(args.output)
        pdfs = sorted(p for p in input_p.glob('*.pdf')
                      if '_durchsuchbar' not in p.stem)
        if not pdfs:
            print(f"Keine PDFs in '{input_p}'.")
            sys.exit(1)
        print(f"\nBatch: {len(pdfs)} PDF(s) → '{output_dir}'")
        ok, fehler = 0, []
        for pdf in pdfs:
            out = output_dir / (pdf.stem + '_durchsuchbar.pdf')
            try:
                process_pdf(pdf, out, lang, use_ocrmypdf)
                ok += 1
            except Exception as e:
                print(f"\n  ❌ {pdf.name}: {e}")
                fehler.append(pdf.name)
        print(f"\n{'═'*60}")
        print(f"  Batch: {ok} OK", end='')
        if fehler:
            print(f", {len(fehler)} Fehler: {', '.join(fehler)}")
        else:
            print(" – keine Fehler.")
        print(f"{'═'*60}")

    elif input_p.is_file():
        out = Path(args.output) if args.output \
              else input_p.parent / (input_p.stem + '_durchsuchbar.pdf')
        process_pdf(input_p, out, lang, use_ocrmypdf)
    else:
        print(f"Fehler: '{args.input}' nicht gefunden.")
        sys.exit(1)


if __name__ == '__main__':
    main()