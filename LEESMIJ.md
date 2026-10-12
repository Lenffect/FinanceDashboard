# Lenffect administratie

Lokale boekhoud-app voor je eenmanszaak en de samenwerking met Jelmer. Alles blijft op je eigen computer.

## Starten
1. Dubbelklik op `Start.command` (eerste keer: rechtsklik → Open). Er opent een Terminal-venster en je browser gaat naar http://127.0.0.1:8765.
2. Werkt dubbelklikken niet? Open Terminal, sleep deze map erin (`cd <map>`) en typ `python3 server.py`.
3. Stoppen: sluit het Terminal-venster.

Je hebt alleen Python 3 nodig (op een Mac krijg je dat bij de eerste keer starten via Apple's "command line tools").

## Waar staat wat
- `data/administratie.db` – al je gegevens (bij elke start een back-up in `data/backup`).
- `data/logo.png` – logo op de facturen (vervang door een eigen png).
- `hulpmiddelen/importeer_spreadsheets.py` – eenmalige import uit je Excel-bestanden (al gedaan; met `--reset` wist het alles en begint opnieuw!).

## Facturen verwerken via de Inbox
1. Zet facturen (pdf, jpg, png) in de map `inbox` of sleep ze in het scherm Inbox in de app.
2. Zeg tegen Claude: "verwerk de inbox". Claude leest de facturen en maakt er **concepten** van (leverancier, datum, bedrag, btw, btw-soort).
3. Jij opent elk concept in de app: links de factuur, rechts de ingevulde gegevens. Pas aan wat niet klopt en klik Goedkeuren.
4. Concepten tellen pas mee in btw en overzichten nadat jij ze hebt goedgekeurd. De factuur wordt bewaard in `facturen/<jaar>/Q<n>/`.

## Hoe werkt het
- Elke boeking heeft een soort (inkomst, kosten, privé-uitgave, storting, btw-betaling) en "van wie": alleen ik, gedeeld (met jouw percentage) of alleen Jelmer.
- Btw-aangifte: kies kwartaal; de bedragen staan per rubriek (1a, 4a/4b verlegd, 5b voorbelasting).
- Verrekening: toont wat Jelmer nog mag factureren op basis van zijn deel van betaalde omzet, gedeelde kosten en zijn facturen.
- Facturen: nieuwe factuur → "Opslaan & bekijken" → "Afdrukken / opslaan als PDF". De factuur komt automatisch als inkomst in je boekhouding.

## Bank afstemmen (maandelijks, 5 minuten)
1. Download de csv van ING (zakelijk) en Wise (EUR) en sleep ze in **Bank afstemmen**.
2. Bovenaan zie je per rekening of het saldo klopt. Zo niet, dan staat eronder wat ontbreekt.
3. Regels die niet in de administratie staan: **Maak concept** en keur het goed in de Inbox.
4. Wise-regels zonder zakelijke boeking tellen als privé; het verschil per maand boek je met één klik.
