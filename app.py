"""Interface Streamlit - Prospection B2B, deux écrans : Société et Contacts."""
import csv
import io
import re
from urllib.parse import urlparse
import streamlit as st

from research import research, normalize_url
from storage import (companies, get_company, save_research, update_contact,
                     remove_contact, database_backup, export_contacts)
from contact_quality import shortlist, folded
from prospect_filters import TARGETS, filter_prospects

st.set_page_config(page_title='Recherche Prospects | Sycomore',page_icon='🔎',layout='wide')
st.markdown('''<style>
.block-container {max-width: 1280px;padding-top: 1.8rem}
[data-testid="stMetric"] {border:1px solid #e5e7eb;border-radius:12px;padding:12px}
div[data-testid="stButton"] button[kind="primary"] {background:#123f36;border-color:#123f36}
.small-note {font-size:.88rem;color:#64748b}
</style>''',unsafe_allow_html=True)

PROFILE_FIELDS=[
 ('nom','Société'),('pays','Pays / présence'),('type_acteur','Type de client'),
 ('encours','Encours / AUM'),('architecture','Architecture ouverte'),
 ('ucits_externes','Fonds UCITS externes'),('alternatives','Stratégies alternatives'),
 ('selection_fonds','Sélection des fonds'),('strategies','Stratégies suivies')]
CONTACT_VIEW=['nom','fonction','email','telephone','role_decisionnel','priorite','source','statut']
CONTACT_LABELS={'nom':'Nom & prénom','fonction':'Fonction','email':'Email pro','telephone':'Téléphone pro',
                'role_decisionnel':'Rôle décisionnel','priorite':'Priorité','source':'Source publique','statut':'Statut'}


def csv_bytes(rows: list[dict],columns: list[str],labels:dict|None=None) -> bytes:
    out=io.StringIO(newline='')
    writer=csv.writer(out,delimiter=';',quoting=csv.QUOTE_MINIMAL)
    writer.writerow([labels.get(c,c) if labels else c for c in columns])
    def safe_cell(value):
        text = str(value if value is not None else '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
    for item in rows:writer.writerow([safe_cell(item.get(c,'')) for c in columns])
    return ('\ufeff'+out.getvalue()).encode('utf-8')


def presentation_shortlist(people: list[dict], focus: str = 'wealth_asset') -> list[dict]:
    """Filtre non destructif : SQLite reste intacte, y compris les corrections manuelles."""
    clean = shortlist(people, focus)
    return [{**c, 'name':c['nom'], 'role':c['fonction'],
             'priority':c['priorite'], 'decision':c['role_decisionnel'],
             'status':c.get('statut', 'À vérifier')} for c in clean]


def evidence(profile,key):
    evidence=profile.get('preuves',{}).get(key,[])
    if not evidence:return
    with st.expander('Voir les preuves et sources',expanded=False):
        for item in evidence[:4]:
            st.write('“'+item.get('extrait','')+'”')
            st.caption(item.get('url',''))
            if item.get('url'):st.link_button('Ouvrir la source',item['url'])


def display_company(profile, contacts, diagnostic):
    st.subheader(profile.get('nom','Société'))
    st.caption(profile.get('url','')+'  ·  Dernière recherche : '+profile.get('date_recherche',''))
    identity=profile.get('identification',{})
    if identity:
        st.info('Identification de la société : **'+identity.get('niveau','À vérifier')+'** — '+identity.get('origine','')+'. '+identity.get('detail',''))
    c1,c2,c3,c4=st.columns(4)
    good = presentation_shortlist(contacts)
    c1.metric('Contacts qualifiés',len(good))
    c2.metric('Contacts prioritaires',sum(1 for c in good if c['priority']=='Haute'))
    c3.metric('Pistes Web à vérifier',len(profile.get('pistes_web',profile.get('pistes_linkedin',[]))))
    c4.metric('Sources officielles citées',len({item['url'] for v in profile.get('preuves',{}).values() for item in v}))
    st.markdown('#### Profil commercial')
    grid=[st.columns(2) for _ in range((len(PROFILE_FIELDS)+1)//2)]
    for i,(key,label) in enumerate(PROFILE_FIELDS):
        with grid[i//2][i%2]:
            with st.container(border=True):
                st.markdown(f'**{label}**')
                st.write(profile.get(key,'À confirmer') or 'À confirmer')
                evidence(profile,key)
    st.markdown('#### Personnes repérées')
    candidates=profile.get('pistes_web',profile.get('pistes_linkedin',[]))
    if candidates:
        st.info(f"{len(candidates)} piste(s) Web trouvée(s). Consulte l’onglet **Contacts** pour afficher les personnes, filtrer par fonction et les exporter avec leur statut de vérification.")
    else:
        st.caption('Aucune piste Web exploitable dans les résultats reçus. Consulte le diagnostic pour comprendre les limites de la recherche.')
    st.markdown('#### Sources externes et bases de données')
    st.caption('Ces liens sont des pistes à contrôler : aucune association nom/fonction n’est considérée comme prouvée par un simple résultat de recherche.')
    lei=profile.get('registre_lei',[])
    if lei:
        st.write('**GLEIF — registre international des entités juridiques (candidats, pas des correspondances certifiées)**')
        st.dataframe(lei,hide_index=True,width='stretch',
            column_config={'source':st.column_config.LinkColumn('Fiche GLEIF',display_text='Consulter')})
    else:
        st.info('Aucune entité candidate LEI trouvée ou service indisponible. Cela ne signifie pas que la société n’existe pas.')
    links=profile.get('liens_recherche',{})
    if links:
        with st.expander('Recherches à ouvrir (LinkedIn, rapports, annuaires)',expanded=True):
            cols=st.columns(2)
            for i,(label,href) in enumerate(links.items()):
                cols[i%2].link_button(label,href,use_container_width=True)
            st.caption('Les profils LinkedIn doivent être vérifiés manuellement ; aucune extraction automatique de LinkedIn.')
    refs=profile.get('sources_externes',[])
    if refs:
        with st.expander(f'Pages publiques externes trouvées ({len(refs)})'):
            for r in refs:
                st.markdown('**'+r['titre']+'**')
                st.caption(r['type']+' — '+r.get('extrait',''))
                st.link_button('Ouvrir la source',r['url'])

    with st.expander('Diagnostic de recherche'):
        for msg in diagnostic:st.write('• '+msg)
        st.caption('Une absence de résultat ne signifie pas absence réelle. Les montants et fonctions doivent être vérifiés dans les sources.')
    st.download_button('Exporter la fiche société en CSV',data=csv_bytes([{k:profile.get(k,'') for k,_ in PROFILE_FIELDS}],
              [k for k,_ in PROFILE_FIELDS],{k:label for k,label in PROFILE_FIELDS}),
              file_name='societe_prospection.csv',mime='text/csv')



def unified_contact_rows(registered: list[dict], candidates: list[dict], focus: str,
                         selection: str = 'Tous les profils') -> list[dict]:
    """Une seule liste : contacts enregistrés + pistes Web non vérifiées.

    Aucune piste Web n'est ajoutée à SQLite ni considérée comme confirmée.
    Les corrections manuelles des contacts enregistrés restent prioritaires.
    """
    if selection == 'Shortlist qualifiée':
        registered = presentation_shortlist(registered, focus)
    rows=[]
    seen=set()
    if selection != 'Pistes Web à vérifier':
        for p in registered:
            name=p.get('name','')
            key=folded(name)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append({
                'nom':name,'fonction':p.get('role',''),
                'email':p.get('email',''),'telephone':p.get('phone',''),
                'role_decisionnel':p.get('decision',''),
                'priorite':p.get('priority','À qualifier'),
                'source':p.get('source',''),
                'statut':p.get('status','À vérifier'),
                'origine':'Contact enregistré','confiance':'',
            })
    if selection in ('Tous les profils','Pistes Web à vérifier'):
        for p in candidates:
            name=p.get('nom','')
            key=folded(name)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append({
                'nom':name,'fonction':p.get('fonction','Fonction à confirmer'),
                'email':'','telephone':'','role_decisionnel':'',
                'priorite':p.get('priorite','À qualifier'),
                'source':p.get('source',''),
                'statut':p.get('statut','Association et poste à vérifier'),
                'origine':'Piste Web','confiance':p.get('confiance','À vérifier'),
            })
    rank={'Haute':0,'Moyenne':1,'À qualifier':2}
    return sorted(rows,key=lambda p:(rank.get(p['priorite'],3),folded(p['nom'])))

st.sidebar.markdown('## 🔎 Sycomore Research')
st.sidebar.caption('Recherche publique B2B')
menu=st.sidebar.radio('Navigation',['Société','Contacts'],label_visibility='collapsed')

def _on_company_change(widget_key):
    # Synchroniser l'identité réellement affichée entre Société et Contacts.
    st.session_state.active_id = st.session_state[widget_key]

entries=companies()
if 'active_id' not in st.session_state:st.session_state.active_id=entries[0]['id'] if entries else None

if menu=='Société':
    st.title('Profil société')
    st.write('Renseigne le site officiel pour identifier les critères commerciaux essentiels et les contacts pertinents.')
    # Éviter qu'un nom de société de la recherche précédente serve de nom au nouveau domaine.
    with st.form('form_recherche',clear_on_submit=True):
        url=st.text_input('URL officielle de la société',value='',placeholder='',help='Saisis l’URL officielle de la société.')
        company_name=st.text_input('Nom exact de la société (facultatif)',value='',placeholder='À utiliser si le site regroupe plusieurs entités',help='Sans nom saisi, le programme utilise les métadonnées officielles; ce champ évite de rechercher sous un nom erroné.')
        focus_option=st.selectbox('Périmètre des contacts', ['Gestion d’actifs / Wealth Management (recommandé)', 'Toutes les activités du groupe'], index=0)
        api_key=st.text_input('Clé API Serper pour Google (facultatif)',type='password',
              help='Sans clé : recherche DDGS. Avec clé Serper : requêtes Google via API autorisée. Clé utilisée uniquement pour cette recherche, non enregistrée dans SQLite.')
        st.caption('Google via API externe : nécessite une clé Serper. Sans clé, DDGS peut renvoyer moins de personnes que Google dans Chrome.')
        submitted=st.form_submit_button('Lancer la recherche approfondie',type='primary')
    search_failed=False
    if submitted:
        try:
            valid=normalize_url(url)
            with st.spinner('Analyse des pages publiques et recherche des interlocuteurs…'):
                progress_bar = st.progress(0, text='Préparation de la recherche')
                result=research(valid, progress=lambda value, message: progress_bar.progress(value, text=message), focus='all' if focus_option.startswith('Toutes') else 'wealth_asset', company_name=company_name, serper_api_key=api_key)
                cid=save_research(result)
            st.session_state.active_id=cid
            st.session_state.selected_company_society=cid
            st.session_state.selected_company_contacts=cid
            st.success(f"Recherche enregistrée : {len(result['contacts'])} contacts du moteur et "
                       f"{len(result['profile'].get('pistes_web',[]))} pistes Web à vérifier.")
        except Exception as exc:
            search_failed=True
            st.error(f'La recherche n’a pas abouti : {exc}')
            st.info('Certaines sociétés bloquent les robots. Dans ce cas, les informations doivent être vérifiées manuellement sur les liens publics.')
    entries=companies()
    if entries and search_failed:
        st.warning('La nouvelle recherche a échoué : aucune nouvelle fiche affichée ou enregistrée. Tes anciens prospects restent conservés dans SQLite.')
    elif entries:
        ids=[e['id'] for e in entries]
        current=st.session_state.active_id if st.session_state.active_id in ids else ids[0]
        if st.session_state.get('selected_company_society') != current:
            st.session_state.selected_company_society=current
        choice=st.selectbox('Résultats enregistrés',ids,
            key='selected_company_society',on_change=_on_company_change,
            args=('selected_company_society',),
            format_func=lambda i:next(e['name']+' — '+(urlparse(e['url']).hostname or '')
                for e in entries if e['id']==i))
        profile,people,diag=get_company(choice)
        st.caption('Fiche actuellement affichée : **'+profile.get('nom','')+'** · '+profile.get('url',''))
        display_company(profile,people,diag)
    else:
        st.info('Aucune société enregistrée. La première recherche créera automatiquement sa fiche et sa liste de contacts.')

else:
    st.title('Contacts & décideurs')
    st.write('Personnes repérées dans les sources publiques, classées selon leur rôle potentiel dans la sélection et la gestion des fonds.')
    if not entries:
        st.info('Commence par une recherche dans l’interface Société.')
    else:
        ids=[e['id'] for e in entries]
        current=st.session_state.active_id if st.session_state.active_id in ids else ids[0]
        if st.session_state.get('selected_company_contacts') != current:
            st.session_state.selected_company_contacts=current
        choice=st.selectbox('Société',ids,
            key='selected_company_contacts',on_change=_on_company_change,
            args=('selected_company_contacts',),
            format_func=lambda i:next(e['name']+' — '+(urlparse(e['url']).hostname or '')
                for e in entries if e['id']==i))
        profile,all_people,diag=get_company(choice)
        st.caption('Contacts affichés pour **'+profile.get('nom','')+'** · '+profile.get('url',''))
        candidates=profile.get('pistes_web', profile.get('pistes_linkedin', []))
        st.caption('Tous les profils associés à cette société sont visibles ici, même si leur poste reste à vérifier. Les pistes Web ne sont pas automatiquement des contacts confirmés.')
        st.markdown('### Personnes associées à la société')
        c1, c2, c3 = st.columns(3)
        c1.metric('Contacts enregistrés', len(all_people))
        c2.metric('Pistes Web', len(candidates))
        c3.metric('Contacts qualifiés', len(presentation_shortlist(all_people)))
        selection=st.radio('Afficher', [
            'Tous les profils', 'Contacts enregistrés',
            'Pistes Web à vérifier', 'Shortlist qualifiée'
        ],horizontal=True)
        scope=st.selectbox('Périmètre commercial',['Gestion d’actifs / Wealth Management','Toutes les activités'])
        focus='all' if scope == 'Toutes les activités' else 'wealth_asset'
        rows=unified_contact_rows(all_people, candidates, focus, selection)
        a,b,c=st.columns([2,1,1])
        q=a.text_input('Chercher un nom ou une fonction', value='', placeholder='')
        priority=b.selectbox('Priorité',['Toutes','Haute','Moyenne','À qualifier'])
        source_filter=c.selectbox('Origine',['Toutes','Contact enregistré','Piste Web'])
        target_col, coordinate_col = st.columns(2)
        target = target_col.selectbox('Interlocuteurs recherchés', list(TARGETS))
        coordinates = coordinate_col.selectbox('Coordonnées disponibles', ['Toutes', 'Avec email', 'Avec email ou téléphone'])
        st.caption('Les filtres métier reposent sur la fonction publiée. Ils ne prouvent pas que la personne achète des fonds externes.')
        hide_former = st.checkbox('Masquer les pistes mentionnant un ancien poste', value=True)
        rows = filter_prospects(rows, target, coordinates)
        if hide_former:
            rows = [r for r in rows if 'ancien' not in folded(r.get('statut', ''))]
        filtered=[]
        for item in rows:
            if q and folded(q) not in folded(' '.join((item['nom'],item['fonction'],item['role_decisionnel']))):
                continue
            if priority!='Toutes' and item['priorite']!=priority:
                continue
            if source_filter!='Toutes' and item['origine']!=source_filter:
                continue
            filtered.append(item)
        st.caption(f'{len(filtered)} personne(s) affichée(s). Les postes et employeurs des pistes Web restent à confirmer; aucun email ou téléphone n’est deviné.')
        columns=['nom','fonction','origine','priorite','statut','email','telephone','source','confiance','role_decisionnel']
        labels={**CONTACT_LABELS,'origine':'Origine','confiance':'Confiance'}
        st.dataframe([{labels.get(col,col):row.get(col,'') for col in columns} for row in filtered],
            hide_index=True, width='stretch',
            column_config={'Source publique':st.column_config.LinkColumn('Source publique',display_text='Ouvrir')})
        st.download_button('Exporter les personnes affichées (CSV Excel)',
                           csv_bytes(filtered,columns,labels),
                           file_name='contacts_et_pistes_web.csv',mime='text/csv')
        with st.expander('Exporter les contacts par société'):
            export_all = st.checkbox('Toutes les sociétés enregistrées')
            company_names = {entry['id']: f"{entry['name']} — {urlparse(entry['url']).hostname}"
                             for entry in entries}
            selected_ids = st.multiselect(
                'Sociétés à exporter', ids, default=[choice], disabled=export_all,
                format_func=company_names.__getitem__, key='export_company_ids',
            )
            if export_all:
                selected_ids = None
            st.caption('Le CSV inclut les contacts enregistrés. Les sociétés sans contact ont une ligne dédiée.')
            st.download_button(
                'Télécharger les contacts des sociétés (CSV Excel)',
                csv_bytes(export_contacts(selected_ids), ['societe', *CONTACT_VIEW],
                          {'societe': 'Société', **CONTACT_LABELS}),
                file_name='societes_et_contacts.csv', mime='text/csv',
                disabled=selected_ids == [],
            )
        if candidates:
            with st.expander('Lire les extraits des résultats Web et vérifier les sources'):
                st.dataframe([{'Nom':x.get('nom',''), 'Fonction indicative':x.get('fonction',''),
                        'Extrait indexé':x.get('extrait',''), 'Source':x.get('source','')}
                       for x in candidates],hide_index=True,width='stretch',
                       column_config={'Source':st.column_config.LinkColumn('Source',display_text='Ouvrir')})
        with st.expander('Rechercher d’autres contacts sur LinkedIn et le Web'):
            st.caption('Liens de recherche manuelle ; aucun compte LinkedIn utilisé automatiquement.')
            for label,link in profile.get('liens_recherche',{}).items():
                st.link_button(label,link)
        people=all_people
        st.markdown('#### Fiche contact : corriger et enrichir')
        if not people:
            st.info('Aucun contact enregistré pour modification. Les pistes Web restent visibles dans le tableau ci-dessus et peuvent être vérifiées à leur source.')
        else:
            to_edit=st.selectbox('Personne', [r['id'] for r in people],format_func=lambda ident:next(r['name']+' — '+r['role'][:65] for r in people if r['id']==ident))
            item=next(r for r in people if r['id']==to_edit)
            with st.form('edit_contact'):
                left,right=st.columns(2)
                name=left.text_input('Nom & prénom',item['name'])
                role=right.text_input('Fonction',item['role'])
                email=left.text_input('Email public / confirmé',item['email'])
                phone=right.text_input('Téléphone public / confirmé',item['phone'])
                decision=left.text_input('Rôle dans la décision',item['decision'])
                prior=right.selectbox('Priorité',['Haute','Moyenne','À qualifier'],index=['Haute','Moyenne','À qualifier'].index(item['priority']) if item['priority'] in ['Haute','Moyenne','À qualifier'] else 2)
                status_edit=st.text_input('Statut de vérification',item['status'])
                save=st.form_submit_button('Enregistrer les corrections',type='primary')
            if save:
                update_contact(to_edit,{'name':name,'role':role,'email':email,'phone':phone,'decision':decision,'priority':prior,'status':status_edit})
                st.success('Corrections enregistrées dans la base locale.');st.rerun()
            with st.expander('Justificatif et source de cette personne'):
                st.write(item['evidence'] or 'Aucun extrait disponible.')
                if item['source']:st.link_button('Ouvrir la source',item['source'])
            with st.expander('Retirer un faux positif'):
                confirm_delete = st.checkbox('Confirmer le retrait de ce contact', key=f'delete_{to_edit}')
                if st.button('Supprimer cette personne de la liste',type='secondary', disabled=not confirm_delete):
                    remove_contact(to_edit);st.rerun()

st.sidebar.divider()
st.sidebar.caption('Stockage local SQLite · Pas de modification de ton fichier .xlsm · Données publiques uniquement')

with st.sidebar.expander('Sauvegarde des données'):
    st.caption('Le fichier contient les sociétés et contacts enregistrés. Conservez-le dans un emplacement privé.')
    if st.button('Préparer une sauvegarde SQLite'):
        st.download_button('Télécharger la sauvegarde', database_backup(), file_name='recherches_sauvegarde.sqlite3', mime='application/octet-stream')
