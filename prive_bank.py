# -*- coding: utf-8 -*-
"""Privé-bankafschriften (ING lopende rekening en spaarrekening, CSV) inlezen. Alles blijft lokaal."""
import csv, hashlib, io, re, datetime as dt

# Een overboeking van/naar jezelf of tussen eigen rekeningen is geen inkomen of uitgave
INTERN_NAAM =re.compile(r'(hr|dhr\.?|mw)?\s*l\.?\s*j\.?\s*t\.?\s*schumacher|lennar jan tilson|lennar schumacher', re.I)
INTERN_OVERIG = re.compile(r'oranje spaarrekening|^wise\b|wise payments|wise europe', re.I)

# (zoekterm in tegenpartij/omschrijving, categorie); de eerste treffer wint
STANDAARD_REGELS = [
    ('jumbo', 'Boodschappen'), ('albert heijn', 'Boodschappen'), ('ah to go', 'Boodschappen'), ('lidl', 'Boodschappen'),
    ('odido', 'Abonnementen'), ('ziggo', 'Abonnementen'), ('spotify', 'Abonnementen'), ('netflix', 'Abonnementen'),
    ('fitness', 'Vrije tijd'), ('vve ', 'Wonen'), ('vitens', 'Wonen'), ('eneco', 'Wonen'), ('vattenfall', 'Wonen'),
    ('zorgverzekering', 'Verzekeringen'), ('schadeverzekering', 'Verzekeringen'), ('verzekering', 'Verzekeringen'),
    ('ov-chipkaart', 'Vervoer'), ('ns groep', 'Vervoer'), ('ns reizigers', 'Vervoer'), ('shell', 'Vervoer'), ('uber *', 'Vervoer'), ('lime*', 'Vervoer'),
    ('transavia', 'Reizen'), ('air india', 'Reizen'), ('booking', 'Reizen'), ('getyourguide', 'Reizen'), ('airbnb', 'Reizen'),
    ('kosten basispakket', 'Bankkosten'), ('infomedics', 'Gezondheid'), ('apotheek', 'Gezondheid'), ('ljekarna', 'Gezondheid'),
    ('uniqlo', 'Kleding'), ('zalando', 'Kleding'), ('vinted', 'Kleding'),
    ('salaris', 'Salaris'),
]


def _bedrag(t):
    t = (t or '').strip().replace('.', '').replace(',', '.')
    return round(float(t), 2)


def _datum(t):
    t = (t or '').strip()
    if re.match(r'^\d{8}$', t):
        return '%s-%s-%s' % (t[:4], t[4:6], t[6:])
    if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
        return t
    raise ValueError('Onbekende datum: %r' % t)


def rekening_label(nr):
    nr = (nr or '').strip()
    soort = 'Lopend' if nr.upper().startswith('NL') else 'Spaar'
    return '%s ·%s' % (soort, re.sub(r'\D', '', nr)[-4:])


def categoriseer(tekst, regels):
    t = tekst.lower()
    for zoek, cat in regels:
        if zoek.lower() in t:
            return cat
    return ''


def lees(tekst, eigen_rekeningen=(), regels=()):
    """Geeft (lijst mutaties, soort bestand). Oudste regel eerst, zodat de laatste id ook de nieuwste is."""
    tekst = tekst.lstrip('﻿')
    rd = csv.DictReader(io.StringIO(tekst), delimiter=';')
    rijen = list(rd)
    kol = rd.fieldnames or []
    if 'Name / Description' in kol:
        soort = 'lopend'
    elif 'Account name' in kol:
        soort = 'spaar'
    else:
        raise ValueError('Dit lijkt geen ING-csv (lopende of spaarrekening).')
    eigen = set(eigen_rekeningen) | {r['Account'] for r in rijen}
    alle_regels = list(regels) + STANDAARD_REGELS
    out, gezien = [], {}
    for r in reversed(rijen):
        datum = _datum(r['Date'])
        credit = r['Debit/credit'].strip().lower() == 'credit'
        if soort == 'lopend':
            bedrag = _bedrag(r['Amount (EUR)'])
            naam = re.sub(r'\s+', ' ', r['Name / Description']).strip()
            notitie = r.get('Notifications') or ''
            m = re.search(r'Description:\s*(.*?)(?:\s+IBAN:|\s+Value date:|\s+Reference:|$)', notitie)
            oms = naam + ((' · ' + m.group(1).strip()[:90]) if m and m.group(1).strip() else '')
            tegen = (r.get('Counterparty') or '').strip()
            tp = naam
            intern = tegen in eigen or bool(INTERN_NAAM.search(naam)) or bool(INTERN_OVERIG.search(naam))
            saldo = r.get('Resulting balance') or ''
        else:
            bedrag = _bedrag(r['Amount'])
            oms = re.sub(r'\s+', ' ', r['Description']).strip()
            tegen = (r.get('Counterparty') or '').strip()
            tp = oms
            intern = bool(re.search(r'transfer (to|from)', oms, re.I)) or tegen in eigen
            saldo = r.get('Resulting balance') or ''
        rek = rekening_label(r['Account'])
        sleutel = '|'.join([rek, datum, 'c' if credit else 'd', '%.2f' % bedrag, tp, saldo])
        gezien[sleutel] = gezien.get(sleutel, 0) + 1
        ext = hashlib.sha1(('%s#%d' % (sleutel, gezien[sleutel])).encode()).hexdigest()
        if soort == 'spaar' and re.search(r'interest|rente', oms, re.I):
            cat = 'Rente'
        else:
            cat = '' if intern else categoriseer(tp + ' ' + oms, alle_regels)
        if credit and cat == 'Salaris':
            pass
        elif cat == 'Salaris' and not credit:
            cat = ''
        out.append({'datum': datum, 'soort': 'inkomen' if credit else 'uitgave', 'bedrag': bedrag, 'categorie': cat,
                    'omschrijving': oms[:160], 'rekening': rek, 'ext_id': ext, 'intern': 1 if intern else 0,
                    'tegenpartij': tp[:80], 'saldo_na': _bedrag(saldo) if saldo.strip() else None})
    return out, soort
