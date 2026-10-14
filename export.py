# -*- coding: utf-8 -*-
"""Platte export van de hele administratie: csv + json per tabel, samenvatting, database-kopie en de gekoppelde facturen.

Wordt bij elke start en bij het afsluiten van de app uitgevoerd, en op verzoek (Instellingen → "Nu exporteren").
De exportmap is zelfstandig leesbaar zonder de app (Excel/Numbers, een tekstverwerker of een andere tool).
"""
import csv, json, os, re, shutil, sqlite3, zipfile, datetime as dt
from xml.sax.saxutils import escape
from contextlib import closing

PRIVE_INSTELLINGEN = ('prive_',)
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


# ---------------------------------------------------------------- xlsx (alleen standaardbibliotheek, geen extra installatie nodig)
def _kolletter(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_DATUM = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _serial(d):
    y, m, dd = map(int, d.split('-'))
    return (dt.date(y, m, dd) - dt.date(1899, 12, 30)).days


def _blad_xml(kol, rijen, bedrag_kolommen):
    ws = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    breedtes = [max(10, min(60, len(str(k)) + 2)) for k in kol]
    for r in rijen:
        for i, v in enumerate(r):
            if v is not None and i < len(breedtes):
                breedtes[i] = max(breedtes[i], min(60, len(str(v)) + 2))
    ws.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    ws.append('<cols>' + ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, b) for i, b in enumerate(breedtes)) + '</cols>')
    ws.append('<sheetData>')
    ws.append('<row r="1">' + ''.join('<c r="%s1" s="1" t="inlineStr"><is><t>%s</t></is></c>' % (_kolletter(i), escape(str(k))) for i, k in enumerate(kol)) + '</row>')
    for n, r in enumerate(rijen, start=2):
        cel = []
        for i, v in enumerate(r):
            ref = '%s%d' % (_kolletter(i), n)
            if v is None or v == '':
                continue
            if isinstance(v, bool):
                cel.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, 'ja' if v else 'nee'))
            elif isinstance(v, (int, float)):
                st = ' s="3"' if (kol[i] in bedrag_kolommen) else ''
                cel.append('<c r="%s"%s><v>%s</v></c>' % (ref, st, repr(float(v)) if isinstance(v, float) else v))
            elif isinstance(v, str) and _DATUM.match(v):
                try:
                    cel.append('<c r="%s" s="2"><v>%d</v></c>' % (ref, _serial(v)))
                except ValueError:
                    cel.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(v)))
            else:
                cel.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, escape(str(v))))
        ws.append('<row r="%d">%s</row>' % (n, ''.join(cel)))
    ws.append('</sheetData>')
    if kol:
        ws.append('<autoFilter ref="A1:%s%d"/>' % (_kolletter(len(kol) - 1), max(1, len(rijen) + 1)))
    ws.append('</worksheet>')
    return ''.join(ws)


def schrijf_xlsx(pad, bladen):
    """bladen: lijst van (naam, kolommen, rijen). Schrijft een echt .xlsx-bestand (opent in Excel, Numbers, LibreOffice)."""
    bedrag_kol = {'bedrag_excl', 'btw_bedrag', 'bedrag_incl_btw', 'Omzet (excl.)', 'Kosten (mijn deel)', 'Winst', 'Btw'}
    namen = []
    for naam, _k, _r in bladen:
        n = re.sub(r'[\[\]:*?/\\]', '', naam)[:31] or 'Blad'
        namen.append(n)
    tmp = pad + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(bladen))) + '</Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n, {'"': '&quot;'}), i + 1, i + 1) for i, n in enumerate(namen)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(bladen))) + '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' % (len(bladen) + 1))
        z.writestr('xl/styles.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="dd\\-mm\\-yyyy"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF4"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, (_n, kol, rijen) in enumerate(bladen):
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), _blad_xml(kol, rijen, bedrag_kol))
    os.replace(tmp, pad)


def exporteer(base, db_pad, doelmap, samenvatting_tekst=None, dash=None):
    """Schrijft de export. Geeft {'map', 'tijd', 'aantal_boekingen', 'bestanden_gekopieerd'} terug."""
    doelmap = os.path.abspath(doelmap)
    os.makedirs(os.path.join(doelmap, 'data'), exist_ok=True)
    nu = dt.datetime.now()
    alles = {}
    bladen = []
    with closing(sqlite3.connect(db_pad)) as c:
        tabellen = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        tabellen.sort(key=lambda t: (TABELLEN_VOLGORDE.index(t) if t in TABELLEN_VOLGORDE else 99, t))
        for t in tabellen:
            if t.startswith('prive_'):
                continue  # privé-gegevens niet als leesbare csv/json/xlsx in de export; alleen in de volledige database-kopie (door Lennar goedgekeurd)
            kol, rijen = _rijen(c, t)
            if t == 'instellingen':
                i = kol.index('sleutel')
                rijen = [r for r in rijen if not str(r[i]).startswith(PRIVE_INSTELLINGEN)]
            if t == 'boekingen' and 'bijlage' in kol:
                i = kol.index('bijlage')
                kol = kol + ['bijlage_in_export']
                rijen = [r + [('bestanden/' + r[i]) if r[i] else ''] for r in rijen]
            _schrijf_atomair(os.path.join(doelmap, 'data', t + '.csv'), _csv_tekst(kol, rijen))
            alles[t] = [dict(zip(kol, r)) for r in rijen]
            bladen.append((t.capitalize(), kol, rijen))
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
    sam = [['Gegeven', 'Waarde']]
    if dash:
        sam = [['Gegeven', 'Waarde'], ['Bijgewerkt', nu.strftime('%d-%m-%Y %H:%M')], ['ING zakelijk (berekend)', dash['ing_saldo']],
               ['ING zakelijk (laatst gecontroleerd met bank)', dash['ing_werkelijk']], ['Datum controle ING', dash['ing_datum']],
               ['Wise-kaart (berekend)', dash['wise_saldo']], ['Btw-reserve', dash['btw_reserve']],
               ['Schuld aan Jelmer', dash['schuld_jelmer']], ['Vrij te besteden', dash['vrij']]]
    kw_kol = ['Kwartaal', 'Omzet (excl.)', 'Kosten (mijn deel)', 'Winst', 'Btw', 'Aangifte vóór']
    kw_rijen = [['Q%d %d' % (k['kwartaal'], dash['jaar']), k['omzet'], k['kosten'], k['winst'], k['btw_saldo'], k['deadline']] for k in dash['kwartalen']] if dash else []
    xl_bladen = []
    if dash:
        xl_bladen.append(('Samenvatting', sam[0], sam[1:]))
        xl_bladen.append(('Btw per kwartaal', kw_kol, kw_rijen))
    xl_bladen += bladen
    xl_bladen.append(('Bestanden', idx_kol, idx))
    schrijf_xlsx(os.path.join(doelmap, 'administratie.xlsx'), xl_bladen)
    if samenvatting_tekst:
        _schrijf_atomair(os.path.join(doelmap, 'SAMENVATTING.md'), samenvatting_tekst)
    _schrijf_atomair(os.path.join(doelmap, 'LEESMIJ.txt'),
                     "Export van de administratie Lenffect / Fluid Marketing\n"
                     "Laatst bijgewerkt: %s\n\n"
                     "SAMENVATTING.md          saldi, btw per kwartaal, openstaande facturen\n"
                     "data/*.csv               alle tabellen (puntkomma-gescheiden, opent direct in Excel)\n"
                     "administratie.xlsx       alles in één Excel-bestand (een tabblad per tabel + samenvatting)\n"
                     "administratie.json       dezelfde data in één bestand\n"
                     "administratie_kopie.db   volledige database (SQLite)\n"
                     "bestanden/               de gekoppelde facturen en btw-stukken\n"
                     "data/bestanden_index.csv koppeling boeking -> bestand\n\n"
                     "Dit is een kopie: wijzig hier niets, het wordt bij elke start/afsluiten overschreven.\n" % nu.strftime('%d-%m-%Y %H:%M'))
    return {'map': doelmap, 'tijd': nu.isoformat(timespec='seconds'), 'aantal_boekingen': len(alles.get('boekingen', [])),
            'bestanden_gekopieerd': gekopieerd}
