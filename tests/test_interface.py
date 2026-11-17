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
