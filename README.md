# Sycomore Research Web — V2 (qualité des contacts)

Deux interfaces dans ton navigateur : **Société** et **Contacts**. Projet Python pour VS Code sous Windows.

## Mettre à jour ton dossier actuel (recommandé)

1. Arrête les anciens serveurs Streamlit (`Ctrl+C` dans chaque terminal VS Code).
2. **Conserve et sauvegarde ton dossier existant** `Sycomore_Research_Web`, en particulier `recherches.sqlite3` : c'est ta base de données locale.
3. Dans le ZIP V2, copie les **trois fichiers** `research.py`, `app.py`, `contact_quality.py` dans ton dossier existant, en remplaçant uniquement `research.py` et `app.py`. Ne touche pas à `storage.py`, à `recherches.sqlite3` ni à ton classeur `.xlsm`.
4. Pour un démarrage par raccourci VS Code, copie aussi le dossier `.vscode` du ZIP (il contient `tasks.json`).
5. Ouvre VS Code sur `Sycomore_Research_Web`, puis appuie sur **Ctrl+Maj+B** (Ctrl+Shift+B) et sélectionne **Démarrer Sycomore Research V2** si demandé.
6. Ouvre `http://localhost:8501`, ou le port indiqué dans le terminal si 8501 est occupé.

## Lancement manuel (si besoin)

```powershell
& "C:\Users\marius.artur\AppData\Local\Python\pythoncore-3.15-64\python.exe" -m streamlit run app.py
```

Installation éventuelle des dépendances :

```powershell
& "C:\Users\marius.artur\AppData\Local\Python\pythoncore-3.15-64\python.exe" -m pip install -r requirements.txt
```

## Changements V2

- `contact_quality.py` : contrôle que le nom ressemble à un vrai nom propre ; élimine des intitulés parasites comme « Open Architecture » et « Outlook July » ; retire les titres collés au nom ; normalise les fonctions ; ne retient par défaut que des fonctions liées à l'investissement, l'allocation ou la sélection de fonds ; dédoublonne sans créer de coordonnées.
- `research.py` : extraction HTML moins permissive ; rôle récupéré uniquement dans une carte/personne locale ; filtrage des rubriques de M&A, Global Advisory et Private Equity lorsque le périmètre sélectionné est « Gestion d’actifs / Wealth Management ».
- `app.py` : deux modes dans Contacts — « Shortlist qualifiée » (par défaut) et « Tous les contacts enregistrés » ; choix du périmètre commercial ; maintien du lien source et des exports CSV.
- `.vscode/tasks.json` : lancement de l'application depuis VS Code avec **Ctrl+Shift+B** sans avoir à taper une commande.

## À savoir

- **Le filtre Shortlist ne supprime rien de SQLite**, même les anciens faux positifs : tu peux revenir à « Tous les contacts enregistrés ». Une nouvelle recherche ajoute ou met à jour les résultats, mais ne purge pas automatiquement les anciens.
- Le filtrage repose sur des règles conservatrices : il peut manquer des contacts pertinents et peut nécessiter des corrections manuelles. Une mention « Source officielle » indique l'origine de l'information, **pas une vérification actuelle de la fonction**.
- Une société d'asset management ne représente pas automatiquement un acheteur de fonds UCITS externes : vérifier séparément son architecture ouverte et les responsabilités réelles de la personne.
- Les emails et téléphones professionnels restent vides lorsqu'aucun numéro ou email public n'est identifié ; aucune donnée n'est devinée.
- En cas de site bloqué, aucune donnée ne sera forcée.
- Respecter les règles de prospection professionnelle, la politique des sites et les exigences de protection des données. SQLite est stockée localement sans chiffrement.

## Tests techniques

Dans le dossier du projet :

```powershell
& "C:\Users\marius.artur\AppData\Local\Python\pythoncore-3.15-64\python.exe" -m pytest -q
```

Les tests de développement peuvent nécessiter `pytest`, qui n'est pas requis pour exécuter Streamlit.

## Améliorations de cette version

- Contacts : filtre par sélection de fonds / architecture ouverte, direction des investissements ou gestion / allocation ; filtre des coordonnées disponibles ; masquage facultatif des pistes mentionnant un ancien poste. Ces critères ne prouvent pas un pouvoir d'achat.
- Collecte : lecture des liens `mailto:` et `tel:` dans la carte d'une seule personne. Plusieurs coordonnées différentes restent à vérifier et ne sont pas attribuées automatiquement.
- Recherche : trois requêtes Web simultanées au maximum, suppression des requêtes identiques, progression par étape et diagnostic des chargements échoués et exclusions robots.txt.
- Stockage : dédoublonnage des nouveaux résultats avec normalisation des accents ; conservation des corrections manuelles ; sauvegarde SQLite cohérente depuis la barre latérale. Les doublons historiques ne sont pas supprimés automatiquement.
- Export : protection des cellules CSV commençant par des caractères interprétés comme une formule Excel ; confirmation avant suppression d'un contact.

### Développement Linux / cloud

Depuis le dossier du projet :

```bash
python3 -m venv /workspace/venvs/research
/workspace/venvs/research/bin/python -m pip install -r requirements.txt pytest
/workspace/venvs/research/bin/python -m pytest -q
/workspace/venvs/research/bin/python -m streamlit run app.py --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
```

Le cloud est déjà isolé : utiliser le checkout existant, sans créer de worktree.
La base `recherches.sqlite3` reste locale et est exclue de Git. Sauvegarder la base existante avant une mise à jour ; copier les fichiers de code et le nouveau module `prospect_filters.py`, jamais remplacer la base par une base de démonstration.

Les tests utilisent des bases temporaires et des réponses Web simulées. Ils vérifient le moteur, la séparation des personnes, le stockage et le filtrage de l'interface ; ils ne mesurent pas la couverture réelle des moteurs de recherche. La recherche réelle nécessite les accès réseau aux moteurs, à GLEIF et aux domaines des sociétés ciblées. Serper reste facultatif. Les pages nécessitant JavaScript et les PDF uniquement numérisés (images) ne sont pas analysés.


### Export des sociétés et collecte des coordonnées

- Dans **Contacts**, ouvre **Exporter les contacts par société**. Choisis une ou plusieurs sociétés, ou coche **Toutes les sociétés enregistrées**. Le CSV Excel indique la société sur chaque ligne, conserve une ligne pour les sociétés sans contact et exporte les contacts enregistrés indépendamment des filtres à l’écran.
- La recherche peut suivre les fiches nominatives et lire les PDF textuels pertinents du site officiel avec pypdf, sans clé ni service payant. Elle analyse jusqu’à six PDF, dans la limite de douze pages par document. Elle conserve les coordonnées publiques pouvant être rattachées au nom et à la fonction, ainsi que la source (page du PDF lorsqu’elle est disponible).
