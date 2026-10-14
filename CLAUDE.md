# Lennar Finance (administratie Lenffect / Fluid Marketing)

Persoonlijke administratie-app van Lennar Schumacher (eenmanszaak **Lenffect**, handelsnaam **Fluid Marketing**,
samenwerking met Jelmer de Weijer / JPPW Consultancy). Taal van de app én van alle communicatie: **Nederlands**.

## Techniek
- Python 3 stdlib (`http.server`, `sqlite3`), geen dependencies. Start met `./Start.command` (poort 8765, maakt een backup in `data/backup`).
- `server.py`: backend + API + factuur-HTML (`/factuur/<id>`). `bank.py`: csv-import en afstemming ING/Wise.
- `index.html`: de hele UI in vanilla JS. Pagina's: Overzicht, Inbox, Bank afstemmen, Boekingen, Btw-aangifte, Verrekening Jelmer, Facturen, Abonnementen, Klanten & leveranciers, Instellingen.
- Data: `data/administratie.db` (SQLite). **Staat niet in git** (privé financiële gegevens), net als `facturen/`, `inbox/` en `data/backup/`.
- `hulpmiddelen/importeer_spreadsheets.py` was een eenmalige import. **Nooit `--reset` draaien op de echte database.**
- `hulpmiddelen/concept_toevoegen.py`: voegt concepten toe uit een JSON-lijst en verplaatst het inbox-bestand naar het archief.

## Boekhoudmodel (tabel `boekingen`)
- `soort`: inkomst, kosten, prive_uitgave, storting, btw_betaling, overboeking, overige_baten, btw_teruggave.
- `toewijzing`: ik / gedeeld (met `pct_ik`) / jelmer. `rekening`: ing, wise, prive. `status`: concept/definitief (concepten tellen nergens mee: zie `NC` in server.py).
- `btw_soort`: nl21, nl9, nl0, eu_verlegd (4b), buiten_eu_verlegd (4a), buitenlandse_btw (niet aftrekbaar), geen.
- Btw: eenmanszaak, per kwartaal, geen KOR, factuurstelsel. Verlegd bedrag telt in 4a/4b en 5b (netto nul). Bij gedeelde kosten claimt Lennar alleen zijn eigen deel voorbelasting.
- Gedeelde omzet telt 100% mee in Lennars btw (hij factureert); Jelmers deel komt terug als JPPW-factuur (categorie `JPPW-factuur`).
- Verrekening Jelmer: schuld = betaalde omzet-aandeel − gedeelde kosten − betaalde JPPW-facturen.
- Bedragen in de aangifte worden door Lennar op hele euro's afgerond; kleine verschillen met de berekening zijn normaal.
- Wise-kaart wordt ook privé gebruikt: alles zonder zakelijke boeking is `prive_uitgave` (per maand samengevat). Wise-kosten per transactie zitten in de bank-regel; aparte boeking alleen categorie `Bankkosten`.

## Werkwijze facturen (inbox)
1. Lennar zet pdf/jpg/png in `inbox/`.
2. Lees elk bestand (tekst/afbeelding), bepaal datum, leverancier, bedrag excl./btw, btw-soort, betaald-datum en wie het betaalt.
3. Maak **concepten** (geen definitieve boekingen) en verplaats het bestand naar `facturen/<jaar>/Q<k>/` (`concept_toevoegen.py`).
4. Lennar controleert in de app (Inbox) en keurt goed. Controleer eerst of er al een boeking is (zelfde bedrag/datum) om dubbelen te voorkomen.
5. Buitenlandse leveranciers die btw rekenen (Freepik, Gumroad, Artlist): btw als nl21 geboekt, voor de boekhouder gemarkeerd.

## Open punten voor de boekhouder
Afschrijving iPhone en MacBook; ING-promotie €150 (winst?); Nederlandse btw op buitenlandse leveranciers.

## Afspraken
- Wijzig geen data in de echte database zonder dat Lennar het vraagt; maak bij twijfel een concept of vraag het.
- Maak vóór schema- of data-aanpassingen een kopie in `data/backup/`.
- Commit klein en beschrijvend (Nederlands of Engels, consistent). Geen database, facturen of bankbestanden committen.

## Privé (Lennar Finance)
- Privé-gegevens (tabellen `prive_*`, bankafschriften uit `inbox/`) blijven **lokaal**: `export.py` zet ze niet in de leesbare export (csv/json/xlsx), alleen in de volledige database-kopie `administratie_kopie.db` in de iCloud-exportmap (door Lennar goedgekeurd als eigen backup). Houd dat zo bij nieuwe tabellen.
- `prive_bank.py`: leest ING-csv (lopend + spaar), markeert interne overboekingen (eigen rekeningen, spaar, Wise) zodat ze niet als inkomen/uitgave tellen.
