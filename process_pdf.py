"""Reorganizează un PDF: prima pagină de N ori + ultimele M pagini."""

import io
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def page_order(total, first_page_copies=2, last_pages=3):
    """Indicii (de la 0) paginilor păstrate. Pentru 6 pagini: 0, 0, 3, 4, 5.

    Prima pagină nu e repetată la final dacă documentul e prea scurt."""
    start_last = max(1, total - last_pages)
    return [0] * first_page_copies + list(range(start_last, total))


def _reorder(reader, first_page_copies, last_pages, name):
    total = len(reader.pages)
    if total == 0:
        raise ValueError(f"{name}: PDF-ul nu are pagini")
    order = page_order(total, first_page_copies, last_pages)
    writer = PdfWriter()
    for i in order:
        writer.add_page(reader.pages[i])
    return writer, [i + 1 for i in order]


def process_bytes(data, first_page_copies=2, last_pages=3, name="PDF"):
    """Ca process_pdf, dar primește și întoarce conținutul PDF-ului (bytes)."""
    writer, pages = _reorder(PdfReader(io.BytesIO(data)), first_page_copies, last_pages, name)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), pages


def process_pdf(src, dst, first_page_copies=2, last_pages=3):
    """Scrie în `dst` prima pagină de `first_page_copies` ori, urmată de
    ultimele `last_pages` pagini din `src`.

    Pentru un PDF de 6 pagini rezultatul este: 1, 1, 4, 5, 6.
    Returnează lista indicilor (de la 1) paginilor scrise.
    """
    writer, pages = _reorder(PdfReader(str(src)), first_page_copies, last_pages, src)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as f:
        writer.write(f)
    return pages


def merge(pdfs):
    """Lipește mai multe PDF-uri (bytes) într-unul singur."""
    writer = PdfWriter()
    for data in pdfs:
        for page in PdfReader(io.BytesIO(data)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# Numărul comenzii de pe prima pagină, ex. „Nr. comandă: TR-2026/1458”,
# „Comanda transport nr 884213”, „Comanda Transport Nr. ARWY01981” (DSV). Ținut în sincron cu findOrderNumber() din web/index.html.
_ID = r"([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/]*)"
_ORDER_PATTERNS = [re.compile(p, re.I) for p in (
    # „Comanda Transport Nr. ARWY01981” — numărul comenzii de transport are prioritate
    # față de referința clientului („Nr. Comanda: S6422…”) de mai jos în pagină.
    r"comand[aă]\s+(?:de\s+)?transport\s*(?:nr|no)\.?\s*[:#]?\s*" + _ID,
    r"(?:nr|num[aă]r|no|number)\.?\s*(?:de\s+)?(?:comand[aă]|order|transport|cmr)\s*[:#.]?\s*" + _ID,
    r"(?:comand[aă]|order|transport|cmr)(?:\s+de\s+transport)?\s*(?:nr|no|number)?\.?\s*[:#]?\s*" + _ID,
    r"\b(?:nr|no)\.?\s*[:#]?\s*" + _ID,
)]


def find_order_number(text):
    flat = re.sub(r"\s+", " ", text or "")
    for pattern in _ORDER_PATTERNS:
        m = pattern.search(flat)
        if m:
            return m.group(1).rstrip(".-/")
    return ""


def first_page_text(data):
    """Textul primei pagini, sau "" dacă nu poate fi citit (ex. PDF scanat)."""
    try:
        return PdfReader(io.BytesIO(data)).pages[0].extract_text() or ""
    except Exception:
        return ""


def order_number(data):
    """Numărul comenzii din PDF (bytes) sau "" dacă nu e găsit (ex. PDF scanat)."""
    return find_order_number(first_page_text(data))


def page_count(data):
    return len(PdfReader(io.BytesIO(data)).pages)


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|\r\n]+', "-", name).strip(" .") or "document"
