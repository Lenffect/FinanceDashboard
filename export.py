# -*- coding: utf-8 -*-
"""Platte export van de hele administratie: csv + json per tabel, samenvatting, database-kopie en de gekoppelde facturen.

Wordt bij elke start en bij het afsluiten van de app uitgevoerd, en op verzoek (Instellingen → "Nu exporteren").
De exportmap is zelfstandig leesbaar zonder de app (Excel/Numbers, een tekstverwerker of een andere tool).
"""
import csv, json, os, shutil, sqlite3, datetime as dt
from contextlib import closing

TABELLEN_VOLGORDE = ['boekingen', 'relaties', 'abonnementen', 'facturen', 'instellingen']


def _rijen(c, tabel):
    cur = c.execute('SELECT * FROM "%s"' % tabel)
    kol = [d[0] for d in cur.description]
    return kol, [list(r) for r in cur.fetchall()]


def _schrijf_atomair(pad, tekst, encoding='utf-8'):
    tmp = pad + '.tmp'
    with open(tmp, 'w', encoding=encoding, newline='') as fh:
        fh.write(tekst)
    os.replace(tmp, pad)


def _csv_tekst(kol, rijen, delim=';'):
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delim, lineterminator='\n')
    w.writerow(kol)
    for r in rijen:
        w.writerow(['' if v is None else v for v in r])
    return '﻿' + buf.getvalue()  # BOM: Excel leest dan UTF-8 goed


def _spiegel(bron, doel):
    """Maakt doel een kopie van bron: alleen gewijzigde/nieuwe bestanden kopiëren, verdwenen bestanden opruimen. Alleen binnen de exportmap."""
    n = 0
    if not os.path.isdir(bron):
        return 0
    gezien = set()
    for root, _dirs, files in os.walk(bron):
        for f in files:
            if f.startswith('.'):
                continue
            src = os.path.join(root, f)
            rel = os.path.relpath(src, bron)
            dst = os.path.join(doel, rel)
            gezien.add(os.path.normpath(dst))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src) or int(os.path.getmtime(dst)) < int(os.path.getmtime(src)):
                shutil.copy2(src, dst)
                n += 1
    if os.path.isdir(doel):
        for root, _dirs, files in os.walk(doel, topdown=False):
            for f in files:
                p = os.path.normpath(os.path.join(root, f))
                if p not in gezien:
                    os.remove(p)
            if root != doel and not os.listdir(root):
                os.rmdir(root)
    return n


def exporteer(base, db_pad, doelmap, samenvatting_tekst=None):
    """Schrijft de export. Geeft {'map', 'tijd', 'aantal_boekingen', 'bestanden_gekopieerd'} terug."""
    doelmap = os.path.abspath(doelmap)
    os.makedirs(os.path.join(doelmap, 'data'), exist_ok=True)
    nu = dt.datetime.now()
    alles = {}
    with closing(sqlite3.connect(db_pad)) as c:
        tabellen = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        tabellen.sort(key=lambda t: (TABELLEN_VOLGORDE.index(t) if t in TABELLEN_VOLGORDE else 99, t))
        for t in tabellen:
            kol, rijen = _rijen(c, t)
            if t == 'boekingen' and 'bijlage' in kol:
                i = kol.index('bijlage')
                kol = kol + ['bijlage_in_export']
                rijen = [r + [('bestanden/' + r[i]) if r[i] else ''] for r in rijen]
            _schrijf_atomair(os.path.join(doelmap, 'data', t + '.csv'), _csv_tekst(kol, rijen))
            alles[t] = [dict(zip(kol, r)) for r in rijen]
        # consistente kopie van de database zelf (SQLite backup-API)
        kopie = os.path.join(doelmap, 'administratie_kopie.db')
        tmp = kopie + '.tmp'
        if os.path.exists(tmp):
            os.remove(tmp)
        with closing(sqlite3.connect(tmp)) as d:
            c.backup(d)
        os.replace(tmp, kopie)
    meta = {'geexporteerd_op': nu.isoformat(timespec='seconds'), 'tabellen': {t: len(r) for t, r in alles.items()}}
    _schrijf_atomair(os.path.join(doelmap, 'administratie.json'), json.dumps({'meta': meta, **alles}, ensure_ascii=False, indent=1, default=str))
    # facturen en btw-stukken (de ruwe bestanden) naast de data zetten
    gekopieerd = _spiegel(os.path.join(base, 'facturen'), os.path.join(doelmap, 'bestanden', 'facturen'))
    gekopieerd += _spiegel(os.path.join(base, 'btw'), os.path.join(doelmap, 'bestanden', 'btw'))
    # index: welke boeking hoort bij welk bestand
    idx_kol = ['boeking_id', 'datum', 'omschrijving', 'factuurnr', 'bedrag_incl_btw', 'bestand', 'bestand_aanwezig']
    idx = []
    for b in alles.get('boekingen', []):
        if b.get('bijlage'):
            idx.append([b['id'], b['datum'], b['omschrijving'], b['factuurnr'],
                        round((b['bedrag_excl'] or 0) + (b['btw_bedrag'] or 0), 2), 'bestanden/' + b['bijlage'],
                        'ja' if os.path.exists(os.path.join(doelmap, 'bestanden', b['bijlage'])) else 'NEE'])
    _schrijf_atomair(os.path.join(doelmap, 'data', 'bestanden_index.csv'), _csv_tekst(idx_kol, idx))
    if samenvatting_tekst:
        _schrijf_atomair(os.path.join(doelmap, 'SAMENVATTING.md'), samenvatting_tekst)
    _schrijf_atomair(os.path.join(doelmap, 'LEESMIJ.txt'),
                     "Export van de administratie Lenffect / Fluid Marketing\n"
                     "Laatst bijgewerkt: %s\n\n"
                     "SAMENVATTING.md          saldi, btw per kwartaal, openstaande facturen\n"
                     "data/*.csv               alle tabellen (puntkomma-gescheiden, opent direct in Excel)\n"
                     "administratie.json       dezelfde data in één bestand\n"
                     "administratie_kopie.db   volledige database (SQLite)\n"
                     "bestanden/               de gekoppelde facturen en btw-stukken\n"
                     "data/bestanden_index.csv koppeling boeking -> bestand\n\n"
                     "Dit is een kopie: wijzig hier niets, het wordt bij elke start/afsluiten overschreven.\n" % nu.strftime('%d-%m-%Y %H:%M'))
    return {'map': doelmap, 'tijd': nu.isoformat(timespec='seconds'), 'aantal_boekingen': len(alles.get('boekingen', [])),
            'bestanden_gekopieerd': gekopieerd}
