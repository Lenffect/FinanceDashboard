#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lenffect administratie - lokale app (geen installatie nodig, alleen Python 3).
Start met Start.command (dubbelklik) of:  python3 server.py
"""
import sqlite3, json, os, re, shutil, sys, threading, webbrowser, html, calendar, datetime as dt
from contextlib import closing
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import mimetypes

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
DB = os.path.join(DATA, 'administratie.db')
PORT = int(os.environ.get('PORT', 8765))
INBOX = os.path.join(BASE, 'inbox')
ARCHIEF = os.path.join(BASE, 'facturen')
NC = "COALESCE(status,'definitief')<>'concept'"  # concepten tellen niet mee
TOEGESTAAN = ('.pdf', '.png', '.jpg', '.jpeg', '.webp', '.heic')

SCHEMA = """
CREATE TABLE IF NOT EXISTS instellingen(sleutel TEXT PRIMARY KEY, waarde TEXT);
CREATE TABLE IF NOT EXISTS relaties(
  id INTEGER PRIMARY KEY AUTOINCREMENT, naam TEXT NOT NULL, soort TEXT DEFAULT 'klant',
  adres TEXT DEFAULT '', postcode_plaats TEXT DEFAULT '', kvk TEXT DEFAULT '', btw_nr TEXT DEFAULT '',
  email TEXT DEFAULT '', std_btw_soort TEXT DEFAULT '', notitie TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS boekingen(
  id INTEGER PRIMARY KEY AUTOINCREMENT, soort TEXT NOT NULL, datum TEXT NOT NULL,
  omschrijving TEXT DEFAULT '', relatie TEXT DEFAULT '', factuurnr TEXT DEFAULT '',
  bedrag_excl REAL DEFAULT 0, btw_bedrag REAL DEFAULT 0, btw_soort TEXT DEFAULT 'geen',
  toewijzing TEXT DEFAULT 'ik', pct_ik REAL DEFAULT 100,
  betaald INTEGER DEFAULT 0, betaaldatum TEXT, rekening TEXT DEFAULT 'ing',
  categorie TEXT DEFAULT '', notitie TEXT DEFAULT '', controleren INTEGER DEFAULT 0,
  factuur_id INTEGER, abonnement_id INTEGER,
  status TEXT DEFAULT 'definitief', bijlage TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_boek_datum ON boekingen(datum);
CREATE TABLE IF NOT EXISTS abonnementen(
  id INTEGER PRIMARY KEY AUTOINCREMENT, naam TEXT NOT NULL, omschrijving TEXT DEFAULT '',
  bedrag_excl REAL DEFAULT 0, btw_bedrag REAL DEFAULT 0, btw_soort TEXT DEFAULT 'geen',
  toewijzing TEXT DEFAULT 'ik', pct_ik REAL DEFAULT 100, rekening TEXT DEFAULT 'wise',
  categorie TEXT DEFAULT '', interval TEXT DEFAULT 'maandelijks', startdatum TEXT, laatste_datum TEXT,
  actief INTEGER DEFAULT 1, variabel INTEGER DEFAULT 0, notitie TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS facturen(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nummer TEXT, datum TEXT, relatie_id INTEGER,
  klant TEXT DEFAULT '{}', onder TEXT DEFAULT 'fluid', regels TEXT DEFAULT '[]',
  btw_pct REAL DEFAULT 21, termijn INTEGER DEFAULT 14, betaallink TEXT DEFAULT '', notitie TEXT DEFAULT '',
  excl REAL DEFAULT 0, btw REAL DEFAULT 0);
"""

DEFAULTS = {
    'bedrijfsnaam': 'Lenffect', 'naam': 'Lennar Schumacher', 'adres': '',
    'postcode_plaats': '', 'iban': '',
    'btw_nr': '', 'kvk': '', 'email': '',
    'telefoon': '', 'handelsnaam': 'Fluid Marketing',
    'partner_naam': 'JPPW Consultancy', 'partner_contact': '',
    'voetregel_fluid': 'Fluid Marketing is een samenwerking tussen Lenffect & JPP Consultancy\nFluid Marketing is een handelsnaam van Lenffect',
    'betaaltermijn': '14', 'btw_stelsel': 'factuurdatum', 'verlegd_tarief': '21', 'wise_startsaldo': '-3.12',
    'ing_saldo_werkelijk': '', 'ing_saldo_datum': '', 'btw_betaald_t_m': '2026-Q2', 'export_map': '',
}

BOEK_FIELDS = ['soort', 'datum', 'omschrijving', 'relatie', 'factuurnr', 'bedrag_excl', 'btw_bedrag', 'btw_soort',
               'toewijzing', 'pct_ik', 'betaald', 'betaaldatum', 'rekening', 'categorie', 'notitie',
               'controleren', 'factuur_id', 'abonnement_id', 'status', 'bijlage']
ABO_FIELDS = ['naam', 'omschrijving', 'bedrag_excl', 'btw_bedrag', 'btw_soort', 'toewijzing', 'pct_ik', 'rekening',
              'categorie', 'interval', 'startdatum', 'laatste_datum', 'actief', 'variabel', 'notitie']
REL_FIELDS = ['naam', 'soort', 'adres', 'postcode_plaats', 'kvk', 'btw_nr', 'email', 'std_btw_soort', 'notitie']
PRIVE_SOORTEN = ('prive_uitgave', 'storting', 'btw_betaling', 'overboeking', 'overige_baten', 'btw_teruggave')
JPPW = 'JPPW-factuur'
AFTREKBAAR_IN_BRUTO = ('nl21', 'nl9', 'buitenlandse_btw')


def r2(x):
    x = float(x or 0)
    return round(x + (1e-9 if x >= 0 else -1e-9), 2)


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def q(sql, args=(), one=False):
    with closing(db()) as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    return (rows[0] if rows else None) if one else rows


def init_db():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(ARCHIEF, exist_ok=True)
    fresh = not os.path.exists(DB)
    if not fresh:  # back-up bij elke start
        bdir = os.path.join(DATA, 'backup')
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(DB, os.path.join(bdir, 'administratie-%s.db' % dt.datetime.now().strftime('%Y%m%d-%H%M%S')))
        olds = sorted(os.listdir(bdir))
        for f in olds[:-30]:
            os.remove(os.path.join(bdir, f))
    with closing(db()) as c:
        c.executescript(SCHEMA)
        kolommen = [r[1] for r in c.execute('PRAGMA table_info(boekingen)')]
        if 'status' not in kolommen:
            c.execute("ALTER TABLE boekingen ADD COLUMN status TEXT DEFAULT 'definitief'")
        if 'bijlage' not in kolommen:
            c.execute("ALTER TABLE boekingen ADD COLUMN bijlage TEXT DEFAULT ''")
        for k, v in DEFAULTS.items():
            c.execute('INSERT OR IGNORE INTO instellingen(sleutel,waarde) VALUES(?,?)', (k, v))
        c.commit()


def settings():
    s = dict(DEFAULTS)
    s.update({r['sleutel']: r['waarde'] for r in q('SELECT * FROM instellingen')})
    return s


# ---------------------------------------------------------------- boekingen
def clean_boeking(d):
    today = dt.date.today().isoformat()
    b = {
        'soort': d.get('soort') or 'kosten',
        'datum': d.get('datum') or today,
        'omschrijving': (d.get('omschrijving') or '').strip(),
        'relatie': (d.get('relatie') or '').strip(),
        'factuurnr': str(d.get('factuurnr') or '').strip(),
        'bedrag_excl': r2(d.get('bedrag_excl')),
        'btw_bedrag': r2(d.get('btw_bedrag')),
        'btw_soort': d.get('btw_soort') or 'geen',
        'toewijzing': d.get('toewijzing') or 'ik',
        'pct_ik': float(d['pct_ik']) if d.get('pct_ik') not in (None, '') else 100.0,
        'betaald': 1 if d.get('betaald') else 0,
        'betaaldatum': d.get('betaaldatum') or None,
        'rekening': d.get('rekening') or 'ing',
        'categorie': (d.get('categorie') or '').strip(),
        'notitie': d.get('notitie') or '',
        'controleren': 1 if d.get('controleren') else 0,
        'factuur_id': d.get('factuur_id') or None,
        'abonnement_id': d.get('abonnement_id') or None,
        'status': 'concept' if d.get('status') == 'concept' else 'definitief',
        'bijlage': d.get('bijlage') or '',
    }
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', b['datum']):
        raise ValueError('Datum moet het formaat JJJJ-MM-DD hebben')
    if b['toewijzing'] == 'ik':
        b['pct_ik'] = 100.0
    elif b['toewijzing'] == 'jelmer':
        b['pct_ik'] = 0.0
    if b['soort'] in PRIVE_SOORTEN:
        b.update(btw_bedrag=0.0, btw_soort='geen', toewijzing='ik', pct_ik=100.0, betaald=1)
    if b['betaald'] and not b['betaaldatum']:
        b['betaaldatum'] = b['datum']
    if not b['betaald']:
        b['betaaldatum'] = None
    return b


def insert(c, table, fields, d):
    cols = list(fields)
    cur = c.execute('INSERT INTO %s(%s) VALUES(%s)' % (table, ','.join(cols), ','.join('?' * len(cols))),
                    [d.get(k) for k in cols])
    return cur.lastrowid


def update(c, table, fields, d, id_):
    c.execute('UPDATE %s SET %s WHERE id=?' % (table, ','.join(k + '=?' for k in fields)),
              [d.get(k) for k in fields] + [id_])


def list_boekingen(p):
    sql, args = 'SELECT * FROM boekingen WHERE 1=1', []
    if p.get('jaar'):
        s, e = p['jaar'] + '-01-01', p['jaar'] + '-12-31'
        if p.get('kwartaal'):
            s, e = kwartaal_range(int(p['jaar']), int(p['kwartaal']))
        sql += ' AND datum BETWEEN ? AND ?'
        args += [s, e]
    if p.get('soort'):
        sql += ' AND soort=?'
        args.append(p['soort'])
    if p.get('toewijzing'):
        sql += ' AND toewijzing=?'
        args.append(p['toewijzing'])
    sql += ' ORDER BY datum DESC, id DESC'
    return q(sql, args)


# ---------------------------------------------------------------- periodes
def kwartaal_range(jaar, k):
    sm = (k - 1) * 3 + 1
    em = sm + 2
    start = dt.date(jaar, sm, 1)
    end = dt.date(jaar, em, calendar.monthrange(jaar, em)[1])
    return start.isoformat(), end.isoformat()


def aangifte_deadline(jaar, k):
    em = k * 3 + 1  # maand na het kwartaal
    j = jaar + (1 if em > 12 else 0)
    em = em - 12 if em > 12 else em
    return dt.date(j, em, calendar.monthrange(j, em)[1]).isoformat()


def eff_datum(b, stelsel):
    if stelsel == 'betaaldatum':
        return b['betaaldatum'] if b['betaald'] else None
    return b['datum']


# ---------------------------------------------------------------- btw
RUBRIEKEN = [
    ('1a', 'Leveringen/diensten belast met 21%'),
    ('1b', 'Leveringen/diensten belast met 9%'),
    ('1e', 'Leveringen/diensten belast met 0% of niet bij u belast'),
    ('4a', 'Diensten uit landen buiten de EU (btw verlegd naar u)'),
    ('4b', 'Diensten uit landen binnen de EU (btw verlegd naar u)'),
    ('5b', 'Voorbelasting'),
]


def btw_calc(jaar, k):
    s, e = kwartaal_range(jaar, k)
    cfg = settings()
    stelsel = cfg['btw_stelsel']
    tarief = float(cfg['verlegd_tarief']) / 100
    rub = {key: {'rubriek': key, 'omschrijving': om, 'grondslag': 0.0, 'btw': 0.0, 'regels': []} for key, om in RUBRIEKEN}
    waarschuwingen = []
    rows = q("SELECT * FROM boekingen WHERE soort IN ('inkomst','kosten') AND " + NC + " ORDER BY datum, id")
    n_concept = q("SELECT COUNT(*) n FROM boekingen WHERE status='concept' AND soort IN ('inkomst','kosten') AND datum BETWEEN ? AND ?", (s, e), one=True)['n']
    if n_concept:
        waarschuwingen.append({'id': None, 'tekst': '%d concept-boeking(en) uit de Inbox in dit kwartaal wachten nog op je goedkeuring en tellen NIET mee.' % n_concept})

    def add(key, b, grondslag, btw, extra=''):
        r = rub[key]
        r['grondslag'] += grondslag
        r['btw'] += btw
        r['regels'].append({'id': b['id'], 'datum': b['datum'], 'omschrijving': b['omschrijving'] + extra,
                            'relatie': b['relatie'], 'grondslag': r2(grondslag), 'btw': r2(btw)})

    for b in rows:
        d = eff_datum(b, stelsel)
        if not d or not (s <= d <= e):
            continue
        if b['controleren']:
            waarschuwingen.append({'id': b['id'], 'tekst': 'Gemarkeerd als "controleren": %s (%s)' % (b['omschrijving'], b['datum'])})
        if b['soort'] == 'inkomst':
            key = {'nl21': '1a', 'nl9': '1b'}.get(b['btw_soort'], '1e')
            add(key, b, b['bedrag_excl'], b['btw_bedrag'] if key in ('1a', '1b') else 0)
            if not b['betaald']:
                waarschuwingen.append({'id': b['id'], 'tekst': 'Factuur %s (%s) is nog niet betaald, maar valt wel in dit kwartaal (factuurstelsel).' % (b['factuurnr'] or b['omschrijving'], b['relatie'])})
        else:
            f = b['pct_ik'] / 100.0
            if b['btw_soort'] in ('nl21', 'nl9'):
                add('5b', b, b['bedrag_excl'] * f, b['btw_bedrag'] * f,
                    '' if f == 1 else ' (%g%% van btw)' % b['pct_ik'])
            elif b['btw_soort'] in ('eu_verlegd', 'buiten_eu_verlegd'):
                key = '4b' if b['btw_soort'] == 'eu_verlegd' else '4a'
                v = b['bedrag_excl'] * f * tarief
                add(key, b, b['bedrag_excl'] * f, v)
                add('5b', b, 0.0, v, ' (verlegde btw)')
    for key in rub:
        rub[key]['grondslag'] = r2(rub[key]['grondslag'])
        rub[key]['btw'] = r2(rub[key]['btw'])
        rub[key]['grondslag_hele_euros'] = int(rub[key]['grondslag'])
    verschuldigd = r2(sum(rub[x]['btw'] for x in ('1a', '1b', '4a', '4b')))
    voorbelasting = rub['5b']['btw']
    return {'jaar': jaar, 'kwartaal': k, 'van': s, 'tot': e, 'deadline': aangifte_deadline(jaar, k),
            'stelsel': stelsel, 'rubrieken': [rub[key] for key, _ in RUBRIEKEN],
            'verschuldigd_5a': verschuldigd, 'voorbelasting_5b': voorbelasting,
            'saldo_5g': r2(verschuldigd - voorbelasting), 'waarschuwingen': waarschuwingen}


# ---------------------------------------------------------------- dashboard & verrekening
def kosten_resultaat(b):
    f = b['pct_ik'] / 100.0
    extra = b['btw_bedrag'] if b['btw_soort'] in ('buitenlandse_btw', 'nl0', 'geen') else 0
    return (b['bedrag_excl'] + extra) * f


def wise_saldo(tot=None):
    cfg = settings()
    rows = q("SELECT * FROM boekingen WHERE (rekening='wise' OR soort='overboeking') AND " + NC +
             (" AND COALESCE(betaaldatum,datum)<='%s'" % tot if tot else ''))
    saldo = float(cfg['wise_startsaldo'])
    for b in rows:
        if b['soort'] in ('storting', 'overboeking'):
            saldo += b['bedrag_excl']
        elif b['soort'] == 'kosten':
            saldo -= b['bedrag_excl'] + b['btw_bedrag']
        elif b['soort'] == 'prive_uitgave':
            saldo -= b['bedrag_excl']
    return r2(saldo)


def ing_saldo(tot=None):
    saldo = 0.0
    for b in q("SELECT * FROM boekingen WHERE rekening='ing' AND betaald=1 AND " + NC +
               (" AND COALESCE(betaaldatum,datum)<='%s'" % tot if tot else '')):
        if b['soort'] == 'inkomst':
            saldo += b['bedrag_excl'] + b['btw_bedrag']
        elif b['soort'] == 'kosten':
            saldo -= b['bedrag_excl'] + b['btw_bedrag']
        elif b['soort'] in ('storting', 'overige_baten', 'btw_teruggave'):
            saldo += b['bedrag_excl']
        elif b['soort'] in ('btw_betaling', 'overboeking'):
            saldo -= b['bedrag_excl']
    return r2(saldo)


def btw_reserve():
    t = settings().get('btw_betaald_t_m') or ''
    m = re.match(r'^(\d{4})-Q([1-4])$', t)
    if not m:
        return 0.0, []
    y, k = int(m.group(1)), int(m.group(2))
    today = dt.date.today()
    huidig = (today.year, (today.month - 1) // 3 + 1)
    items, tot = [], 0.0
    while True:
        k += 1
        if k > 4:
            y, k = y + 1, 1
        if (y, k) > huidig:
            break
        sd = btw_calc(y, k)['saldo_5g']
        items.append({'jaar': y, 'kwartaal': k, 'saldo': sd})
        tot += sd
    return r2(tot), items


def dashboard(jaar):
    kw = []
    for k in range(1, 5):
        s, e = kwartaal_range(jaar, k)
        rows = q('SELECT * FROM boekingen WHERE ' + NC + ' AND datum BETWEEN ? AND ?', (s, e))
        omzet = sum(b['bedrag_excl'] for b in rows if b['soort'] == 'inkomst')
        omzet_mijn = sum(b['bedrag_excl'] * b['pct_ik'] / 100 for b in rows if b['soort'] == 'inkomst')
        kosten = sum(kosten_resultaat(b) for b in rows if b['soort'] == 'kosten')
        baten = sum(b['bedrag_excl'] for b in rows if b['soort'] == 'overige_baten')
        prive = sum(b['bedrag_excl'] for b in rows if b['soort'] == 'prive_uitgave' and b['rekening'] == 'wise')
        bt = btw_calc(jaar, k)
        kw.append({'kwartaal': k, 'omzet': r2(omzet), 'omzet_mijn_deel': r2(omzet_mijn), 'kosten': r2(kosten),
                   'overige_baten': r2(baten), 'winst': r2(omzet + baten - kosten), 'prive_wise': r2(prive), 'btw_saldo': bt['saldo_5g'], 'deadline': bt['deadline']})
    today = dt.date.today()
    open_f = q("SELECT * FROM boekingen WHERE soort='inkomst' AND betaald=0 AND " + NC + " ORDER BY datum")
    termijn = int(settings()['betaaltermijn'])
    for b in open_f:
        d = dt.date.fromisoformat(b['datum'])
        b['dagen_open'] = (today - d).days
        b['te_laat'] = (today - d).days > termijn
    controle = q('SELECT id,datum,omschrijving FROM boekingen WHERE controleren=1 AND ' + NC + ' ORDER BY datum')
    cfg = settings()
    ing = ing_saldo()
    werkelijk = float(cfg['ing_saldo_werkelijk']) if cfg.get('ing_saldo_werkelijk') not in (None, '') else None
    reserve, reserve_items = btw_reserve()
    v = verrekening({})
    schuld = max(0.0, r2(v['omzet_betaald_incl'] - v['kosten_bruto'] - v['jppw_betaald_incl']))
    return {'jaar': jaar, 'kwartalen': kw, 'open_facturen': open_f, 'te_controleren': controle,
            'wise_saldo': wise_saldo(), 'vandaag': today.isoformat(),
            'concepten': q("SELECT COUNT(*) n FROM boekingen WHERE status='concept'", one=True)['n'],
            'ing_saldo': ing, 'ing_werkelijk': werkelijk, 'ing_datum': cfg.get('ing_saldo_datum') or '',
            'btw_reserve': reserve, 'btw_reserve_items': reserve_items, 'schuld_jelmer': schuld,
            'vrij': r2(werkelijk - reserve - schuld) if werkelijk is not None else None}


def verrekening(p):
    van = p.get('van') or '0000-01-01'
    tot = p.get('tot') or '9999-12-31'
    rows = q('SELECT * FROM boekingen WHERE ' + NC + ' AND datum BETWEEN ? AND ? ORDER BY datum', (van, tot))
    inkomsten, kosten, jppw = [], [], []
    totalen = {'omzet_betaald': 0.0, 'omzet_open': 0.0, 'kosten': 0.0, 'jppw': 0.0, 'jppw_betaald': 0.0}
    for b in rows:
        deel_j = (100 - b['pct_ik']) / 100.0
        if b['soort'] == 'inkomst' and deel_j > 0:
            bedrag = (b['bedrag_excl'] + b['btw_bedrag']) * deel_j
            inkomsten.append({**b, 'jelmer_deel_incl': r2(bedrag)})
            totalen['omzet_betaald' if b['betaald'] else 'omzet_open'] += bedrag
        elif b['soort'] == 'kosten' and b['categorie'] == JPPW:
            jppw.append({**b, 'incl': r2(b['bedrag_excl'] + b['btw_bedrag'])})
            totalen['jppw'] += b['bedrag_excl'] + b['btw_bedrag']
            if b['betaald']:
                totalen['jppw_betaald'] += b['bedrag_excl'] + b['btw_bedrag']
        elif b['soort'] == 'kosten' and deel_j > 0:
            bruto = b['bedrag_excl'] + (b['btw_bedrag'] if b['btw_soort'] in AFTREKBAAR_IN_BRUTO else 0)
            kosten.append({**b, 'jelmer_deel_bruto': r2(bruto * deel_j)})
            totalen['kosten'] += bruto * deel_j
    netto = totalen['omzet_betaald'] - totalen['kosten'] - totalen['jppw']
    tarief = 1 + float(settings()['verlegd_tarief']) / 100
    return {'van': van, 'tot': tot, 'inkomsten': inkomsten, 'kosten': kosten, 'jppw_facturen': jppw,
            'omzet_betaald_incl': r2(totalen['omzet_betaald']), 'omzet_open_incl': r2(totalen['omzet_open']),
            'kosten_bruto': r2(totalen['kosten']), 'jppw_incl': r2(totalen['jppw']),
            'jppw_betaald_incl': r2(totalen['jppw_betaald']), 'jppw_open_incl': r2(totalen['jppw'] - totalen['jppw_betaald']),
            'netto_nog_te_factureren_incl': r2(netto), 'netto_nog_te_factureren_excl': r2(netto / tarief),
            'nog_te_verwachten_incl': r2(totalen['omzet_open'])}


# ---------------------------------------------------------------- abonnementen
def add_interval(start, interval, n):
    d = dt.date.fromisoformat(start)
    months = n if interval == 'maandelijks' else 12 * n
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def genereer_abonnementen():
    nieuw = []
    today = dt.date.today()
    with closing(db()) as c:
        for a in [dict(r) for r in c.execute('SELECT * FROM abonnementen WHERE actief=1').fetchall()]:
            if not a['startdatum']:
                continue
            n = 0
            while True:
                d = add_interval(a['startdatum'], a['interval'], n)
                if d > today:
                    break
                if not a['laatste_datum'] or d.isoformat() > a['laatste_datum']:
                    b = clean_boeking({
                        'soort': 'kosten', 'datum': d.isoformat(),
                        'omschrijving': a['naam'] + (' - ' + a['omschrijving'] if a['omschrijving'] else ''),
                        'relatie': a['naam'], 'bedrag_excl': a['bedrag_excl'], 'btw_bedrag': a['btw_bedrag'],
                        'btw_soort': a['btw_soort'], 'toewijzing': a['toewijzing'], 'pct_ik': a['pct_ik'],
                        'rekening': a['rekening'], 'categorie': a['categorie'], 'betaald': 1,
                        'controleren': a['variabel'], 'abonnement_id': a['id'],
                        'notitie': 'Automatisch aangemaakt uit abonnement.' + (' Bedrag controleren (wisselt per maand/koers).' if a['variabel'] else '')})
                    b['id'] = insert(c, 'boekingen', BOEK_FIELDS, b)
                    c.execute('UPDATE abonnementen SET laatste_datum=? WHERE id=?', (d.isoformat(), a['id']))
                    a['laatste_datum'] = d.isoformat()
                    nieuw.append(b)
                n += 1
        c.commit()
    return nieuw


# ---------------------------------------------------------------- inbox & bijlagen
def veilige_naam(naam):
    naam = os.path.basename(naam or 'bestand')
    stam, ext = os.path.splitext(naam)
    stam = re.sub(r'[^A-Za-z0-9._ -]+', '_', stam).strip(' ._') or 'bestand'
    return stam + ext.lower()


def inbox_bestanden():
    out = []
    for f in sorted(os.listdir(INBOX)):
        fp = os.path.join(INBOX, f)
        if f.startswith('.') or not os.path.isfile(fp):
            continue
        st = os.stat(fp)
        out.append({'naam': f, 'grootte': st.st_size, 'datum': dt.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')})
    return out


def archiveer(naam, datum, relatie):
    src = os.path.join(INBOX, os.path.basename(naam))
    if not os.path.isfile(src):
        raise ValueError('Bestand niet gevonden in de inbox: %s' % naam)
    datum = datum or dt.date.today().isoformat()
    kw = (int(datum[5:7]) - 1) // 3 + 1
    ddir = os.path.join(ARCHIEF, datum[:4], 'Q%d' % kw)
    os.makedirs(ddir, exist_ok=True)
    rel = re.sub(r'[^A-Za-z0-9]+', '_', relatie or '').strip('_')
    fn = '%s_%s_%s' % (datum, rel, veilige_naam(naam)) if rel else '%s_%s' % (datum, veilige_naam(naam))
    dest, i = os.path.join(ddir, fn), 1
    while os.path.exists(dest):
        stam, ext = os.path.splitext(fn)
        dest = os.path.join(ddir, '%s_%d%s' % (stam, i, ext))
        i += 1
    shutil.move(src, dest)
    return os.path.relpath(dest, BASE)


def koppel_bijlage(d):
    if d.get('inbox_bestand'):
        d['bijlage'] = archiveer(d['inbox_bestand'], d.get('datum'), d.get('relatie'))
    return d


def concepten():
    rows = q("SELECT * FROM boekingen WHERE status='concept' ORDER BY datum, id")
    for b in rows:
        tot = b['bedrag_excl'] + b['btw_bedrag']
        b['mogelijk_dubbel'] = q("SELECT id,datum,omschrijving FROM boekingen WHERE id<>? AND status<>'concept' AND ABS(bedrag_excl+btw_bedrag-?)<0.02 "
                                 "AND ABS(julianday(datum)-julianday(?))<=5 AND (relatie=? OR ?='')", (b['id'], tot, b['datum'], b['relatie'], b['relatie']))
    return rows


def pad_in(basis, rel):
    fp = os.path.normpath(os.path.join(BASE, rel))
    return fp if fp.startswith(os.path.normpath(basis) + os.sep) and os.path.isfile(fp) else None


# ---------------------------------------------------------------- facturen
def volgend_nummer():
    nrs = [int(r['nummer']) for r in q('SELECT nummer FROM facturen') if str(r['nummer']).isdigit()]
    return str(max(nrs) + 1) if nrs else '%d01' % dt.date.today().year


def clean_regels(regels):
    out = []
    for r in regels or []:
        om = (r.get('omschrijving') or '').strip()
        if not om and r.get('totaal') in (None, '') and r.get('prijs') in (None, ''):
            continue
        aantal, prijs, totaal = r.get('aantal'), r.get('prijs'), r.get('totaal')
        if totaal in (None, '') and aantal not in (None, '') and prijs not in (None, ''):
            totaal = float(aantal) * float(prijs)
        out.append({'periode': (r.get('periode') or '').strip(), 'omschrijving': om,
                    'aantal': float(aantal) if aantal not in (None, '') else None,
                    'prijs': float(prijs) if prijs not in (None, '') else None,
                    'prijs_tekst': r.get('prijs_tekst') or '', 'totaal': r2(totaal)})
    return out


def save_factuur(d, fid=None):
    cfg = settings()
    regels = clean_regels(d.get('regels'))
    if not regels:
        raise ValueError('Voeg minimaal één factuurregel toe')
    excl = r2(sum(r['totaal'] for r in regels))
    pct = float(d.get('btw_pct') if d.get('btw_pct') not in (None, '') else 21)
    btw = r2(excl * pct / 100)
    klant = d.get('klant') or {}
    rid = d.get('relatie_id') or None
    if rid:
        rel = q('SELECT * FROM relaties WHERE id=?', (rid,), one=True)
        if rel:
            klant = {k: rel[k] for k in ('naam', 'adres', 'postcode_plaats', 'kvk', 'btw_nr', 'email')}
    if not klant.get('naam'):
        raise ValueError('Kies een klant')
    nummer = str(d.get('nummer') or volgend_nummer())
    if q('SELECT id FROM facturen WHERE nummer=? AND id<>?', (nummer, fid or 0), one=True):
        raise ValueError('Factuurnummer %s bestaat al' % nummer)
    datum = d.get('datum') or dt.date.today().isoformat()
    rec = {'nummer': nummer, 'datum': datum, 'relatie_id': rid, 'klant': json.dumps(klant, ensure_ascii=False),
           'onder': d.get('onder') or 'fluid', 'regels': json.dumps(regels, ensure_ascii=False), 'btw_pct': pct,
           'termijn': int(d.get('termijn') or cfg['betaaltermijn']), 'betaallink': (d.get('betaallink') or '').strip(),
           'notitie': d.get('notitie') or '', 'excl': excl, 'btw': btw}
    fields = list(rec)
    toew = d.get('toewijzing') or 'ik'
    bsoort = {21.0: 'nl21', 9.0: 'nl9'}.get(pct, 'nl0')
    omschr = klant['naam'] + ' - ' + (regels[0]['omschrijving'] or 'factuur')
    with closing(db()) as c:
        if fid:
            update(c, 'facturen', fields, rec, fid)
            bk = c.execute('SELECT * FROM boekingen WHERE factuur_id=?', (fid,)).fetchone()
        else:
            fid = insert(c, 'facturen', fields, rec)
            bk = None
        b = clean_boeking({'soort': 'inkomst', 'datum': datum, 'omschrijving': omschr, 'relatie': klant['naam'],
                           'factuurnr': nummer, 'bedrag_excl': excl, 'btw_bedrag': btw, 'btw_soort': bsoort,
                           'toewijzing': toew, 'pct_ik': d.get('pct_ik'), 'betaald': bk['betaald'] if bk else 0,
                           'betaaldatum': bk['betaaldatum'] if bk else None, 'rekening': 'ing',
                           'categorie': bk['categorie'] if bk else 'Omzet', 'factuur_id': fid,
                           'notitie': bk['notitie'] if bk else '', 'controleren': bk['controleren'] if bk else 0})
        if bk:
            update(c, 'boekingen', BOEK_FIELDS, b, bk['id'])
        else:
            insert(c, 'boekingen', BOEK_FIELDS, b)
        c.commit()
    return fid


def factuur_json(fid):
    f = q('SELECT * FROM facturen WHERE id=?', (fid,), one=True)
    if not f:
        return None
    f['klant'] = json.loads(f['klant'] or '{}')
    f['regels'] = json.loads(f['regels'] or '[]')
    bk = q('SELECT * FROM boekingen WHERE factuur_id=?', (fid,), one=True) or {}
    f['betaald'] = bk.get('betaald', 0)
    f['betaaldatum'] = bk.get('betaaldatum')
    f['toewijzing'] = bk.get('toewijzing', 'ik')
    f['pct_ik'] = bk.get('pct_ik', 100)
    f['boeking_id'] = bk.get('id')
    f['totaal'] = r2(f['excl'] + f['btw'])
    f['vervaldatum'] = (dt.date.fromisoformat(f['datum']) + dt.timedelta(days=int(f['termijn']))).isoformat()
    return f


def eur(n):
    s = '{:,.2f}'.format(float(n or 0)).replace(',', 'X').replace('.', ',').replace('X', '.')
    return '€ ' + s


def nl_datum(s):
    return dt.date.fromisoformat(s).strftime('%d-%m-%Y') if s else ''


def factuur_html(fid):
    f = factuur_json(fid)
    if not f:
        return None
    cfg = settings()
    e = lambda x: html.escape(str(x or ''))
    k = f['klant']
    heeft_aantal = any(r.get('aantal') is not None or r.get('prijs') is not None or r.get('prijs_tekst') for r in f['regels'])
    heeft_periode = any(r.get('periode') for r in f['regels'])
    kop = ''
    if heeft_periode:
        kop += '<th>Datum / periode</th>'
    kop += '<th>Werkzaamheden</th>'
    if heeft_aantal:
        kop += '<th class=num>Aantal</th><th class=num>Prijs</th>'
    kop += '<th class=num>Totaal</th>'
    regels = ''
    for r in f['regels']:
        regels += '<tr>'
        if heeft_periode:
            regels += '<td>%s</td>' % e(r['periode'])
        regels += '<td>%s</td>' % e(r['omschrijving'])
        if heeft_aantal:
            aantal = ('%g' % r['aantal']) if r.get('aantal') is not None else ''
            prijs = r['prijs_tekst'] or (eur(r['prijs']) if r.get('prijs') is not None else '')
            regels += '<td class=num>%s</td><td class=num>%s</td>' % (e(aantal), e(prijs))
        regels += '<td class=num>%s</td></tr>' % eur(r['totaal'])
    logo = '<img class=logo src="/logo.png" alt="">' if os.path.exists(os.path.join(DATA, 'logo.png')) else ''
    voet = ''
    if f['onder'] == 'fluid' and cfg.get('voetregel_fluid'):
        voet = '<p class=sub>%s</p>' % '<br>'.join(e(x) for x in cfg['voetregel_fluid'].split('\n'))
    link = ''
    if f['betaallink']:
        link = '<tr><td>Online betalen:</td><td><a href="%s">%s</a></td></tr>' % (e(f['betaallink']), e(f['betaallink']))
    notitie = ('<p class=notitie>%s</p>' % e(f['notitie']).replace('\n', '<br>')) if f['notitie'] else ''
    kvk_btw = ''
    if k.get('kvk'):
        kvk_btw += 'KvK nr: %s<br>' % e(k['kvk'])
    if k.get('btw_nr'):
        kvk_btw += 'BTW nr: %s' % e(k['btw_nr'])
    gemaild = ('<p class=gemaild>Gemaild naar: %s</p>' % e(k['email'])) if k.get('email') else ''
    return """<!doctype html><html lang=nl><head><meta charset=utf-8><title>Factuur %(nr)s - %(klant)s</title>
<style>
 @page{size:A4;margin:14mm}
 body{font:13px/1.45 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1c1c1e;margin:0;background:#f2f2f4}
 .blad{background:#fff;max-width:794px;margin:16px auto;padding:44px 52px 120px;box-shadow:0 1px 8px rgba(0,0,0,.12);min-height:1000px;position:relative;box-sizing:border-box}
 .knop{position:fixed;top:12px;right:16px;background:#1c1c1e;color:#fff;border:0;border-radius:8px;padding:9px 16px;font:600 13px sans-serif;cursor:pointer}
 .logo{max-height:70px;max-width:220px;float:right}
 h1{font-size:30px;margin:0 0 6px}
 .gemaild{color:#6b6b70;margin:0 0 26px}
 .kop{display:flex;justify-content:space-between;gap:20px;margin-bottom:34px}
 .meta td{padding:1px 0 1px 18px}.meta td:first-child{color:#6b6b70;padding-left:0}
 table.regels{width:100%%;border-collapse:collapse;margin-bottom:6px}
 table.regels th{text-align:left;border-bottom:2px solid #1c1c1e;padding:6px 8px 6px 0;font-size:12px}
 table.regels td{padding:8px 8px 8px 0;border-bottom:1px solid #e3e3e6;vertical-align:top}
 .num{text-align:right;white-space:nowrap}
 table.regels th.num{text-align:right}
 table.totaal{margin-left:auto;margin-top:8px;min-width:260px;border-collapse:collapse}
 table.totaal td{padding:3px 0 3px 22px}.totaal .t td{font-weight:700;border-top:2px solid #1c1c1e;padding-top:7px}
 table.betaling{margin-top:34px}table.betaling td{padding:2px 20px 2px 0}table.betaling td:first-child{color:#6b6b70}
 .notitie{margin-top:18px;color:#444;font-size:12px}
 .sub{margin-top:30px;color:#6b6b70;font-size:12px}
 .voet{position:absolute;left:52px;right:52px;bottom:34px;border-top:1px solid #d6d6da;padding-top:10px;display:flex;gap:26px;font-size:11.5px;color:#444}
 .voet div{flex:1}
 @media print{body{background:#fff}.blad{box-shadow:none;margin:0;padding:0 0 100px;min-height:255mm}.knop{display:none}.voet{left:0;right:0;bottom:0}}
</style></head><body>
<button class=knop onclick="window.print()">Afdrukken / opslaan als PDF</button>
<div class=blad>
 %(logo)s<h1>Factuur</h1>%(gemaild)s
 <div class=kop><div><b>%(klant)s</b><br>%(adres)s<br>%(plaats)s<br>%(kvkbtw)s</div>
 <table class=meta><tr><td>Factuurdatum:</td><td>%(datum)s</td></tr><tr><td>Factuurnummer:</td><td><b>%(nr)s</b></td></tr></table></div>
 <table class=regels><tr>%(kop)s</tr>%(regels)s</table>
 <table class=totaal><tr><td>Totaal ex. BTW</td><td class=num>%(excl)s</td></tr>
 <tr><td>BTW (%(pct)g%%)</td><td class=num>%(btw)s</td></tr>
 <tr class=t><td>Totaal inc. BTW</td><td class=num>%(totaal)s</td></tr></table>
 %(notitie)s
 <table class=betaling><tr><td>Betalingstermijn:</td><td>%(termijn)s dagen na de factuurdatum, voor %(vervalt)s</td></tr>
 <tr><td>Account op naam van:</td><td>%(bedrijf)s</td></tr><tr><td>IBAN nummer:</td><td>%(iban)s</td></tr>
 <tr><td>Factuurnummer:</td><td>%(nr)s</td></tr>%(link)s</table>
 %(voet)s
 <div class=voet><div><b>%(bedrijf)s</b><br>%(adres_e)s<br>%(plaats_e)s</div>
 <div>%(naam)s<br>E: %(email)s<br>M: %(tel)s</div>
 <div>IBAN nr: %(iban)s<br>BTW nr: %(btwnr)s<br>KvK nr: %(kvk)s</div></div>
</div></body></html>""" % {
        'nr': e(f['nummer']), 'klant': e(k.get('naam')), 'adres': e(k.get('adres')), 'plaats': e(k.get('postcode_plaats')),
        'kvkbtw': kvk_btw, 'logo': logo, 'gemaild': gemaild, 'datum': nl_datum(f['datum']), 'kop': kop, 'regels': regels,
        'excl': eur(f['excl']), 'btw': eur(f['btw']), 'totaal': eur(f['totaal']), 'pct': f['btw_pct'], 'notitie': notitie,
        'termijn': f['termijn'], 'vervalt': nl_datum(f['vervaldatum']), 'bedrijf': e(cfg['bedrijfsnaam']), 'iban': e(cfg['iban']),
        'link': link, 'voet': voet, 'adres_e': e(cfg['adres']), 'plaats_e': e(cfg['postcode_plaats']), 'naam': e(cfg['naam']),
        'email': e(cfg['email']), 'tel': e(cfg['telefoon']), 'btwnr': e(cfg['btw_nr']), 'kvk': e(cfg['kvk'])}


# ---------------------------------------------------------------- HTTP
def bank_analyse(body):
    import bank
    bronnen, waarsch = {}, []
    for f in body.get('bestanden', []):
        res = bank.parse(f.get('naam', 'bestand'), f.get('tekst', ''))
        waarsch += res['waarschuwingen']
        if not res['bron']:
            continue
        b = bronnen.setdefault(res['bron'], {'regels': [], 'saldo': None, 'saldo_datum': None})
        b['regels'] += res['regels']
        if res['saldo_datum'] and (not b['saldo_datum'] or res['saldo_datum'] >= b['saldo_datum']):
            b['saldo'], b['saldo_datum'] = res['saldo'], res['saldo_datum']
    boekingen = q("SELECT * FROM boekingen WHERE " + NC)
    uit = {'waarschuwingen': waarsch, 'rekeningen': {}}
    for bron, b in bronnen.items():
        a = bank.afstemmen(bron, b['regels'], boekingen)
        app = ing_saldo(b['saldo_datum']) if bron == 'ing' else wise_saldo(b['saldo_datum'])
        a.update(saldo_bank=b['saldo'], saldo_datum=b['saldo_datum'], saldo_app=app,
                 saldo_verschil=r2((b['saldo'] or 0) - app), aantal=len(b['regels']))
        a['prive_maanden'] = bank.prive_per_maand(a['prive'], boekingen) if bron == 'wise' else []
        a['prive'] = len(a['prive'])
        for k in ('nieuw', 'zakelijk_onbekend'):
            a[k] = [{x: v for x, v in r.items() if x not in ('tijd',)} for r in a[k]]
        uit['rekeningen'][bron] = a
    return uit


def bank_concept(d):
    rek = 'wise' if d.get('rekening') == 'wise' else 'ing'
    bedrag = float(d.get('bedrag') or 0)
    topup = 'topped up' in (d.get('omschrijving') or '').lower()
    soort = 'storting' if (topup or (rek == 'wise' and bedrag > 0)) else ('inkomst' if bedrag > 0 else 'kosten')
    b = clean_boeking({'soort': soort, 'datum': d.get('datum'), 'omschrijving': (d.get('tegenpartij') or d.get('omschrijving') or '')[:120],
                       'relatie': d.get('tegenpartij') or '', 'bedrag_excl': abs(bedrag), 'btw_soort': 'geen', 'rekening': rek,
                       'betaald': 1, 'betaaldatum': d.get('datum'), 'controleren': 1, 'status': 'concept',
                       'notitie': 'Uit bankafschrift – controleer soort, btw en bijlage. ' + (d.get('omschrijving') or '')[:200]})
    with closing(db()) as c:
        b['id'] = insert(c, 'boekingen', BOEK_FIELDS, b)
        c.commit()
    return b


def bank_prive(d):
    maand = (d.get('maand') or '')[:7]
    b = clean_boeking({'soort': 'prive_uitgave', 'datum': d.get('datum'), 'omschrijving': 'Privé-uitgaven Wise %s (bankafstemming)' % maand,
                       'bedrag_excl': float(d.get('bedrag') or 0), 'rekening': 'wise', 'categorie': 'Privé', 'betaald': 1,
                       'betaaldatum': d.get('datum'), 'notitie': 'Verschil uit Wise-export; bij afstemmen toegevoegd.'})
    with closing(db()) as c:
        b['id'] = insert(c, 'boekingen', BOEK_FIELDS, b)
        c.commit()
    return b


def samenvatting_md():
    d = dashboard(dt.date.today().year)
    e = lambda n: ('€ {:,.2f}'.format(n or 0)).replace(',', 'X').replace('.', ',').replace('X', '.')
    l = ['# Samenvatting administratie Lenffect', '', 'Bijgewerkt: %s' % dt.datetime.now().strftime('%d-%m-%Y %H:%M'), '',
         '## Saldi', '',
         '- ING zakelijk (berekend): %s' % e(d['ing_saldo']),
         '- ING zakelijk (laatst gecontroleerd met bank): %s (%s)' % (e(d['ing_werkelijk']) if d['ing_werkelijk'] is not None else '-', d['ing_datum'] or '-'),
         '- Wise-kaart (berekend): %s' % e(d['wise_saldo']),
         '- Btw-reserve (nog te betalen kwartalen): %s' % e(d['btw_reserve']),
         '- Schuld aan Jelmer (verrekening): %s' % e(d['schuld_jelmer']),
         '- Vrij te besteden: %s' % (e(d['vrij']) if d['vrij'] is not None else '-'), '',
         '## Per kwartaal %d' % d['jaar'], '', '| Kwartaal | Omzet (excl.) | Kosten (mijn deel) | Winst | Btw (+ af te dragen / − terug) | Aangifte vóór |', '|---|---:|---:|---:|---:|---|']
    for k in d['kwartalen']:
        l.append('| Q%d | %s | %s | %s | %s | %s |' % (k['kwartaal'], e(k['omzet']), e(k['kosten']), e(k['winst']), e(k['btw_saldo']), k['deadline']))
    l += ['', '## Openstaande facturen (aan klanten)', '']
    l += ['- %s · %s · %s · %d dagen open' % (b['factuurnr'], b['relatie'], e(b['bedrag_excl'] + b['btw_bedrag']), b['dagen_open']) for b in d['open_facturen']] or ['- geen']
    l += ['', '## Nog te controleren', '', '- concepten: %d' % d['concepten']]
    l += ['- %s %s' % (b['datum'], b['omschrijving']) for b in d['te_controleren']]
    return '\n'.join(l) + '\n'


def export_doel():
    gekozen = (settings().get('export_map') or '').strip()
    doel = os.path.abspath(os.path.expanduser(gekozen)) if gekozen else os.path.join(BASE, 'export')
    verboden = [BASE, DATA, INBOX, ARCHIEF, os.path.join(BASE, 'btw')]
    if any(os.path.normpath(doel) == os.path.normpath(v) for v in verboden):
        raise ValueError('Kies voor de export een aparte map (niet de programmamap, data, facturen of inbox).')
    return doel


def maak_export():
    import export
    res = export.exporteer(BASE, DB, export_doel(), samenvatting_md())
    return res


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        u = urlparse(self.path)
        p = u.path
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = {}
        try:
            if method == 'POST' and p == '/api/inbox/upload':
                naam = veilige_naam(unquote(self.headers.get('X-Filename', 'bestand')))
                if os.path.splitext(naam)[1] not in TOEGESTAAN:
                    raise ValueError('Alleen pdf, jpg, png, webp of heic (niet: %s)' % naam)
                n = int(self.headers.get('Content-Length') or 0)
                if n > 40 * 1024 * 1024:
                    raise ValueError('Bestand is te groot (max 40 MB)')
                data = self.rfile.read(n)
                dest, i = os.path.join(INBOX, naam), 1
                while os.path.exists(dest):
                    stam, ext = os.path.splitext(naam)
                    dest = os.path.join(INBOX, '%s_%d%s' % (stam, i, ext))
                    i += 1
                with open(dest, 'wb') as fh:
                    fh.write(data)
                return self.send(200, {'ok': True, 'naam': os.path.basename(dest)})
            if method in ('POST', 'PUT'):
                n = int(self.headers.get('Content-Length') or 0)
                if n:
                    body = json.loads(self.rfile.read(n).decode('utf-8'))
            if method == 'GET' and p in ('/', '/index.html'):
                with open(os.path.join(BASE, 'index.html'), 'rb') as fh:
                    return self.send(200, fh.read(), 'text/html; charset=utf-8')
            if method == 'GET' and p == '/favicon.ico':
                return self.send(204, b'', 'image/x-icon')
            if method == 'GET' and p == '/logo.png':
                fp = os.path.join(DATA, 'logo.png')
                if os.path.exists(fp):
                    with open(fp, 'rb') as fh:
                        return self.send(200, fh.read(), 'image/png')
                return self.send(404, 'geen logo', 'text/plain')
            m = re.match(r'^/(inbox|bijlage)/(.+)$', p)
            if method == 'GET' and m:
                if m.group(1) == 'inbox':
                    fp = pad_in(INBOX, os.path.join('inbox', os.path.basename(unquote(m.group(2)))))
                else:
                    b = q('SELECT bijlage FROM boekingen WHERE id=?', (int(m.group(2)),), one=True) if m.group(2).isdigit() else None
                    fp = pad_in(ARCHIEF, b['bijlage']) if b and b['bijlage'] else None
                if not fp:
                    return self.send(404, 'Bestand niet gevonden', 'text/plain')
                with open(fp, 'rb') as fh:
                    return self.send(200, fh.read(), mimetypes.guess_type(fp)[0] or 'application/octet-stream')
            m = re.match(r'^/factuur/(\d+)$', p)
            if method == 'GET' and m:
                h = factuur_html(int(m.group(1)))
                return self.send(200 if h else 404, h or 'Niet gevonden', 'text/html; charset=utf-8')
            if not p.startswith('/api/'):
                return self.send(404, {'fout': 'niet gevonden'})
            out = self.api(method, p[5:], params, body)
            return self.send(200, out)
        except ValueError as ex:
            return self.send(400, {'fout': str(ex)})
        except Exception as ex:  # noqa
            import traceback
            traceback.print_exc()
            return self.send(500, {'fout': 'Interne fout: %s' % ex})

    def api(self, method, p, params, body):
        # --- boekingen
        if p == 'boekingen' and method == 'GET':
            return list_boekingen(params)
        if p == 'export' and method == 'POST':
            return maak_export()
        if p == 'bank/analyse' and method == 'POST':
            return bank_analyse(body)
        if p == 'bank/concept' and method == 'POST':
            return bank_concept(body)
        if p == 'bank/prive' and method == 'POST':
            return bank_prive(body)
        if p == 'bank/saldo' and method == 'POST':
            d = body.get('datum') or ''
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
                raise ValueError('Ongeldige datum')
            with closing(db()) as c:
                c.execute("UPDATE instellingen SET waarde=? WHERE sleutel='ing_saldo_werkelijk'", (str(r2(body.get('saldo'))),))
                c.execute("UPDATE instellingen SET waarde=? WHERE sleutel='ing_saldo_datum'", (d,))
                c.commit()
            return {'ok': True}
        if p == 'inbox' and method == 'GET':
            return {'map': INBOX, 'bestanden': inbox_bestanden(), 'concepten': concepten()}
        if p == 'boekingen' and method == 'POST':
            b = clean_boeking(koppel_bijlage(body))
            with closing(db()) as c:
                b['id'] = insert(c, 'boekingen', BOEK_FIELDS, b)
                c.commit()
            return b
        m = re.match(r'^boekingen/(\d+)$', p)
        if m:
            i = int(m.group(1))
            if method == 'GET':
                return q('SELECT * FROM boekingen WHERE id=?', (i,), one=True)
            if method == 'PUT':
                old = q('SELECT * FROM boekingen WHERE id=?', (i,), one=True)
                if not old:
                    raise ValueError('Boeking bestaat niet')
                b = clean_boeking(koppel_bijlage({**old, **body}))
                with closing(db()) as c:
                    update(c, 'boekingen', BOEK_FIELDS, b, i)
                    c.commit()
                return b
            if method == 'DELETE':
                old = q('SELECT * FROM boekingen WHERE id=?', (i,), one=True)
                if old and old['factuur_id']:
                    raise ValueError('Deze boeking hoort bij een factuur. Verwijder de factuur om hem ook uit de boekhouding te halen.')
                with closing(db()) as c:
                    c.execute('DELETE FROM boekingen WHERE id=?', (i,))
                    c.commit()
                return {'ok': True}
        # --- rapporten
        if p == 'btw' and method == 'GET':
            return btw_calc(int(params['jaar']), int(params['kwartaal']))
        if p == 'dashboard' and method == 'GET':
            return dashboard(int(params.get('jaar') or dt.date.today().year))
        if p == 'verrekening' and method == 'GET':
            return verrekening(params)
        if p == 'abonnementen/genereer' and method == 'POST':
            return genereer_abonnementen()
        # --- relaties / abonnementen (eenvoudige CRUD)
        for name, table, fields in (('relaties', 'relaties', REL_FIELDS), ('abonnementen', 'abonnementen', ABO_FIELDS)):
            if p == name and method == 'GET':
                return q('SELECT * FROM %s ORDER BY naam COLLATE NOCASE' % table)
            if p == name and method == 'POST':
                with closing(db()) as c:
                    body['id'] = insert(c, table, fields, {k: body.get(k) for k in fields})
                    c.commit()
                return body
            m = re.match(r'^%s/(\d+)$' % name, p)
            if m and method == 'PUT':
                old = q('SELECT * FROM %s WHERE id=?' % table, (int(m.group(1)),), one=True) or {}
                new = {**old, **body}
                with closing(db()) as c:
                    update(c, table, fields, new, int(m.group(1)))
                    c.commit()
                return new
            if m and method == 'DELETE':
                with closing(db()) as c:
                    c.execute('DELETE FROM %s WHERE id=?' % table, (int(m.group(1)),))
                    c.commit()
                return {'ok': True}
        # --- facturen
        if p == 'facturen' and method == 'GET':
            ids = [r['id'] for r in q('SELECT id FROM facturen ORDER BY datum DESC, nummer DESC')]
            return [factuur_json(i) for i in ids]
        if p == 'facturen/volgend' and method == 'GET':
            return {'nummer': volgend_nummer()}
        if p == 'facturen' and method == 'POST':
            return factuur_json(save_factuur(body))
        m = re.match(r'^facturen/(\d+)$', p)
        if m:
            i = int(m.group(1))
            if method == 'GET':
                return factuur_json(i)
            if method == 'PUT':
                return factuur_json(save_factuur(body, i))
            if method == 'DELETE':
                with closing(db()) as c:
                    c.execute('DELETE FROM boekingen WHERE factuur_id=?', (i,))
                    c.execute('DELETE FROM facturen WHERE id=?', (i,))
                    c.commit()
                return {'ok': True}
        # --- instellingen
        if p == 'instellingen' and method == 'GET':
            return settings()
        if p == 'instellingen' and method == 'PUT':
            with closing(db()) as c:
                for k, v in body.items():
                    c.execute('INSERT OR REPLACE INTO instellingen(sleutel,waarde) VALUES(?,?)', (k, str(v)))
                c.commit()
            return settings()
        raise ValueError('Onbekende actie: %s %s' % (method, p))

    def do_GET(self): self.handle_any('GET')
    def do_POST(self): self.handle_any('POST')
    def do_PUT(self): self.handle_any('PUT')
    def do_DELETE(self): self.handle_any('DELETE')


def veilig_exporteren(wanneer):
    try:
        r = maak_export()
        print('Export (%s) bijgewerkt: %s' % (wanneer, r['map']))
    except Exception as ex:  # noqa — een mislukte export mag het afsluiten nooit blokkeren
        print('Let op: export (%s) is mislukt: %s' % (wanneer, ex))


def main():
    import signal
    init_db()
    veilig_exporteren('start')
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    url = 'http://127.0.0.1:%d' % PORT
    print('Lenffect administratie draait op', url, '(sluit dit venster of druk Ctrl+C om te stoppen; daarbij wordt de export bijgewerkt)')
    if not os.environ.get('NO_BROWSER'):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    def stop(*_a):
        raise KeyboardInterrupt
    for sig in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), stop)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print('Afsluiten… export bijwerken')
        veilig_exporteren('afsluiten')
        srv.server_close()


if __name__ == '__main__':
    main()
