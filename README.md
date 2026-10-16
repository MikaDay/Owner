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
