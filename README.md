# FinanceDashboard

Lokale administratie-app voor eenmanszaak **Lenffect** (handelsnaam Fluid Marketing): boekingen, btw-aangifte, bankafstemming, facturen, abonnementen, verrekening met Jelmer en een privé-vermogensoverzicht.

Alles draait lokaal op de eigen computer. Er is geen cloud en er zijn geen externe dependencies.

## Techniek
- Python 3 (alleen standaardbibliotheek): `http.server` en `sqlite3`
- Frontend: vanilla JS in één bestand (`index.html`)
- Database: SQLite (`data/administratie.db`)

## Starten
1. Dubbelklik op `Start.command` (of voer `python3 server.py` uit).
2. Open http://127.0.0.1:8765.

## Onderdelen
| Bestand | Doel |
|---|---|
| `server.py` | Backend, API en factuur-HTML |
| `bank.py` | Import en afstemming ING/Wise (csv) |
| `prive_bank.py` | Privé-bankimport en vermogensoverzicht |
| `export.py` | Platte kopie (csv, json, xlsx) |
| `index.html` | De volledige interface |
| `hulpmiddelen/` | Losse scripts, o.a. concepten toevoegen vanuit de inbox |

## Privacy
Financiële gegevens staan **niet** in deze repository: database, `facturen/`, `inbox/`, bankbestanden, `btw/` en back-ups worden genegeerd via `.gitignore`.

## Meer uitleg
Zie [LEESMIJ.md](LEESMIJ.md) voor de gebruikershandleiding (inbox, bank afstemmen, export).
