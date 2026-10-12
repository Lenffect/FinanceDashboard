#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voegt boekingen toe als CONCEPT (tellen pas mee na jouw goedkeuring in de app).
Gebruik:  python3 concept_toevoegen.py concepten.json
Het JSON-bestand is een lijst met boekingen (zelfde velden als in de app). Met "inbox_bestand": "naam.pdf"
wordt de factuur uit de map inbox/ naar het archief (facturen/<jaar>/Q<n>/) verplaatst en aan de boeking gekoppeld."""
import sys, os, json
from contextlib import closing
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S

S.init_db()
n = 0
with closing(S.db()) as c:
    for d in json.load(open(sys.argv[1], encoding='utf-8')):
        d['status'] = 'concept'
        S.koppel_bijlage(d)
        S.insert(c, 'boekingen', S.BOEK_FIELDS, S.clean_boeking(d))
        n += 1
    c.commit()
print('%d concept(en) toegevoegd' % n)
