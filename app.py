"""Descarcă PDF-urile de transport din mail și le reorganizează:
prima pagină (detaliile transportului) x2 + ultimele 3 pagini (termeni și condiții).

Utilizare:
    python app.py                 # descarcă din mail și procesează
    python app.py --local FOLDER  # procesează PDF-urile dintr-un folder (fără mail)
    python app.py fisier.pdf ...  # procesează doar fișierele date
"""

import argparse
import configparser
import os
import sys
from pathlib import Path

from fetch_mail import fetch_pdfs
from process_pdf import process_pdf


def load_config(path):
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";",))
    if not cfg.read(path, encoding="utf-8"):
        sys.exit(f"Nu găsesc {path}. Copiază config.example.ini ca config.ini și completează-l.")
    return cfg


def process_all(files, output_dir, copies, last):
    ok = 0
    for src in files:
        dst = Path(output_dir) / f"{Path(src).stem}_procesat.pdf"
        try:
            pages = process_pdf(src, dst, copies, last)
        except Exception as e:  # un PDF stricat nu trebuie să oprească restul
            print(f"  EROARE {Path(src).name}: {e}")
            continue
        ok += 1
        print(f"  {Path(src).name} -> {dst.name}  (pagini: {', '.join(map(str, pages))})")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="PDF-uri de procesat direct (fără mail)")
    ap.add_argument("--local", metavar="FOLDER", help="procesează toate PDF-urile din FOLDER")
    ap.add_argument("--config", default="config.ini")
    args = ap.parse_args()
    args.files = [Path(f).resolve() for f in args.files]
    if args.local:
        args.local = Path(args.local).resolve()
    # Căile din config sunt relative la folderul aplicației (merge și cu dublu-click).
    os.chdir(Path(__file__).resolve().parent)

    if args.files or args.local:
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";",))
        cfg.read(args.config, encoding="utf-8")
    else:
        cfg = load_config(args.config)
    out = cfg.get("output", "output_dir", fallback="procesate")
    copies = cfg.getint("output", "first_page_copies", fallback=2)
    last = cfg.getint("output", "last_pages", fallback=3)

    if args.files:
        files = args.files
    elif args.local:
        files = sorted(Path(args.local).glob("*.pdf"))
    else:
        print("Verific mailul...")
        try:
            files = fetch_pdfs(
                server=cfg.get("email", "server"),
                port=cfg.getint("email", "port", fallback=993),
                user=cfg.get("email", "user"),
                password=cfg.get("email", "password"),
                folder=cfg.get("email", "folder", fallback="INBOX"),
                download_dir=cfg.get("output", "download_dir", fallback="descarcate"),
                sender=cfg.get("filter", "from", fallback=""),
                subject=cfg.get("filter", "subject", fallback=""),
                days=cfg.getint("filter", "days", fallback=30),
                unseen_only=cfg.getboolean("filter", "unseen_only", fallback=False),
            )
        except Exception as e:
            sys.exit(f"Eroare la conectarea la mail: {e}")

    if not files:
        print("Niciun PDF nou de procesat.")
        return
    print(f"Procesez {len(files)} PDF(-uri)...")
    ok = process_all(files, out, copies, last)
    print(f"Gata: {ok}/{len(files)} salvate în '{out}'.")


if __name__ == "__main__":
    main()
