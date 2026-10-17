"""Reorganizează un PDF: prima pagină de N ori + ultimele M pagini."""

import io
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def page_order(total, first_page_copies=2, last_pages=3, detail_pages=1):
    """Indicii (de la 0) paginilor păstrate. Pentru 6 pagini: 0, 0, 3, 4, 5.

    Când detaliile transportului se întind pe mai multe pagini (ex. 3 din 8),
    se repetă tot setul: 0, 1, 2, 0, 1, 2, 5, 6, 7. Paginile de detalii nu
    sunt repetate la final dacă documentul e prea scurt."""
    detail_pages = max(1, min(detail_pages, total))
    start_last = max(detail_pages, total - last_pages)
    return list(range(detail_pages)) * first_page_copies + list(range(start_last, total))


# Începutul paginilor care NU sunt detalii de transport: instrucțiunile ePOD și termenii.
_NOT_DETAILS = re.compile(r"Platform[aă]\s+ePOD\s*[–-]|TERMENI\s+SI\s+CONDITII", re.I)


# Ce are o pagină de detalii: locuri de încărcare/descărcare, prețul, nr. camionului.
_DETAILS = re.compile(r"Loc\s+de\s+(?:in|des)carcare|Transport:\s*[\d.]|inmatriculare\s+camion", re.I)


class _LazyTexts:
    """Textul paginilor, extras doar când e cerut și o singură dată.

    Extragerea textului e partea lentă (secunde pe serverul gratuit), iar de obicei
    ajung primele 2 pagini: detaliile și începutul instrucțiunilor ePOD."""

    def __init__(self, reader):
        self.reader, self._cache = reader, {}

    def __len__(self):
        return len(self.reader.pages)

    def __getitem__(self, i):
        if i not in self._cache:
            try:
                self._cache[i] = self.reader.pages[i].extract_text() or ""
            except Exception:
                self._cache[i] = ""
        return self._cache[i]


def page_texts(reader):
    texts = _LazyTexts(reader)
    return [texts[i] for i in range(len(texts))]


def count_detail_pages(texts):
    """Câte pagini de la început conțin detaliile transportului (cel puțin 1).

    Dacă documentul nu are pagini ePOD / termeni recunoscute (alt format, PDF scanat),
    rămâne regula simplă: doar prima pagină."""
    first_other = next((i for i in range(len(texts)) if _NOT_DETAILS.search(texts[i])), None)
    if first_other is None:
        return 1
    n = 1
    while n < first_other and _DETAILS.search(texts[n]):
        n += 1
    return n


def analyse(data, first_page_copies=2, last_pages=3, name="PDF"):
    """Totul dintr-o singură citire a PDF-ului:
    {"out": PDF-ul reorganizat, "pages": [1, 1, 4, 5, 6], "total": 6, "text": textul detaliilor}."""
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    if total == 0:
        raise ValueError(f"{name}: PDF-ul nu are pagini")
    texts = _LazyTexts(reader)
    detail = count_detail_pages(texts)
    order = page_order(total, first_page_copies, last_pages, detail)
    writer = PdfWriter()
    for i in order:
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return {"out": out.getvalue(), "pages": [i + 1 for i in order], "total": total,
            "text": "\n".join(texts[i] for i in range(detail))}


def details_text(data):
    """Textul paginilor de detalii (pentru numărul comenzii și registrul Excel)."""
    try:
        texts = _LazyTexts(PdfReader(io.BytesIO(data)))
        return "\n".join(texts[i] for i in range(count_detail_pages(texts)))
    except Exception:
        return ""


def process_bytes(data, first_page_copies=2, last_pages=3, name="PDF"):
    """Ca process_pdf, dar primește și întoarce conținutul PDF-ului (bytes)."""
    r = analyse(data, first_page_copies, last_pages, name)
    return r["out"], r["pages"]


def process_pdf(src, dst, first_page_copies=2, last_pages=3):
    """Scrie în `dst` prima pagină de `first_page_copies` ori, urmată de
    ultimele `last_pages` pagini din `src`.

    Pentru un PDF de 6 pagini rezultatul este: 1, 1, 4, 5, 6.
    Returnează lista indicilor (de la 1) paginilor scrise.
    """
    out, pages = process_bytes(Path(src).read_bytes(), first_page_copies, last_pages, str(src))
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(out)
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
    r"(?<!VAT )\b(?:nr|no)\.?\s*[:#]?\s*" + _ID,   # nu „VAT No: RO46318372” (codul de TVA)
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
    return find_order_number(details_text(data))


def page_count(data):
    return len(PdfReader(io.BytesIO(data)).pages)


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|\r\n]+', "-", name).strip(" .") or "document"
