# Cirforvest

Comenzile de transport DSV, de la mail la print și până la încasare.

Site: **https://cirforvest.onrender.com** (se adaugă pe ecranul principal al telefonului ca o aplicație).

## Ce face

- **Comenzi noi** (un buton): ia din Yahoo comenzile de la expeditorul setat — necitite, primite după
  ultima descărcare sau deschise printre cele necitite —, le recunoaște după conținut (nu după subiect),
  le reorganizează (detaliile ×2 + termenii și condițiile; detaliile pot avea mai multe pagini) și deschide
  pagina de verificare, cu progres „comanda 7 din 14”.
- **Verificare**: miniaturi (glisare, ciupire pentru mărire), nume după numărul comenzii, comenzi cu
  Toll_RO separate, încărcările de azi/mâine marcate, duplicate și comenzi **modificate de DSV** (cu ce
  s-a schimbat), avertizare când PDF-ul pare în alt format.
- **Trimite pe mail** (cu confirmare): două mailuri în Inbox — Toll_RO, apoi restul — sortate după data
  încărcării. Comenzile intră în registru și devin citite. Alte ieșiri: ZIP, ZIP pe camion, Excel,
  „Trimite șoferului” (doar detaliile, prin meniul de partajare).
- **Registru (Comenzi)**: pentru fiecare comandă — ePOD (link + colectare/livrare/CMR), termenul pentru
  documentele originale (5 zile lucrătoare, 10 internațional, cu sărbătorile legale), scadența plății
  (45 de zile), încasarea, suma Toll_RO, notă, istoricul versiunilor. Comenzile mai vechi se pot aduce din
  mail. Căutare după număr (și în mailurile vechi), istoric trimiteri, calendar (abonare din telefon).
- **Rapoarte**: pe luni, pe camion, prețuri pe traseu, sume de încasat/restante, întârzieri medii;
  registrul Excel pe orice perioadă și date pentru facturare (CSV, cu numărul comenzii pentru BT-10).
- **Automat** (Setări): notificări pe telefon pentru comenzi noi, rezumatul zilei, trimiterea automată la
  ora aleasă, registrul lunar în Inbox pe 1 ale lunii, alertă dacă parola Yahoo nu mai merge.
- **Conturi**: administrator + utilizatori cu parola lor, schimbarea parolei, cheie separată pentru
  scurtătura iPhone; limită de încercări la parolă.
- **Telefon**: iPhone — scurtătura „Cirforvest” din meniul de partajare (pagina `/scurtatura`);
  Android — Cirforvest apare singur în meniul „Distribuie” după „Instalează aplicația”.

Memoria aplicației (registrul, ce s-a descărcat, setările, conturile, abonamentele la notificări) stă în
contul Yahoo, în folderul **Cirforvest** — un singur mesaj-jurnal. **Nu șterge folderul și nici mesajul.**

## Setări pe server (Render → Environment)

| Variabilă | Ce e |
|---|---|
| `MAIL_SERVER`, `MAIL_USER`, `MAIL_PASSWORD` | contul de mail (Yahoo: `imap.mail.yahoo.com` + parola de aplicație) |
| `SITE_PASSWORD` | parola administratorului (până o schimbi din Setări) |
| `SECRET_KEY` | cheia sesiunilor (orice text lung, aleator) |
| `FILTER_FROM` | expeditorul comenzilor (ex. `ilie.rumega@dsv.com`) |
| `CRON_TOKEN` *(opțional)* | dacă e setat, verificarea periodică trebuie să-l trimită (pune același text în GitHub → Settings → Secrets → Actions → `CRON_TOKEN`) |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` *(opțional)* | cheile pentru notificări; dacă lipsesc, serverul le generează singur |
| `NEW_ORDERS_DAYS`, `EXPECTED_PAGES`, `FIRST_PAGE_COPIES`, `LAST_PAGES` | 45, 6, 2, 3 implicit |

Verificarea periodică (notificări, trimitere automată, raport lunar) o face GitHub Actions
(`.github/workflows/tick.yml`) la ~30 de minute. Dacă site-ul nu răspunde, rularea eșuează și GitHub
trimite un mail.

## Dezvoltare

```
pip install -r requirements-dev.txt
python -m pytest -q tests      # rulează și pe GitHub la fiecare modificare
python web_app.py               # local, pe portul 8000
```

Testele folosesc un server IMAP în memorie și PDF-uri DSV generate (fără date reale).

## De verificat pe un iPhone real

1. Safari → adresa site-ului → Partajează → „Adaugă pe ecranul principal”; iconița Cirforvest apare și
   aplicația se deschide fără bara Safari.
2. Setări → Notificări → „Activează” (din aplicația de pe ecranul principal) → „Trimite o notificare de
   test” → apare notificarea.
3. Setări iPhone → Afișaj → Mărimea textului la maxim: textul din aplicație crește, butoanele rămân
   folosibile.
4. VoiceOver pornit: butoanele și căsuțele au nume citite corect; pagina de verificare se poate parcurge.
5. Pagina de verificare: glisare între pagini, ciupire, tragere în jos pentru închidere.
6. „Trimite șoferului” → apare meniul de partajare cu PDF-ul.
7. Comenzi → „Abonează-te în Calendar” → evenimentele apar în Calendar.
8. După 20 de minute fără folosire, deschide aplicația: apare „Serverul pornește…”, apoi pagina.

## Aplicația de calculator (fără server)

`app.py` (Windows: `porneste.bat`) descarcă PDF-urile prin IMAP, le procesează în `procesate/` și scrie
registrul Excel. Setări în `config.ini` (vezi `config.example.ini`).
