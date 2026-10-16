# PDF-uri transport din mail

Aplicația descarcă PDF-urile primite pe mail și le reorganizează așa:

| Original (6 pagini) | Rezultat (5 pagini) |
|---|---|
| 1 – detalii transport | 1 – detalii transport |
| 2, 3 | 1 – detalii transport (copie) |
| 4, 5, 6 – termeni și condiții | 4, 5, 6 – termeni și condiții |

## Instalare (o singură dată)

1. Instalează [Python 3](https://www.python.org/downloads/) (pe Windows bifează *Add Python to PATH*).
2. În folderul aplicației rulează:
   ```
   pip install -r requirements.txt
   ```
3. Copiază `config.example.ini` ca `config.ini` și completează adresa de mail și parola.
   - **Gmail:** activează verificarea în 2 pași, apoi creează o *parolă de aplicație*
     la https://myaccount.google.com/apppasswords și pune-o la `password`.
   - **Outlook / Yahoo:** la fel, folosește o parolă de aplicație.
   - Recomandat: completează `from` (expeditorul) și/sau `subject` ca să fie luate doar mailurile cu transporturi.

## Utilizare

- **Windows:** dublu-click pe `porneste.bat`.
- **Din terminal:** `python app.py`

PDF-urile originale ajung în `descarcate/`, cele procesate în `procesate/`
(ex: `Comanda 5_procesat.pdf`). Aplicația ține minte mailurile deja descărcate
(în `.processed.json`), deci o poți rula oricând fără să obții dubluri.

Fără mail, doar procesare:

```
python app.py fisier1.pdf fisier2.pdf    # fișiere anume
python app.py --local un_folder          # toate PDF-urile dintr-un folder
```

Numărul de copii ale primei pagini și numărul de pagini de la final se pot schimba
în `config.ini` (`first_page_copies`, `last_pages`).

## Site cu conectare la mail (Yahoo) — pentru iPhone / iPad

`web_app.py` e un site care se conectează la mail, îți arată mailurile cu
PDF-uri și îți dă înapoi PDF-urile procesate: **un singur PDF** cu toate
(gata de printat) sau un **ZIP** cu câte un PDF pentru fiecare mail.
Mailurile deja descărcate sunt marcate, iar cele noi sunt bifate automat.

Înainte de descărcare vezi o pagină de verificare: miniaturile fiecărui document
(apasă pe una ca s-o vezi mare), numele fișierului luat din numărul comenzii
(îl poți modifica) și o avertizare dacă un PDF nu are 6 pagini.

### 1. Parola de aplicație Yahoo

login.yahoo.com → **Securitatea contului** → **Generează parolă de aplicație**
→ copiază codul de 16 litere. (Parola obișnuită nu merge.)

### 2. Găzduirea (gratuit, pe Render.com)

1. Fă-ți cont pe https://render.com cu contul de GitHub.
2. **New → Blueprint** și alege acest repo (folosește `render.yaml`).
3. Completează variabilele:

| Variabilă | Ce pui |
|---|---|
| `MAIL_USER` | adresa ta, ex. `nume@yahoo.com` |
| `MAIL_PASSWORD` | parola de aplicație Yahoo (16 litere) |
| `SITE_PASSWORD` | o parolă aleasă de tine, cerută când deschizi site-ul |
| `FILTER_FROM` | opțional: expeditorul comenzilor, ex. `comenzi@firma.ro` |
| `FILTER_SUBJECT` | opțional: text din subiect, ex. `transport` |

4. După deploy primești o adresă de forma `https://pagini-transport.onrender.com`.
   Deschide-o în Safari → Partajează → **Adaugă pe ecranul principal**.

Pe planul gratuit site-ul „adoarme” după 15 minute fără folosire și pornește
în ~1 minut la prima deschidere; tot acolo, marcajul „descărcat” se pierde
la repornire (pentru a-l păstra, adaugă un disc și setează `DATA_DIR`).
Merge și pe orice alt server: `docker build -t transport . && docker run -p 8000:8000 --env-file .env transport`.

Alte setări: `MAIL_SERVER` (implicit `imap.mail.yahoo.com`; Gmail = `imap.gmail.com`),
`MAIL_FOLDER` (implicit `INBOX`), `FIRST_PAGE_COPIES` (2), `LAST_PAGES` (3), `EXPECTED_PAGES` (6).

## Pe iPhone / iPad, fără server

Python nu rulează pe iOS, așa că există și o versiune web în `web/index.html`
(folosește [pdf-lib](https://pdf-lib.js.org), totul se face local, în browser):

1. În Mail, ține apăsat pe atașamentul PDF → **Salvează în Fișiere**.
2. Deschide pagina, apasă **Alege PDF-uri** și selectează unul sau mai multe fișiere.
3. Apasă **Salvează** (poți bifa să pună toate documentele într-un singur PDF, util la printat).

Pagina poate fi deschisă ca artifact în aplicația Claude sau găzduită oriunde
(de ex. GitHub Pages) și adăugată pe ecranul principal din Safari
(Partajează → Adaugă pe ecranul principal).
