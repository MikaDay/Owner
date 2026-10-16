"""Reorganizează un PDF: prima pagină de N ori + ultimele M pagini."""

from pathlib import Path

from pypdf import PdfReader, PdfWriter


def process_pdf(src, dst, first_page_copies=2, last_pages=3):
    """Scrie în `dst` prima pagină de `first_page_copies` ori, urmată de
    ultimele `last_pages` pagini din `src`.

    Pentru un PDF de 6 pagini rezultatul este: 1, 1, 4, 5, 6.
    Returnează lista indicilor (de la 1) paginilor scrise.
    """
    reader = PdfReader(str(src))
    total = len(reader.pages)
    if total == 0:
        raise ValueError(f"{src}: PDF-ul nu are pagini")

    # Nu repeta prima pagină dacă documentul e prea scurt și se suprapune cu finalul.
    start_last = max(1, total - last_pages)
    order = [0] * first_page_copies + list(range(start_last, total))

    writer = PdfWriter()
    for i in order:
        writer.add_page(reader.pages[i])

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as f:
        writer.write(f)
    return [i + 1 for i in order]
