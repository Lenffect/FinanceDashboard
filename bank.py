# -*- coding: utf-8 -*-
"""Bankafschriften (ING + Wise, CSV) inlezen en afstemmen met de administratie."""
import csv, io, re, datetime as dt
from collections import defaultdict

ZAKELIJK_WOORDEN = ['google', 'freepik', 'magnific', 'artlist', 'gumroad', 'adobe', 'surfspot', 'mijn.host',
                    'mijnhost', 'embrosa', 'apple', 'amac', 'kvk', 'stamps', 'jpp', 'belastingdienst']


def num(s):
    s = (s or '').strip().replace(' ', '')
    if not s:
        return 0.0
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    return float(s)


def _dt(s):
    s = (s or '').strip()
    for f in ('%d-%m-%Y %H:%M:%S.%f', '%d-%m-%Y %H:%M:%S', '%d-%m-%Y'):
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    return dt.datetime(1970, 1, 1)


def _lees(tekst):
    tekst = tekst.lstrip('﻿')
    eerste = tekst.split('\n', 1)[0]
    delim = ';' if eerste.count(';') > eerste.count(',') else ','
    return list(csv.DictReader(io.StringIO(tekst), delimiter=delim))


def parse(naam, tekst):
    """Geeft {bron, regels, saldo, saldo_datum, waarschuwingen}. Regels: datum, bedrag (met teken), omschrijving, tegenpartij."""
    rijen = _lees(tekst)
    if not rijen:
        return {'bron': None, 'regels': [], 'saldo': None, 'saldo_datum': None, 'waarschuwingen': ['%s: geen regels gevonden (lege export?)' % naam]}
    k = set(rijen[0].keys())
    if 'TransferWise ID' in k:
        return _wise(naam, rijen)
    if ('Resulting balance' in k) or ('Saldo na mutatie' in k):
        return _ing(naam, rijen)
    raise ValueError('%s: onbekend bestandsformaat (verwacht een ING- of Wise-csv)' % naam)


def _ing(naam, rijen):
    def g(r, *keys):
        for key in keys:
            if key in r and r[key] is not None:
                return r[key]
        return ''
    regels = []
    for r in rijen:
        d = g(r, 'Date', 'Datum')
        if not re.match(r'^\d{8}$', d):
            continue
        af = g(r, 'Debit/credit', 'Af Bij').strip().lower() in ('debit', 'af')
        bedrag = num(g(r, 'Amount (EUR)', 'Bedrag (EUR)'))
        regels.append({'datum': '%s-%s-%s' % (d[:4], d[4:6], d[6:]), 'bedrag': round(-bedrag if af else bedrag, 2),
                       'tegenpartij': g(r, 'Name / Description', 'Naam / Omschrijving').strip(),
                       'omschrijving': g(r, 'Notifications', 'Mededelingen').strip(),
                       'saldo': num(g(r, 'Resulting balance', 'Saldo na mutatie')), 'fee': 0.0})
    if not regels:
        return {'bron': 'ing', 'regels': [], 'saldo': None, 'saldo_datum': None, 'waarschuwingen': []}
    nieuwste_eerst = regels[0]['datum'] >= regels[-1]['datum']
    laatste = regels[0] if nieuwste_eerst else regels[-1]
    return {'bron': 'ing', 'regels': regels, 'saldo': laatste['saldo'], 'saldo_datum': laatste['datum'], 'waarschuwingen': []}


def _wise(naam, rijen):
    waarsch, regels, eur = [], [], []
    vreemd = defaultdict(int)
    for r in rijen:
        if (r.get('Currency') or '').upper() != 'EUR':
            vreemd[r.get('Currency')] += 1
            continue
        eur.append(r)
    for c, n in vreemd.items():
        waarsch.append('%s: %d regels in %s overgeslagen (alleen EUR wordt afgestemd)' % (naam, n, c))
    for r in eur:
        omschr = r.get('Description') or ''
        if omschr.startswith('Wise Charges for:'):
            continue  # kosten zitten al in de bijbehorende regel (kolom Total fees)
        bedrag, fee = num(r.get('Amount')), num(r.get('Total fees'))
        d, m, y = (r.get('Date') or '').split('-')
        regels.append({'datum': '%s-%s-%s' % (y, m, d), 'bedrag': round(bedrag - fee, 2), 'fee': round(fee, 2),
                       'tegenpartij': (r.get('Merchant') or '').strip(),
                       'omschrijving': omschr.strip(), 'saldo': num(r.get('Running Balance')), 'tijd': _dt(r.get('Date Time')),
                       'netto_bank': bedrag})
    # saldo: laatste regel in de tijd (Wise levert nieuwste eerst; bij gelijke tijd telt de bovenste)
    saldo = datum = None
    if eur:
        tijden = [_dt(r.get('Date Time')) for r in eur]
        mx = max(tijden)
        idx = [i for i, t in enumerate(tijden) if t == mx]
        nieuwste_eerst = tijden[0] >= tijden[-1]
        i = idx[0] if nieuwste_eerst else idx[-1]
        saldo = num(eur[i].get('Running Balance'))
        datum = mx.strftime('%Y-%m-%d')
    return {'bron': 'wise', 'regels': regels, 'saldo': saldo, 'saldo_datum': datum, 'waarschuwingen': waarsch}


def boek_cash(b):
    tot = (b['bedrag_excl'] or 0) + (b['btw_bedrag'] or 0)
    s = b['soort']
    if s == 'inkomst':
        return tot
    if s == 'kosten':
        return -tot
    if s in ('storting', 'overige_baten', 'btw_teruggave'):
        return b['bedrag_excl']
    if s in ('btw_betaling', 'overboeking', 'prive_uitgave'):
        return -b['bedrag_excl']
    return 0.0


def _dagen(a, b):
    return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)


def afstemmen(bron, regels, boekingen, venster=35):
    """Koppelt bankregels aan betaalde boekingen. Geeft dict met gekoppeld, terugboekingen, nieuw, privé, ontbreekt."""
    regels = sorted(regels, key=lambda r: r['datum'])
    if not regels:
        return {'gekoppeld': 0, 'terugboekingen': 0, 'nieuw': [], 'prive': [], 'zakelijk_onbekend': [], 'ontbreekt': [], 'van': None, 'tot': None}
    van, tot = regels[0]['datum'], regels[-1]['datum']
    kand = []
    for b in boekingen:
        if b['rekening'] != bron or not b['betaald']:
            continue
        if b['soort'] == 'prive_uitgave' and bron == 'wise':
            continue  # privé-uitgaven worden per maand vergeleken
        if b['soort'] == 'kosten' and (b.get('categorie') or '') == 'Bankkosten':
            continue
        bd = b['betaaldatum'] or b['datum']
        if _dagen(bd, van) > 400 or bd < _dagen_min(van, venster) or bd > _dagen_plus(tot, venster):
            continue
        kand.append(dict(b, _cash=round(boek_cash(b), 2), _bd=bd))
    gebruikt = set()
    open_regels = []
    gekoppeld = 0
    for r in regels:
        beste, bd = None, 10 ** 6
        for b in kand:
            if b['id'] in gebruikt:
                continue
            ok = abs(b['_cash'] - r['bedrag']) <= 0.02 or (r.get('fee') and abs(b['_cash'] - (r['bedrag'] + r['fee'] * (1 if r['bedrag'] < 0 else -1))) <= 0.02)
            if not ok:
                continue
            dd = _dagen(b['_bd'], r['datum'])
            if dd <= venster and dd < bd:
                beste, bd = b, dd
        if beste:
            gebruikt.add(beste['id'])
            gekoppeld += 1
        else:
            open_regels.append(r)
    # één boeking = twee bankregels van dezelfde partij (bijv. opwaardering + aanvulling)
    for b in kand:
        if b['id'] in gebruikt:
            continue
        gevonden = False
        for i in range(len(open_regels)):
            for j in range(i + 1, len(open_regels)):
                x, y = open_regels[i], open_regels[j]
                if x['tegenpartij'] and x['tegenpartij'] == y['tegenpartij'] and abs(x['bedrag'] + y['bedrag'] - b['_cash']) <= 0.02 \
                        and _dagen(x['datum'], b['_bd']) <= venster and _dagen(y['datum'], b['_bd']) <= venster:
                    gebruikt.add(b['id'])
                    gekoppeld += 2
                    open_regels = [r for k, r in enumerate(open_regels) if k not in (i, j)]
                    gevonden = True
                    break
            if gevonden:
                break
    # terugboekingen: gelijk bedrag met tegengesteld teken binnen 14 dagen
    terug = 0
    over = []
    gebruikt_r = set()
    for i, r in enumerate(open_regels):
        if i in gebruikt_r:
            continue
        for j in range(i + 1, len(open_regels)):
            if j in gebruikt_r:
                continue
            s = open_regels[j]
            if abs(r['bedrag'] + s['bedrag']) <= 0.02 and _dagen(r['datum'], s['datum']) <= 14:
                gebruikt_r.update((i, j))
                terug += 2
                break
    over = [r for i, r in enumerate(open_regels) if i not in gebruikt_r]
    nieuw, prive, zak = [], [], []
    if bron == 'ing':
        nieuw = over
    else:
        for r in over:
            t = (r['tegenpartij'] + ' ' + r['omschrijving']).lower()
            if r['bedrag'] > 0 and 'topped up' in t:
                nieuw.append(r)
            elif any(w in t for w in ZAKELIJK_WOORDEN):
                zak.append(r)
            else:
                prive.append(r)
    ontbreekt = [{'id': b['id'], 'datum': b['_bd'], 'omschrijving': b['omschrijving'], 'bedrag': b['_cash']}
                 for b in kand if b['id'] not in gebruikt and van <= b['_bd'] <= tot]
    return {'gekoppeld': gekoppeld, 'terugboekingen': terug, 'nieuw': nieuw, 'prive': prive, 'zakelijk_onbekend': zak,
            'ontbreekt': ontbreekt, 'van': van, 'tot': tot}


def _dagen_min(d, n):
    return (dt.date.fromisoformat(d) - dt.timedelta(days=n)).isoformat()


def _dagen_plus(d, n):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def prive_per_maand(prive_regels, boekingen):
    """Vergelijkt onbekende Wise-regels (= privé) per maand met de privé-boekingen in de administratie."""
    bank = defaultdict(float)
    laatste = {}
    for r in prive_regels:
        m = r['datum'][:7]
        bank[m] += -r['bedrag']
        laatste[m] = max(laatste.get(m, ''), r['datum'])
    admin = defaultdict(float)
    for b in boekingen:
        if b['rekening'] == 'wise' and b['soort'] == 'prive_uitgave' and b['betaald']:
            admin[(b['betaaldatum'] or b['datum'])[:7]] += b['bedrag_excl']
    uit = []
    for m in sorted(set(bank) | set(admin)):
        v = round(admin.get(m, 0.0) - 0, 2)
        verschil = round(bank.get(m, 0.0) - v, 2)
        uit.append({'maand': m, 'bank': round(bank.get(m, 0.0), 2), 'administratie': v, 'verschil': verschil,
                    'datum': laatste.get(m) or m + '-28'})
    return uit
