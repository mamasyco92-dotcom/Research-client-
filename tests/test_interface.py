from streamlit.testing.v1 import AppTest
import storage


def test_navigation_and_target_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'ui.sqlite3')
    people = []
    for name, role in [('Marie Dupont', 'Head of Fund Selection'), ('Paul Martin', 'Portfolio Manager')]:
        people.append(dict(nom=name, fonction=role, email='', telephone='', role_decisionnel='', priorite='Haute', source='https://example.com/team', statut='Source officielle', preuve='Fonction publiée'))
    storage.save_research(dict(profile=dict(nom='Alpha', url='https://example.com'), contacts=people, diagnostic=[]))
    at = AppTest.from_file('../app.py').run(timeout=20)
    assert not at.exception
    at.radio[0].set_value('Contacts').run()
    assert not at.exception
    target = next(s for s in at.selectbox if s.label == 'Interlocuteurs recherchés')
    target.set_value('Sélection de fonds / Architecture ouverte').run()
    assert not at.exception
    assert at.dataframe[0].value['Nom & prénom'].tolist() == ['Marie Dupont']


def test_export_selection_defaults_to_active_and_supports_multiple_or_all(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'export.sqlite3')
    ids = [storage.save_research(dict(profile=dict(nom=name, url=f'https://{name.lower()}.example'),
                                     contacts=[], diagnostic=[]))
           for name in ('Alpha', 'Beta', 'Gamma')]
    import csv
    import io
    import streamlit as st

    selections = []
    downloads = []
    export_contacts = storage.export_contacts
    download_button = st.download_button

    def record_download(label, data, *args, **kwargs):
        if kwargs.get('file_name') == 'societes_et_contacts.csv':
            downloads.append(list(csv.DictReader(io.StringIO(data.decode('utf-8-sig')), delimiter=';')))
        return download_button(label, data, *args, **kwargs)

    monkeypatch.setattr(st, 'download_button', record_download)

    def record_selection(selection):
        selections.append(selection)
        return export_contacts(selection)

    monkeypatch.setattr(storage, 'export_contacts', record_selection)
    at = AppTest.from_file('../app.py').run(timeout=20)
    at.radio[0].set_value('Contacts').run()
    assert not at.exception
    assert selections[-1] == [ids[-1]]
    at.multiselect(key='export_company_ids').set_value(ids[:2]).run()
    assert selections[-1] == ids[:2]
    assert [row['Société'] for row in downloads[-1]] == ['Alpha', 'Beta']
    all_companies = next(c for c in at.checkbox if c.label == 'Toutes les sociétés enregistrées')
    all_companies.check().run()
    assert selections[-1] is None
    assert [row['Société'] for row in downloads[-1]] == ['Alpha', 'Beta', 'Gamma']
    assert at.multiselect(key='export_company_ids').proto.disabled
    next(c for c in at.checkbox if c.label == all_companies.label).uncheck().run()
    at.multiselect(key='export_company_ids').set_value([]).run()
    assert selections[-1] == []
    assert next(b for b in at.get('download_button') if 'contacts des sociétés' in b.proto.label).proto.disabled
