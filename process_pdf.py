"""Reorganizează un PDF: prima pagină de N ori + ultimele M pagini."""

import io
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
