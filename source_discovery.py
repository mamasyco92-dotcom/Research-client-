"""Sources complémentaires publiques. Aucune connexion ni extraction de LinkedIn.
Les résultats de moteurs de recherche et du registre LEI sont des PISTES.
"""
from urllib.parse import quote_plus, urlparse
import re
import unicodedata
import requests

GLEIF_ENDPOINT = 'https://api.gleif.org/api/v1/lei-records'


def _fold(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').casefold())
                   if not unicodedata.combining(c))


def external_links(company: str, domain: str) -> dict:
    # Liens conçus pour un clic humain ; aucun robot ne visite LinkedIn.
    queries={
        'LinkedIn via Google': f'site:linkedin.com/in/ "{company}" ("fund selection" OR "CIO" OR "asset allocation")',
        'Annuaires / équipes': f'"{company}" ("investment team" OR "fund selector" OR "portfolio manager")',
        'Documents et rapports': f'"{company}" ("annual report" OR "AUM" OR "assets under management") filetype:pdf',
        'Architecture ouverte': f'"{company}" ("third party funds" OR "open architecture" OR "fonds tiers")',
    }
    return {k:'https://www.google.com/search?q='+quote_plus(v) for k,v in queries.items()}


def classify_hits(hits: list[dict], company: str, domain: str) -> list[dict]:
    """Liens consultables : aucune personne/coordonnée n'est déduite du titre."""
    seen=set();res=[]
    folded=_fold(company)
    for hit in hits:
        url=str(hit.get('url') or '')
        if url in seen or urlparse(url).scheme not in ('https','http'):continue
        host=(urlparse(url).hostname or '').lower()
        if not host or (host == domain or host.endswith('.' + domain)) or host=='www.'+domain: continue
        title=str(hit.get('title') or '')[:170]
        snippet=str(hit.get('snippet') or '')[:240]
        # Exclure les résultats sans indication de la société cible.
        if folded not in _fold(title+' '+snippet) and domain not in _fold(title+' '+snippet):continue
        typ='LinkedIn — vérification manuelle' if (host == 'linkedin.com' or host.endswith('.linkedin.com')) else 'Source Web externe — à vérifier'
        res.append({'type':typ,'titre':title,'url':url,'extrait':snippet})
        seen.add(url)
        if len(res)>=15:break
    return res


def search_lei(name: str, logs: list[str]) -> list[dict]:
    """Recherche des candidats GLEIF, jamais une identification certaine.

    Un résultat LEI ne prouve ni architecture ouverte ni AUM.
    """
    try:
        response=requests.get(GLEIF_ENDPOINT,
                params={'filter[entity.legalName]':name,'page[size]':5},
                headers={'Accept':'application/vnd.api+json','User-Agent':'SycomoreResearch/3.0 (public LEI lookup)'},
                timeout=(4,9))
        response.raise_for_status()
        payload=response.json()
        output=[]
        for row in payload.get('data',[])[:5]:
            lei=row.get('id','')
            ent=(row.get('attributes') or {}).get('entity') or {}
            raw_name=ent.get('legalName') or {}
            legal=raw_name.get('name','') if isinstance(raw_name,dict) else str(raw_name)
            if not legal or not re.fullmatch(r'[A-Z0-9]{20}',lei):continue
            # N'affirmer aucune équivalence juridique sans validation humaine.
            output.append({'nom':legal,'lei':lei,
                'pays':(ent.get('legalAddress') or {}).get('country',''),
                'source':f'https://api.gleif.org/api/v1/lei-records/{lei}',
                'statut':'Candidat à vérifier'})
        logs.append(f'{len(output)} entité(s) candidate(s) dans le registre LEI GLEIF.')
        return output
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logs.append('Registre GLEIF indisponible : '+str(exc)[:110])
        return []



def indexed_linkedin_candidates(hits: list[dict], company: str, limit: int = 25) -> list[dict]:
    """Pistes issues des métadonnées des résultats d'un moteur public.

    N'effectue AUCUNE requête vers LinkedIn et ne présente JAMAIS une piste
    comme une personne dont l'emploi actuel serait confirmé.
    """
    from contact_quality import person_name, role_label, classify

    output = []
    seen = set()
    company_norm = _fold(company)
    for hit in hits:
        url = str(hit.get('url', ''))
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host != 'linkedin.com' and not host.endswith('.linkedin.com'):
            continue
        if not re.match(r'^/in/[^/]+', parsed.path, re.I):
            continue  # ni pages entreprise, ni annuaires, ni résultats de recherche
        title = re.sub(r'\s+', ' ', str(hit.get('title') or '')).strip()
        snippet = re.sub(r'\s+', ' ', str(hit.get('snippet') or '')).strip()
        combined = title + ' ' + snippet
        if not company_norm or company_norm not in _fold(combined):
            continue

        # Le nom doit apparaître dans l'en-tête du résultat : jamais déduit
        # des personnes éventuellement citées dans la description.
        head = re.split(r'\s+[|–—-]\s+', title, maxsplit=1)[0].strip()
        head = re.sub(r'^(?:LinkedIn\s*[-|]\s*)', '', head, flags=re.I)
        name = person_name(head)
        if not name:
            continue
        # La fonction reste indicative même si elle figure explicitement
        # dans le titre ou l'extrait indexé.
        tail = title[len(head):]
        role = role_label(tail[:180]) or role_label(snippet[:300])
        if not role:
            continue
        # L'employeur éventuellement ajouté par le moteur n'est pas une partie
        # de l'intitulé du poste : conserver uniquement la fonction.
        role = re.split(r'\s+[-–—|]\s+|\s+(?:at|chez)\s+(?=[A-ZÀ-ÖØ-Þ])', role, maxsplit=1, flags=re.I)[0].strip()
        priority, _ = classify(role)
        if priority == 'À qualifier':
            continue
        key = _fold(name) + ':' + _fold(company)
        if key in seen:
            continue
        seen.add(key)
        output.append({
            'nom': name,
            'fonction': role,
            'priorite': priority,
            'source': url,
            'extrait': snippet[:300],
            'statut': 'Piste LinkedIn indexée — à vérifier',
        })
        if len(output) >= limit:
            break
    return sorted(output, key=lambda x: (x['priorite'] != 'Haute', x['nom']))


# ---- V4.2 : personnes visibles dans les résultats de recherche Web ----

_MANAGEMENT_ROLE = re.compile(
    r'\b(?:co[- ]?founder|founder|fondatrice?|cofondatrice?|'
    r'chief executive officer|\bCEO\b|managing director|'
    r'chief operating officer|\bCOO\b|president|président(?:e)?|'
    r'head of (?:wealth|private bank|advisory)|'
    r'head of (?:sales|investor relations))\b', re.I)
_FORMER_LINK = re.compile(r'\b(?:former|formerly|previously|ex[- ]|ancien(?:ne)?|a quitté|past role)\b', re.I)
_RELATION = re.compile(r'\b(?:at|chez|with|works? at|working at|employed by|of|pour|au sein de|current(?:ly)? at)\s+', re.I)


def _person_from_title(title: str) -> tuple[str,str]:
    """N'extrait que le nom au DÉBUT d'un titre de résultat.

    Aucun balayage de paragraphes qui fusionnerait des collaborateurs.
    """
    from contact_quality import person_name
    txt = re.sub(r'\s+', ' ', title or '').strip()
    txt = re.sub(r'^LinkedIn\s*[-|]\s*', '', txt, flags=re.I)
    prefix = re.split(r'\s+[-–—|•]\s+|\s+\(LinkedIn\)', txt, maxsplit=1)[0].strip()
    prefix = re.sub(r'\s+\([^)]{2,80}\)$', '', prefix).strip()
    name = person_name(prefix)
    return (name, txt[len(prefix):]) if name else ('','')


def indexed_people_candidates(hits: list[dict], company: str, limit: int = 40) -> list[dict]:
    """Personnes potentiellement associées à une société dans des extraits indexés.

    Pas de requête vers LinkedIn, pas de coordonnées inférées, pas de certitude
    sur l'employeur ou la date. Conserve aussi des personnes sans rôle ciblé.
    """
    from contact_quality import role_label, classify, folded

    company_n = _fold(company)
    if len(company_n) < 4:
        return []
    candidates: dict[str,dict] = {}
    for hit in hits:
        url = str(hit.get('url') or '')
        parsed = urlparse(url)
        hostname = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http','https') or not hostname:
            continue
        is_linkedin = hostname == 'linkedin.com' or hostname.endswith('.linkedin.com')
        if is_linkedin and not re.match(r'^/in/[^/]+/?',parsed.path,re.I):
            continue
        if not is_linkedin and any(hostname.endswith(s) for s in
                ('.facebook.com','.instagram.com','.tiktok.com','.pinterest.com')):
            continue
        title = re.sub(r'\s+',' ',str(hit.get('title') or '')).strip()
        snippet = re.sub(r'\s+',' ',str(hit.get('snippet') or '')).strip()
        if company_n not in _fold(title+' '+snippet):
            continue
        name, tail = _person_from_title(title)
        if not name or company_n in _fold(name):
            continue
        # Écarte les titres annonçant explicitement un AUTRE employeur,
        # même si un article évoque la société demandée ailleurs.
        title_before_linkedin = re.split(r'\s+[|–—-]\s+LinkedIn\s*$',title,maxsplit=1,flags=re.I)[0]
        explicit_other = re.search(r'\b(?:at|chez|@)\s+([A-Z][^|–—]{2,60})',title_before_linkedin)
        if explicit_other and company_n not in _fold(explicit_other.group(1)):
            continue
        company_in_title = company_n in _fold(title)
        # Sans mention dans le titre, requiert une relation employeur explicite
        # dans un extrait court, pour éviter les biographies hors cible.
        context = snippet[:230]
        direct_link = bool(re.search(
            r'\b(?:at|chez|with|works? (?:at|for)|working at|employed by|'
            r'of|au sein de|pour)\s+'+re.escape(company)+r'\b',context,re.I))
        # Ex. "Jane Doe · Moneikos · Portfolio Manager" dans l'extrait.
        snippet_subject = _fold(name) in _fold(context[:100])
        company_early = company_n in _fold(context[:160])
        if not (company_in_title or direct_link or (snippet_subject and company_early)):
            continue
        role = role_label(tail[:150]) or role_label(context[:160])
        if not role:
            management = _MANAGEMENT_ROLE.search(tail[:160]) or _MANAGEMENT_ROLE.search(context[:190])
            role = management.group(0) if management else 'Fonction à confirmer'
        priority,_ = classify(role)
        is_former = bool(_FORMER_LINK.search((tail+' '+context)[:240]))
        confidence = 'Indice fort' if company_in_title and role != 'Fonction à confirmer' else 'Indice à vérifier'
        status = 'Lien professionnel ancien possible — à vérifier' if is_former else 'Association et poste à vérifier'
        origin = 'LinkedIn indexé (sans accès au profil)' if is_linkedin else 'Autre résultat Web public'
        item = {'nom':name,'fonction':role,'priorite':priority,'source':url,
                'origine':origin,'confiance':confidence,'statut':status,
                'extrait':snippet[:360] or title[:250]}
        key = folded(name)
        old = candidates.get(key)
        if old is None:
            candidates[key] = item
        else:
            def score(c):
                return ('ancien' not in c['statut'],
                        c['fonction']!='Fonction à confirmer',
                        c['confiance']=='Indice fort',
                        c['priorite']=='Haute',
                        c['origine'].startswith('LinkedIn'))
            if score(item)>score(old):candidates[key]=item
    return sorted(candidates.values(),
                  key=lambda x:(x['priorite']!='Haute',x['priorite']=='À qualifier',
                                x['confiance']!='Indice fort',x['nom']))[:limit]
