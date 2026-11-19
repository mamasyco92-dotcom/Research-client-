"""Stockage local SQLite indépendant du fichier Excel VBA."""
from __future__ import annotations
from contextlib import closing
import json
import hashlib
import unicodedata
import re
from contact_quality import folded
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

DB_PATH=Path(__file__).with_name('recherches.sqlite3')


def connect():
    conn=sqlite3.connect(DB_PATH)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS companies(
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       domain TEXT UNIQUE NOT NULL,
       name TEXT NOT NULL,
       url TEXT NOT NULL,
       profile_json TEXT NOT NULL,
       diagnostic_json TEXT NOT NULL,
       updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS contacts(
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
       name TEXT NOT NULL,
       role TEXT NOT NULL DEFAULT '',
       email TEXT NOT NULL DEFAULT '',
       phone TEXT NOT NULL DEFAULT '',
       decision TEXT NOT NULL DEFAULT '',
       priority TEXT NOT NULL DEFAULT '',
       source TEXT NOT NULL DEFAULT '',
       status TEXT NOT NULL DEFAULT '',
       evidence TEXT NOT NULL DEFAULT ''
    );
    ''')
    return conn


def _identity_name(name: str) -> str:
    """Identité normalisée : une autre entité du même domaine reste indépendante."""
    name = unicodedata.normalize('NFKD', str(name or '').casefold())
    name = ''.join(c for c in name if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', name).strip()


def _storage_identity(con, host: str, name: str) -> tuple[int | None, str]:
    """Compatibilité SQLite : ne change ni le schéma ni les anciennes fiches.

    L'ancien champ 'domain' est unique. Les nouvelles entités partageant le même
    site officiel reçoivent une clé stable dérivée du domaine ET du nom exact.
    Les anciennes fiches conservent leur identifiant et leurs contacts.
    """
    existing = con.execute(
        'SELECT id, domain, name FROM companies WHERE domain=? OR domain LIKE ?',
        (host, host + '::%')).fetchall()
    target = _identity_name(name)
    for row in existing:
        if _identity_name(row['name']) == target:
            return row['id'], row['domain']
    if not existing:
        return None, host
    digest = hashlib.sha256(target.encode('utf-8')).hexdigest()[:20]
    return None, host + '::' + digest


def save_research(bundle: dict) -> int:
    profile=bundle['profile']
    host=(urlparse(profile['url']).hostname or '').lower().removeprefix('www.')
    if not host:
        raise ValueError('URL de société invalide : impossible d’enregistrer la fiche.')
    name = str(profile.get('nom') or '').strip()
    if not _identity_name(name):
        raise ValueError('Nom de société manquant : renseigne le nom exact.')
    with connect() as con:
        existing_id, key = _storage_identity(con, host, name)
        if existing_id is None:
            con.execute('''INSERT INTO companies(domain,name,url,profile_json,diagnostic_json)
              VALUES(?,?,?,?,?)''',
              (key, name, profile['url'], json.dumps(profile,ensure_ascii=False),
               json.dumps(bundle['diagnostic'],ensure_ascii=False)))
            cid=con.execute('SELECT last_insert_rowid()').fetchone()[0]
        else:
            cid=existing_id
            con.execute('''UPDATE companies SET name=?,url=?,profile_json=?,diagnostic_json=?,
              updated=CURRENT_TIMESTAMP WHERE id=?''',
              (name, profile['url'], json.dumps(profile,ensure_ascii=False),
               json.dumps(bundle['diagnostic'],ensure_ascii=False), cid))
        # Les corrections manuelles et les contacts d'une même identité sont préservés.
        # Aucun contact d'une AUTRE entité sur le même site n'est mélangé.
        existing={folded(r['name']):dict(r) for r in con.execute('SELECT * FROM contacts WHERE company_id=?',(cid,))}
        for c in bundle['contacts']:
            prev=existing.get(folded(c['nom']))
            if prev:
                updates={'role':prev['role'] or c['fonction'],'email':prev['email'] or c['email'],
                         'phone':prev['phone'] or c['telephone'],'decision':prev['decision'] or c['role_decisionnel'],
                         'priority':prev['priority'] or c['priorite'],'source':prev['source'] or c['source'],
                         'status':prev['status'] or c['statut'],'evidence':prev['evidence'] or c['preuve']}
                added = [c[field] for column, field in (('email', 'email'), ('phone', 'telephone'))
                         if not prev[column] and c[field]]
                if added and c['source'] != prev['source']:
                    citation = f"Coordonnées ({', '.join(added)}) : {c['source']}"
                    updates['evidence'] = ' — '.join(filter(None, (prev['evidence'], citation, c['preuve'])))
                con.execute('''UPDATE contacts SET role=:role,email=:email,phone=:phone,
                     decision=:decision,priority=:priority,source=:source,status=:status,evidence=:evidence
                     WHERE id=:id''',{**updates,'id':prev['id']})
                existing[folded(c['nom'])] = {**prev, **updates}
            else:
                con.execute('''INSERT INTO contacts(company_id,name,role,email,phone,decision,priority,source,status,evidence)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                            (cid,c['nom'],c['fonction'],c['email'],c['telephone'],c['role_decisionnel'],c['priorite'],
                             c['source'],c['statut'],c['preuve']))
                added = con.execute('SELECT * FROM contacts WHERE id=last_insert_rowid()').fetchone()
                existing[folded(c['nom'])] = dict(added)
    return cid


def companies() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT id,name,url,updated FROM companies ORDER BY updated DESC,id DESC')]


def get_company(company_id:int) -> tuple[dict, list[dict], list[str]]:
    with connect() as con:
        row=con.execute('SELECT * FROM companies WHERE id=?',(company_id,)).fetchone()
        if not row:raise KeyError('Société introuvable')
        contacts=[dict(r) for r in con.execute('SELECT * FROM contacts WHERE company_id=? ORDER BY CASE priority WHEN "Haute" THEN 0 WHEN "Moyenne" THEN 1 ELSE 2 END,name',(company_id,))]
        return json.loads(row['profile_json']),contacts,json.loads(row['diagnostic_json'])


def export_contacts(company_ids: list[int] | None = None) -> list[dict]:
    """Une ligne par contact et une ligne société quand elle est sans contact."""
    if company_ids == []:
        return []
    query = """
        SELECT companies.name AS societe, contacts.name AS nom, role AS fonction,
               email, phone AS telephone, decision AS role_decisionnel,
               priority AS priorite, source, status AS statut
        FROM companies LEFT JOIN contacts ON contacts.company_id = companies.id
    """
    if company_ids is not None:
        placeholders = ','.join('?' for _ in company_ids)
        query += f' WHERE companies.id IN ({placeholders})'
    query += ' ORDER BY companies.name COLLATE NOCASE, companies.id, contacts.name COLLATE NOCASE, contacts.id'
    with closing(connect()) as con:
        return [dict(row) for row in con.execute(query, company_ids or [])]


def update_contact(contact_id:int, changes:dict):
    whitelist={'name','role','email','phone','decision','priority','status'}
    vals={k:str(v or '') for k,v in changes.items() if k in whitelist}
    if not vals:return
    with connect() as con:
        con.execute('UPDATE contacts SET '+','.join(f'{k}=?' for k in vals)+' WHERE id=?',[*vals.values(),contact_id])


def remove_contact(contact_id:int):
    with connect() as con:con.execute('DELETE FROM contacts WHERE id=?',(contact_id,))


def database_backup() -> bytes:
    """Instantané SQLite cohérent, y compris si le journal WAL est actif."""
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'recherches.sqlite3'
        # Le contexte SQLite valide la transaction mais ne ferme pas le fichier.
        # Fermer les deux connexions avant la suppression du dossier sous Windows.
        with closing(connect()) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        return path.read_bytes()
