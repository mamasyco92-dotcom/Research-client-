"""Identification prudente de la société dont l'URL officielle est fournie.

Les titres de pages ne sont pas des raisons suffisantes pour affirmer une
identité. Priorité aux données structurées du site officiel et aux indications
explicites de son propriétaire; possibilité d'un nom fourni par l'utilisateur.
"""
from __future__ import annotations
import json
import re
import unicodedata
from urllib.parse import urlparse

ORG_TYPES = {'Organization','Corporation','LocalBusiness','FinancialService',
             'BankOrCreditUnion','InvestmentFund','NGO','ProfessionalService'}
GENERIC = re.compile(r'^(?:home|homepage|accueil|welcome|contact|about(?: us)?|our team|team|people|leadership|news|investments?|wealth management|private banking|asset management|services?|solutions?|portfolio management|client login|our company|legal|who we are)$',re.I)


def clean(v):
    return re.sub(r'\s+',' ',str(v or '')).strip(' \t\n\r—–|:-')


def fold(v):
    return ''.join(c for c in unicodedata.normalize('NFKD',clean(v).casefold()) if not unicodedata.combining(c))


def domain_label(domain):
    # Brand from host, NOT an assertion of legal corporate identity.
    host=(domain or '').removeprefix('www.').split('.')
    if len(host)>2 and host[-2] in {'co','com','org','net'}: root=host[-3]
    else:root=host[-2] if len(host)>=2 else host[0]
    return root


def plausible(name):
    s=clean(name)
    return bool(3<=len(s)<=100 and not GENERIC.fullmatch(s) and
                not re.search(r'^(?:https?://|www\.)',s,re.I) and
                not re.search(r'\b(?:cookies?|privacy policy|terms of use|all rights reserved)\b',s,re.I))


def domain_affinity(name, domain):
    n=re.sub('[^a-z0-9]','',fold(name))
    root=re.sub('[^a-z0-9]','',fold(domain_label(domain)))
    if not root or not n:return 0
    if n==root or n in root or root in n:return 20
    bits=[p for p in re.findall('[a-z0-9]+',fold(name)) if len(p)>=5]
    if any(p in root for p in bits):return 12
    # Domain and brand do not always match (holding company, acronyms).
    return 0


def jsonld_orgs(page):
    for script in page.soup.find_all('script',attrs={'type':'application/ld+json'}):
        try:
            data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):continue
        stack=[data]
        while stack:
            obj=stack.pop()
            if isinstance(obj,list):stack.extend(obj);continue
            if not isinstance(obj,dict):continue
            if isinstance(obj.get('@graph'),list):stack.extend(obj['@graph'])
            typ=obj.get('@type',[])
            types=typ if isinstance(typ,list) else [typ]
            types={str(t).split('/')[-1] for t in types}
            if not (types & ORG_TYPES):continue
            name=clean(obj.get('name',''))
            if not plausible(name):continue
            u=obj.get('url')
            if isinstance(u,dict):u=u.get('@id') or u.get('url')
            hostname=urlparse(str(u or '')).hostname or ''
            matches=not hostname or hostname==urlparse(page.url).hostname or hostname.endswith('.'+(urlparse(page.url).hostname or ''))
            if matches:
                yield name, 'Organisation JSON-LD (site officiel)', 95 if hostname else (62 if domain_affinity(name, urlparse(page.url).hostname or '') else 30)


def resolve_company(pages, domain, declared_name=''):
    if clean(declared_name):
        name=clean(declared_name)[:100]
        if not plausible(name):raise ValueError('Nom de société saisi invalide.')
        return {'nom':name,'niveau':'Nom fourni par utilisateur','origine':'Saisie manuelle',
                'detail':'Nom indiqué pour cibler les recherches; identité juridique non vérifiée.',
                'alternatives':[]}
    candidates=[]
    for page in pages[:5]:
        for name,source,score in jsonld_orgs(page):
            candidates.append((score+domain_affinity(name,domain),name,source))
        meta=page.soup.find('meta',attrs={'property':'og:site_name'})
        if meta:
            s=clean(meta.get('content'))
            if plausible(s):
                affinity=domain_affinity(s,domain)
                candidates.append(((72 if affinity else 38)+affinity,s,'og:site_name (site officiel)'))
        for x in ['application-name','apple-mobile-web-app-title']:
            tag=page.soup.find('meta',attrs={'name':x})
            if tag:
                s=clean(tag.get('content'))
                if plausible(s):
                    affinity=domain_affinity(s,domain)
                    candidates.append(((60 if affinity else 25)+affinity,s,'Métadonnée officielle'))
        raw=clean(page.soup.title.get_text(' ',strip=True) if page.soup.title else page.title)
        parts=[clean(q) for q in re.split(r'\s+[|–—-]\s+|\s*::\s*',raw)]
        for idx,p in enumerate(parts):
            if plausible(p):
                affinity=domain_affinity(p,domain)
                # Les titres sont faibles, et les rubriques génériques sont exclues.
                candidates.append((30+affinity+(5 if idx==len(parts)-1 else 0),p,'Titre de page (à vérifier)'))
    if candidates:
        candidates.sort(key=lambda x:(x[0],len(x[1])<60),reverse=True)
        score,name,source=candidates[0]
        level='Forte (site officiel)' if score>=90 else 'Moyenne — à confirmer' if score>=70 else 'Faible — à confirmer'
        alternatives=[]
        for _,v,_ in candidates[1:]:
            if fold(v)!=fold(name) and v not in alternatives: alternatives.append(v)
        return {'nom':name,'niveau':level,'origine':source,
                'detail':'Vérifier l’entité ou la division ciblée si le site représente un groupe.',
                'alternatives':alternatives[:5]}
    return {'nom':domain_label(domain).replace('-',' ').title(), 'niveau':'Faible — à confirmer',
            'origine':'Nom de domaine uniquement',
            'detail':'Aucune identité officielle fiable trouvée dans le HTML : saisis le nom exact pour les recherches externes.',
            'alternatives':[]}
