from prospect_filters import filter_prospects
from research import contacts_from_page, parse_page, merge_contacts
import storage


def test_target_separates_selection_management_and_generic_titles():
    rows = [{'fonction': role} for role in ['Head of Fund Selection', 'Portfolio Manager', 'Managing Partner', 'Responsable sélection de fonds']]
    assert filter_prospects(rows, 'Sélection de fonds / Architecture ouverte') == [rows[0], rows[3]]
    assert filter_prospects(rows, 'Gestion / Allocation') == [rows[1]]
    assert filter_prospects(rows) == rows


def test_coordinates_do_not_include_empty_values():
    rows = [{'email': '', 'telephone': ''}, {'email': 'a@example.com'}, {'telephone': '+33123456789'}]
    assert filter_prospects(rows, coordinates='Avec email') == [rows[1]]
    assert filter_prospects(rows, coordinates='Avec email ou téléphone') == rows[1:]


def test_extraction_keeps_colleagues_separate():
    page = parse_page('https://example.com/team', '''<article><h2>Marie Dupont</h2><p>Head of Fund Selection</p></article><article><h2>Paul Martin</h2><p>Portfolio Manager</p></article>''')
    contacts = merge_contacts(contacts_from_page(page))
    assert {p['nom']: p['fonction'] for p in contacts} == {'Marie Dupont': 'Head of Fund Selection', 'Paul Martin': 'Portfolio Manager'}


def test_repeat_research_preserves_manual_corrections_and_entities(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'test.sqlite3')
    contact = dict(nom='Marie Dupont', fonction='Head of Fund Selection', email='', telephone='', role_decisionnel='', priorite='Haute', source='https://example.com/team', statut='À vérifier', preuve='Published role')
    bundle = dict(profile=dict(url='https://example.com', nom='Alpha'), contacts=[contact], diagnostic=[])
    cid = storage.save_research(bundle)
    person = storage.get_company(cid)[1][0]
    storage.update_contact(person['id'], {'email': 'confirmed@example.com', 'status': 'Vérifié manuellement'})
    assert storage.save_research(bundle) == cid
    people = storage.get_company(cid)[1]
    assert len(people) == 1
    assert people[0]['email'] == 'confirmed@example.com'
    assert people[0]['status'] == 'Vérifié manuellement'
    other = {**bundle, 'profile': {**bundle['profile'], 'nom': 'Beta'}}
    assert storage.save_research(other) != cid


def test_public_coordinates_in_person_links():
    page = parse_page('https://example.com/team', '''<article><h2>Marie Dupont</h2><p>Head of Fund Selection</p><a href="mailto:marie%40example.com?subject=Bonjour">Email</a><a href="tel:+33123456789">Appeler</a></article><article><h2>Paul Martin</h2><p>Portfolio Manager</p><a href="mailto:paul@example.com">Email</a></article>''')
    people = {p['nom']: p for p in merge_contacts(contacts_from_page(page))}
    assert people['Marie Dupont']['email'] == 'marie@example.com'
    assert people['Marie Dupont']['telephone'] == '+33123456789'
    assert people['Paul Martin']['email'] == 'paul@example.com'
    assert people['Paul Martin']['telephone'] == ''


def test_conflicting_coordinates_are_not_assigned():
    page = parse_page('https://example.com/team', '''<article><h2>Marie Dupont</h2><p>Head of Fund Selection</p><a href="mailto:first@example.com">Email</a><a href="mailto:second@example.com">Assistant</a></article>''')
    assert all(not p['email'] for p in contacts_from_page(page))


def test_duplicate_contacts_in_same_bundle_and_backup(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'original.sqlite3')
    contact = dict(nom='Marie Dupont', fonction='Portfolio Manager', email='', telephone='', role_decisionnel='', priorite='Moyenne', source='', statut='', preuve='')
    bundle = dict(profile=dict(url='https://example.com', nom='Alpha'), contacts=[contact, {**contact, 'nom': 'Marie Dúpont', 'email': 'marie@example.com'}, contact], diagnostic=[])
    cid = storage.save_research(bundle)
    people = storage.get_company(cid)[1]
    assert len(people) == 1
    assert people[0]['email'] == 'marie@example.com'
    backup = tmp_path / 'backup.sqlite3'
    backup.write_bytes(storage.database_backup())
    with sqlite3.connect(backup) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert con.execute('SELECT email FROM contacts').fetchone()[0] == 'marie@example.com'


def test_backup_closes_connections_before_removing_temporary_directory(tmp_path, monkeypatch):
    import sqlite3
    import pytest
    import tempfile
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'original.sqlite3')
    real_connect = sqlite3.connect
    real_directory = tempfile.TemporaryDirectory
    connections = []

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    class CheckedDirectory(real_directory):
        def __exit__(self, *args):
            # Linux autorise la suppression d'un fichier ouvert : vérifier
            # explicitement la fermeture pour reproduire la contrainte Windows.
            for connection in connections:
                with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                    connection.execute('SELECT 1')
            return super().__exit__(*args)

    monkeypatch.setattr(sqlite3, 'connect', tracked_connect)
    monkeypatch.setattr(tempfile, 'TemporaryDirectory', CheckedDirectory)
    assert storage.database_backup().startswith(b'SQLite format 3')
    assert len(connections) == 2
