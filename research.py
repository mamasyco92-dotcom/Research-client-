"""Recherche de sociétés et de contacts B2B à partir de sources publiques.
Aucune donnée (nom, email, AUM) n'est inventée ni déduite d'un format probable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from urllib import robotparser
from collections import deque
import ipaddress
import json
import re
import socket
import unicodedata

import requests
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from bs4 import BeautifulSoup
from contact_quality import (person_name, role_label, quality_contact, shortlist,
                             in_target_division)
from source_discovery import (external_links, search_lei, classify_hits,
                              indexed_linkedin_candidates, indexed_people_candidates)
from search_provider import search_web
from company_identity import resolve_company

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SycomoreResearch/1.0; public B2B research)"}
TIMEOUT = (5, 12)
MAX_BYTES = 3_500_000
# Plafond de sécurité technique, non paramétrable par l'utilisateur.
MAX_PAGES = 36
MAX_PDFS = 6
MAX_PDF_PAGES = 12
MAX_PDF_BYTES = 8_000_000
MAX_FETCHES = 72
PDF_LINK = re.compile(r'\.pdf$', re.I)
PDF_TOPIC = re.compile(r'annual[\s_-]*report|rapport[\s_-]*annuel|fact[\s_-]*sheet|brochure|annuaire', re.I)
IMPORTANT_PATH = re.compile(
    r"team|people|person|profil|biograph|\bbio\b|about|who-we-are|our-team|leadership|direction|equipe|gestion|"
    r"investment|investissement|fund|fonds|manager|selection|partners|contact|wealth|"
    r"portfol|strate|company|societe|notre-histoire|expertise|governance|governance|"
    r"committee|comite|approach|approche|research|recherche|asset|service",
    re.I,
)
ROLE_RE = re.compile(
    r"fund selection|sélection(?:neur| de fonds| des fonds)|sélection externe|manager selection|"
    r"chief investment officer|\bcio\b|head of investment|investment director|"
    r"investment manager|head of portfolio|portfolio manager|portfolio management|"
    r"head of research|investment committee|\bhead of asset|asset allocation|"
    r"investment strategist|multi.manager|fund analyst|analyste fonds|"
    r"responsable (?:de la )?gestion|responsable (?:des )?investissements|"
    r"directeur (?:des )?investissements|directrice (?:des )?investissements|"
    r"directeur (?:de la )?gestion|directrice (?:de la )?gestion|"
    r"gestionnaire de portefeuille|analyste (?:de )?fonds|"
    r"managing partner|managing director|founding partner|associé.gérant|"
    r"head of advisory|head of discretionary|private banker|"
    r"partner\b|\bpresident\b|\bprésident\b|\bceo\b|chief executive officer",
    re.I,
)
HIGH_ROLE = re.compile(r"fund selection|sélection|manager selection|chief investment officer|\bcio\b|head of investment|head of portfolio|investment director|investment committee|directeur des investissements|responsable des investissements", re.I)
MED_ROLE = re.compile(r"portfolio manager|investment manager|fund analyst|analyste fonds|portfolio management|gestionnaire de portefeuille|asset allocation|head of research|multi.manager", re.I)
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+|00)\d[\d\s().-]{7,19}\d(?!\w)")
WORD = r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:[-'][A-ZÀ-ÖØ-Þ]?[a-zà-öø-ÿ]+)*"
PARTICLE = r"(?:van|von|de|der|den|del|di|da|du|la|le|des|ten|ter|bin|al)"
PERSON_RE = re.compile(rf"\b({WORD}(?:\s+(?:{WORD}|{PARTICLE})){{1,4}})\b")
BAD_NAME_TERMS = re.compile(r"\b(?:chief|investment|officer|portfolio|manager|management|wealth|asset|research|team|contact|news|about|fund|services|group|office|funds|private|bank|banking|client|committee|solutions|head|strategy|careers|home|global|our|site|privacy|policy|financial|sustainab|direction|groupe|entreprise|responsable|gestion|fonds|capit|découvrez|decouvrez|selection)\b", re.I)
COUNTRIES = {
    'Suisse': r'\b(?:suisse|switzerland|swiss)\b',
    'France': r'\b(?:france|french)\b',
    'Luxembourg': r'\b(?:luxembourg)\b',
    'Royaume-Uni': r'\b(?:united kingdom|uk|london|britain)\b',
    'Israël': r'\b(?:israel|israël|israeli)\b',
    'Pays-Bas': r'\b(?:netherlands|the netherlands|pays-bas|dutch)\b',
    'Belgique': r'\b(?:belgium|belgique)\b',
    'Allemagne': r'\b(?:germany|allemagne)\b',
    'États-Unis': r'\b(?:united states|usa|états-unis)\b',
    'Émirats arabes unis': r'\b(?:united arab emirates|uae|dubai|dubaï)\b',
}
TYPE_TERMS = [
    ('Caisse de pension / Institutionnel', r'pension fund|pension scheme|fonds de pension|caisse de pension|fonds de prévoyance'),
    ('Banque privée', r'private bank(?:ing)?|banque privée'),
    ('Family office', r'family office|multi.family office'),
    ('Gestion de fortune', r'wealth management|gestion de fortune|independent asset manager|gérant de fortune'),
    ('Gestion d’actifs', r'asset management|investment management|société de gestion|gestion d.actifs'),
]


@dataclass
class Page:
    url: str
    text: str
    soup: BeautifulSoup
    title: str = ''
    description: str = ''
    links: list[tuple[str,str]] = field(default_factory=list)
    pdf_contacts: list[dict] | None = None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or '').strip()


def _norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s).lower())
    return ''.join(c for c in s if not unicodedata.combining(c))


def normalize_url(raw: str) -> str:
    raw = (raw or '').strip()
    if not raw: raise ValueError("Renseigne l'URL du site officiel.")
    if not re.match(r'^[a-z][a-z0-9+.-]*://', raw, re.I):raw = 'https://' + raw
    p = urlparse(raw)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError("URL invalide. Utilise un site public http ou https.")
    if p.port and p.port not in (80,443): raise ValueError('Ports non standard non autorisés.')
    if not _safe_host(p.hostname): raise ValueError('URL locale/privée non autorisée.')
    return p._replace(fragment='').geturl()


def _safe_host(host: str) -> bool:
    if not host or host.lower() in ('localhost',) or host.lower().endswith(('.local','.internal','.localhost')):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global
    except ValueError:pass
    # On refuse les noms manifestement locaux; les IP issues du DNS sont vérifiées avant les requêtes.
    return '.' in host


def _safe_dns(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None)
        return bool(addresses) and all(ipaddress.ip_address(r[4][0]).is_global for r in addresses)
    except (socket.gaierror, ValueError, OSError):
        return False


def base_domain(url: str) -> str:
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def same_site(url: str, domain: str) -> bool:
    hostname = (urlparse(url).hostname or '').lower()
    return hostname == domain or hostname == 'www.' + domain or hostname.endswith('.' + domain)


def _download(url: str, session: requests.Session, allowed, *, robots=False, pdf=True):
    """Contrôle chaque destination avant la requête, redirections comprises."""
    types = {'text/plain'} if robots else {'text/html'}
    if pdf and not robots:
        types.add('application/pdf')
    for _ in range(4):
        url = normalize_url(url)
        if not allowed(url):
            return None
        if not _safe_dns(urlparse(url).hostname or ''):
            raise ValueError('Domaine non résolu ou adresse privée.')
        with session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False, stream=True) as res:
            if 300 <= res.status_code < 400 and res.headers.get('location'):
                url = urljoin(url, res.headers['location'])
                continue
            res.raise_for_status()
            mime = res.headers.get('content-type', '').split(';')[0].strip().lower()
            if mime == 'application/octet-stream' and PDF_LINK.search(urlparse(url).path):
                mime = 'application/pdf'
            if mime not in types:
                return None
            limit = MAX_PDF_BYTES if mime == 'application/pdf' else MAX_BYTES
            data = bytearray()
            for chunk in res.iter_content(32768):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError('Document trop volumineux.')
            if mime == 'application/pdf' and b'%PDF-' not in data[:1024]:
                return None
            body = bytes(data) if mime == 'application/pdf' else data.decode(res.encoding or 'utf-8', errors='replace')
            return url, mime, body
    return None


def parse_page(url: str, html: str) -> Page:
    soup=BeautifulSoup(html, 'html.parser')
    og=soup.find('meta', attrs={'property':'og:site_name'})
    description=soup.find('meta',attrs={'name':'description'}) or soup.find('meta',attrs={'property':'og:description'})
    title=(og.get('content','') if og else '') or (soup.title.get_text(' ',strip=True) if soup.title else '')
    desc=description.get('content','') if description else ''
    links=[]
    for a in soup.find_all('a', href=True):
        href=urldefrag(urljoin(url,a['href']))[0]
        if urlparse(href).scheme in ('https','http'):
            links.append((href, a.get_text(' ',strip=True)))
    filtered=BeautifulSoup(html,'html.parser')
    for tag in filtered(['script','style','noscript','svg','footer','form']):tag.decompose()
    # Les menus répétés noient les informations propres à chaque page.
    for tag in filtered.select('nav, [role="navigation"], .cookie-banner, #cookie-banner'):tag.decompose()
    text=_clean(filtered.get_text(' ',strip=True))[:100_000]
    return Page(url,text,soup,title,desc,links)


def _quick_search(queries: list[str], logs: list[str], api_key: str = '') -> list[dict]:
    """Résultats de recherche Web sans consultation des profils LinkedIn."""
    return search_web(queries, logs, api_key=api_key)


def _contexts(text: str, regex: str, before: int=65, after: int=195, max_found: int=3) -> list[str]:
    output=[]
    for m in re.finditer(regex, text or '', re.I):
        q=_clean(text[max(0,m.start()-before):min(len(text),m.end()+after)])
        if q and q not in output:output.append(q)
        if len(output)>=max_found:break
    return output


def _evidence(pages: list[Page], regex: str, max_found: int=3) -> list[dict]:
    evidence=[]
    for page in pages:
        for excerpt in _contexts(page.description+' '+page.text, regex, max_found=2):
            evidence.append({'extrait': excerpt, 'url':page.url})
            if len(evidence)>=max_found:return evidence
    return evidence


def _extract_name(pages: list[Page], domain:str) -> str:
    if pages:
        name=_clean(pages[0].title)
        for separator in [' | ',' – ',' — ',' - ',': ']:
            if separator in name:
                fragments=[_clean(x) for x in name.split(separator)]
                fragments=[x for x in fragments if x and len(x)<75 and not re.fullmatch(r'home|accueil|welcome|our people|about us|contact',x,re.I)]
                if fragments: name=fragments[0]
                break
        if name and not re.search(r'home|accueil|welcome',name,re.I) and len(name)<80:return name
    return domain.split('.')[0].replace('-',' ').title()


def _field(result: dict, key: str, value: str, items: list[dict]):
    result[key]=value
    result['preuves'][key]=items


def profile_from_pages(pages: list[Page], domain: str, site_url:str) -> dict:
    profile={'url':site_url,'nom':_extract_name(pages,domain),'date_recherche':str(date.today()),'preuves':{}}
    documents=pages or []
    # Pays : on privilégie les mentions sur la page de présentation plutôt que les pays traversés au hasard.
    focus=' '.join((p.description+' '+p.text[:3000]) for p in pages[:3])
    pays=[name for name,reg in COUNTRIES.items() if re.search(reg,focus,re.I)]
    _field(profile,'pays',', '.join(pays[:4]) if pays else 'À confirmer',_evidence(documents, r'\b(?:switzerland|suisse|france|luxembourg|netherlands|pays-bas|israel|israël|united kingdom|london|geneva|genève|paris|zurich)\b',2))
    actor='À qualifier'
    for label,pattern in TYPE_TERMS:
        if re.search(pattern,' '.join(p.text[:9000] for p in pages[:8]), re.I):
            actor=label;break
    _field(profile,'type_acteur',actor,_evidence(documents,r'private bank|banque privée|family office|wealth management|gestion de fortune|asset management|pension fund',2))
    aum=_evidence(documents,r'assets under management|assets under advisory|\bAUM\b|actifs sous gestion|encours (?:gérés|sous gestion|de gestion)|assets (?:managed|advised)',4)
    # Le champ reste une citation brute: ne pas transformer administration et gestion en AUM.
    aum_use=[x for x in aum if re.search(r'\b(?:EUR|CHF|USD|€|\$|£|million|billion|milliard|milliards|bn|mrd|million)\b|\d[,.]\d',x['extrait'],re.I)]
    _field(profile,'encours',aum_use[0]['extrait'][:280] if aum_use else 'À confirmer',aum_use[:2])
    open_e=_evidence(documents,r'open.architecture|architecture ouverte',3)
    third_e=_evidence(documents,r'third.party (?:funds?|managers?)|fonds (?:tiers|externes)|external fund|external manager|independent fund selection',3)
    if open_e:
        _field(profile,'architecture','Confirmée — mention explicite',open_e)
    elif third_e:
        _field(profile,'architecture','Indices de fonds tiers — architecture à confirmer',third_e)
    else:_field(profile,'architecture','À confirmer',[])
    ucits_ext=_evidence(documents,r'(?:third.party|external|fonds tiers|fonds externes)[^.!?]{0,110}(?:UCITS|OPCVM)|(?:UCITS|OPCVM)[^.!?]{0,110}(?:third.party|external|fonds tiers|fonds externes)',3)
    ucits_any=_evidence(documents,r'\bUCITS\b|\bOPCVM\b',2)
    if ucits_ext:_field(profile,'ucits_externes','Confirmés — fonds externes UCITS mentionnés',ucits_ext)
    elif ucits_any:_field(profile,'ucits_externes','UCITS mentionnés — statut externe à confirmer',ucits_any)
    elif third_e:_field(profile,'ucits_externes','Fonds tiers mentionnés — statut UCITS à confirmer',third_e)
    else:_field(profile,'ucits_externes','À confirmer',[])
    alt_e=_evidence(documents,r'alternative investment|alternative strategies|alternative funds|stratégies alternatives|fonds alternatifs|hedge fund|long.short|market neutral',3)
    _field(profile,'alternatives','Stratégies alternatives mentionnées' if alt_e else 'À confirmer',alt_e)
    selection=_evidence(documents,r'fund selection|manager selection|sélection de fonds|sélection de gérants|investment committee|comité d.investissement|due diligence',3)
    _field(profile,'selection_fonds',selection[0]['extrait'][:280] if selection else 'À identifier',selection)
    strategy=_evidence(documents,r'asset allocation|multi.asset|multi.actifs|equities|actions cotées|fixed income|obligations|esg|impact investing|investissement responsable|private markets',3)
    _field(profile,'strategies',strategy[0]['extrait'][:280] if strategy else 'À identifier',strategy)
    return profile


def _valid_name(name: str) -> bool:
    return bool(person_name(name))


def _role_from_text(text: str) -> str:
    # Only use actual role phrases. No role is inferred from a person's name.
    text = EMAIL_RE.sub(' ', text)
    text = PHONE_RE.sub(' ', text)
    return role_label(text)


def _contact(name: str, role: str, source: str, evidence: str, email='', phone='', verified='À vérifier') -> dict:
    role=_clean(role)[:150]
    importance='Haute' if HIGH_ROLE.search(role) else ('Moyenne' if MED_ROLE.search(role) else 'À qualifier')
    decision=('Sélectionneur / décideur potentiel' if HIGH_ROLE.search(role) else ('Gestion / utilisateur potentiel' if MED_ROLE.search(role) else 'Rôle à confirmer'))
    return {'nom':_clean(name),'fonction':role,'email':email,'telephone':phone,'role_decisionnel':decision,
            'priorite':importance,'source':source,'statut':verified,'preuve':_clean(evidence)[:350]}


def _jd_persons(page: Page) -> list[dict]:
    contacts=[]
    for s in page.soup.find_all('script',attrs={'type':'application/ld+json'}):
        try:info=json.loads(s.string or s.get_text())
        except (ValueError,TypeError):continue
        stack=[info]
        while stack:
            item=stack.pop()
            if isinstance(item,list):stack.extend(item);continue
            if not isinstance(item,dict):continue
            if isinstance(item.get('@graph'),list):stack.extend(item['@graph'])
            typ=item.get('@type','')
            if 'Person' not in (typ if isinstance(typ,list) else [typ]):continue
            name=str(item.get('name',''))
            role=str(item.get('jobTitle',''))
            if _valid_name(name) and ROLE_RE.search(role):
                email=str(item.get('email','')).removeprefix('mailto:')
                if not EMAIL_RE.fullmatch(email):email=''
                phone=str(item.get('telephone',''))
                if not TEL_VALUE.fullmatch(phone) or not 8 <= len(_phone_key(phone)) <= 15:phone=''
                contacts.append(_contact(name,role,page.url,role,email,phone,'Source officielle'))
    return contacts




def _phone_key(value: str) -> str:
    digits = re.sub(r'\D', '', value)
    return digits[2:] if value.startswith('00') else digits


def _unique_coordinates(emails, phones):
    emails = {value.casefold(): value for value in emails}
    phones = {_phone_key(value): value for value in phones
              if 8 <= len(_phone_key(value)) <= 15}
    return (next(iter(emails.values())) if len(emails) == 1 else '',
            next(iter(phones.values())) if len(phones) == 1 else '')


LOCAL_PHONE = re.compile(r'(?:t[ée]l(?:[ée]phone)?\.?|phone|direct|mobile)\s*:?\s*(0[1-9](?:[ .-]?\d{2}){4})(?!\d)', re.I)
TEL_VALUE = re.compile(r'(?:\+|00)?\d[\d ().-]{6,20}\d')


def _card_coordinates(node):
    """Lit uniquement les coordonnées publiées dans la carte individuelle."""
    text = _clean(node.get_text(' ', strip=True))
    emails = EMAIL_RE.findall(text)
    phones = PHONE_RE.findall(text) + LOCAL_PHONE.findall(text)
    for link in node.select('a[href]'):
        href = link['href']
        if href.lower().startswith('mailto:'):
            value = unquote(href[7:].split('?')[0]).strip()
            if EMAIL_RE.fullmatch(value):
                emails.append(value)
        elif href.lower().startswith('tel:'):
            value = unquote(href[4:]).strip()
            if TEL_VALUE.fullmatch(value):
                phones.append(value)
    return _unique_coordinates(emails, phones)


def _named_email(email: str, name: str) -> bool:
    local = re.sub(r'[^a-z0-9]', '', _norm(email.split('@')[0]))
    parts = re.findall(r'[a-z]+', _norm(name))
    if len(parts) < 2:
        return False
    first, last = parts[0], parts[-1]
    return local in {first + last, last + first, first[0] + last, last + first[0],
                     ''.join(parts), ''.join(parts[1:]) + first}


def _individual_profile(page: Page) -> list[dict]:
    """S'appuie sur le titre nominatif et le rôle de la fiche officielle."""
    main = page.soup.select_one('main, [role="main"]') or page.soup.body
    if main is None:
        return []
    body = BeautifulSoup(str(main), 'html.parser')
    for element in body.select('header nav, nav, footer, aside, form, script, style, [hidden]'):
        element.decompose()
    titles = body.find_all('h1')
    if len(titles) != 1:
        return []
    title = titles[0]
    name = person_name(title.get_text(' ', strip=True))
    if not name:
        return []
    roles = [c['fonction'] for c in _jd_persons(page) if c['nom'] == name]
    if not roles:
        job = title.find_next(attrs={'itemprop': 'jobTitle'})
        if job and ROLE_RE.search(job.get_text(' ', strip=True)):
            roles = [_role_from_text(job.get_text(' ', strip=True))]
    if not roles:
        following = title.find_next(['p', 'h2', 'h3', 'div'])
        text = _clean(following.get_text(' ', strip=True)) if following else ''
        match = ROLE_RE.search(text[:95])
        if not match or match.start() > 16 or len(text) > 120:
            return []
        roles = [_role_from_text(text)]
    if not roles[0]:
        return []
    # Un email nominatif peut se trouver plus bas dans une biographie longue.
    # Pour un téléphone, exiger un bloc de coordonnées explicitement direct.
    emails = [email for email in EMAIL_RE.findall(body.get_text(' ', strip=True))
              if _named_email(email, name)]
    direct_phones = []
    for link in body.select('a[href]'):
        href = link['href']
        if href.lower().startswith('mailto:'):
            value = unquote(href[7:].split('?')[0]).strip()
            if EMAIL_RE.fullmatch(value) and _named_email(value, name):
                emails.append(value)
    other_people = {person_name(h.get_text(' ', strip=True)) for h in body.find_all(['h2', 'h3', 'h4'])}
    if not (other_people - {'', name}):
        for block in body.select('p, li, address'):
            text = _clean(block.get_text(' ', strip=True))
            if len(text) <= 100 and re.match(r'^(?:(?:ligne|tél[ée]phone|tel\.?|phone) )?(?:directe?|mobile)\b', text, re.I):
                _, phone = _card_coordinates(block)
                if phone:
                    direct_phones.append(phone)
    email, phone = _unique_coordinates(emails, direct_phones)
    if email or phone:
        return [_contact(name, roles[0], page.url,
                         ' — '.join(filter(None, (roles[0], email, phone))),
                         email, phone, 'Source officielle')]
    return []


def _pdf_contacts(url: str, body: bytes) -> list[dict]:
    reader = PdfReader(BytesIO(body))
    if reader.is_encrypted:
        raise ValueError('PDF chiffré.')
    contacts = []
    for number, pdf_page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        lines = (pdf_page.extract_text() or '').splitlines()
        for index, line in enumerate(lines):
            # Les fiches textuelles utilisent un nom sur sa propre ligne, ou
            # une ligne de tableau avec séparateurs explicites.
            parts = re.split(r'\s*[|•]\s*|\s+[-–—]\s+| {2,}|\t+', line.strip())
            name = person_name(parts[0])
            if not name:
                continue
            following = lines[index + 1:index + 4]
            role_line = parts[1] if len(parts) > 1 else (following.pop(0).strip() if following else '')
            match = ROLE_RE.search(role_line[:90])
            if not match or match.start() > 12 or len(role_line) > 100:
                continue
            role = _role_from_text(role_line)
            if not role:
                continue
            details = parts[2:]
            for detail in following:
                detail = detail.strip()
                if not re.match(r'^(?:(?:e-?mail|courriel|direct|mobile|phone|t[ée]l(?:[ée]phone)?\.?)\s*:|[+]|00|[^ ]+@)', detail, re.I):
                    break
                details.append(detail)
            emails = [email for detail in details for email in EMAIL_RE.findall(detail)
                      if _named_email(email, name)]
            phones = [phone for detail in details
                      if not re.search(r'\b(?:fax|standard|switchboard)\b', detail, re.I)
                      for phone in PHONE_RE.findall(detail) + LOCAL_PHONE.findall(detail)]
            email, phone = _unique_coordinates(emails, phones)
            if email or phone:
                evidence = ' — '.join(filter(None, (name, role, email, phone)))
                contacts.append(_contact(name, role, f'{url}#page={number}',
                                         evidence, email, phone, 'Source officielle'))
    return contacts


def _structured_contact_cards(page: Page) -> list[dict]:
    """Repère les cartes *individuelles* d'équipe, sans fusionner des collègues.

    La présence simultanée d'un nom plausible et d'une fonction sur la même
    petite carte est exigée. Les rubriques générales et annuaires collectifs
    ne sont pas interprétés comme des personnes.
    """
    found=[]
    selectors='article, [itemtype*="schema.org/Person"], [class*="team-member"], [class*="team_member"], [class*="person-card"], [class*="staff-member"], [class*="profile-card"], [class*="employee-card"]'
    for card in page.soup.select(selectors)[:250]:
        content=_clean(card.get_text(' ',strip=True))
        if not (10 < len(content) <= 650):continue
        headings=card.find_all(['h1','h2','h3','h4','h5','strong','b'],limit=12)
        names=[person_name(_clean(h.get_text(' ',strip=True))) for h in headings]
        names=list(dict.fromkeys(n for n in names if n))
        if len(names)!=1:continue
        name=names[0]
        # Position de la fonction dans un extrait ne comportant qu'un nom.
        pos=content.find(name)
        if pos<0:continue
        context=content[pos+len(name):pos+len(name)+200]
        if not ROLE_RE.search(context[:130]):continue
        role=_role_from_text(context)
        if not role:continue
        email, phone = _card_coordinates(card)
        found.append(_contact(name,role,page.url,content[:300],email, phone,'Source officielle'))
    return found


def contacts_from_page(page: Page) -> list[dict]:
    """Extract only person+role pairs in a small, shared HTML context.

    Never take a role from a large parent listing or across two colleagues.
    """
    contacts = _jd_persons(page) + _structured_contact_cards(page) + _individual_profile(page)
    # Directories: a name in its heading, one role in a SMALL enclosing card.
    for head in page.soup.find_all(['h1', 'h2', 'h3', 'h4', 'strong', 'b']):
        raw_name = _clean(head.get_text(' ', strip=True))
        name = person_name(raw_name)
        if not name:
            continue
        parent = head
        for _ in range(3):
            parent = parent.parent
            if parent is None:
                break
            cardtext = _clean(parent.get_text(' ', strip=True))
            if len(cardtext) > 420:
                break
            other_names = {
                person_name(h.get_text(' ', strip=True)) for h in
                parent.find_all(['h1', 'h2', 'h3', 'h4', 'strong', 'b'])
            }
            other_names.discard('')
            if len(other_names) > 1:
                break
            # Role must occur immediately after the heading, within 100 chars.
            at = cardtext.find(raw_name)
            if at < 0:
                continue
            suffix = cardtext[at + len(raw_name):]
            if len(suffix) > 180:
                suffix = suffix[:180]
            if ROLE_RE.search(suffix) is None:
                continue
            role = _role_from_text(suffix)
            if not role or len(suffix.split()) > 26:
                continue
            is_container = parent.name in ('[document]', 'html', 'body', 'main') or parent.get('role') == 'main'
            email, phone = ('', '') if is_container else _card_coordinates(parent)
            person = _contact(name, role, page.url, cardtext[:280],
                              email, phone, 'Source officielle')
            contacts.append(person)
            break

    # Compact paragraph/li with role directly following name. Longer page
    # descriptions and navigation entries must not create bogus people.
    for node in page.soup.find_all(['p','li'], limit=400):
        line = _clean(node.get_text(' ',strip=True))
        if not 12 < len(line) < 190:
            continue
        for m in PERSON_RE.finditer(line[:90]):
            raw = m.group(1)
            name = person_name(raw)
            if not name or m.start()>4:
                continue
            after = line[m.end():]
            if len(after)>100 or not ROLE_RE.search(after[:65]):
                continue
            # The role must begin close to the name, not after a distant name.
            role_start = ROLE_RE.search(after[:65]).start()
            if role_start > 12:
                continue
            role = _role_from_text(after)
            if not role:
                continue
            email, phone = _card_coordinates(node)
            contacts.append(_contact(name, role, page.url, line,
                          email, phone, 'Source officielle'))
            break
    return contacts


def contacts_from_results(hits: list[dict], company: str, domain: str) -> list[dict]:
    result=[]
    # Résultats du moteur externe uniquement en pistes, jamais présentés comme vérifiés.
    for hit in hits:
        hurl=hit['url'];title=_clean(hit['title']);snippet=_clean(hit['snippet'])
        # Les profils LinkedIn ne sont pas extraits automatiquement : vérification manuelle.
        if 'linkedin.com' in (urlparse(hurl).hostname or '').lower():continue
        combined=(title+' '+snippet)[:600]
        if not ROLE_RE.search(combined):continue
        # Un résultat doit parler explicitement de l'entreprise cible.
        label=_norm(company).split(' | ')[0]
        if not (same_site(hurl,domain) or (len(label)>3 and label in _norm(combined)) or domain in _norm(combined)):
            continue
        # Privilégier les formes "Nom — Fonction" (évite d'attribuer des fonctions au hasard).
        for m in PERSON_RE.finditer(title[:150]):
            name=m.group(1)
            name=person_name(name)
            if not name:continue
            after=title[m.end():]
            before=title[:m.start()]
            job=_role_from_text(after[:100]) if len(after)<=125 else ''
            if not job and re.search(re.escape(name)+r'.{0,55}'+ROLE_RE.pattern,snippet,re.I):
                job=_role_from_text(snippet)
            if job:
                result.append(_contact(name,job,hurl,combined,'','','Piste externe — à vérifier'))
                break
    return result


def merge_contacts(contacts: list[dict], focus: str = 'wealth_asset') -> list[dict]:
    """Validate, dedupe and prioritize; do not keep irrelevant generic titles."""
    return shortlist(contacts, focus)


def crawl_site(start_url: str, search_hits: list[dict], logs: list[str], focus: str = 'wealth_asset') -> list[Page]:
    url = normalize_url(start_url)
    domain = base_domain(url)
    candidates = deque([url])
    for hit in search_hits:
        href = hit['url']
        hint = href + ' ' + hit.get('title', '')
        if same_site(href, domain) and (IMPORTANT_PATH.search(hint) or PDF_TOPIC.search(hint)):
            candidates.append(href)
    seen = set()
    pages = []
    failures = blocked = pdf_attempts = html_count = pdf_count = 0
    robots_by_origin = {}

    def in_scope(target):
        return same_site(target, domain) and in_target_division(target, focus)

    def allowed(target):
        nonlocal blocked
        if not in_scope(target):
            return False
        parsed = urlparse(target)
        origin = (parsed.scheme, parsed.netloc)
        if origin not in robots_by_origin:
            robots_by_origin[origin] = None
            try:
                robots = _download(f'{parsed.scheme}://{parsed.netloc}/robots.txt',
                                   session, lambda u: (urlparse(u).scheme, urlparse(u).netloc) == origin,
                                   robots=True)
                if robots:
                    parser = robotparser.RobotFileParser()
                    parser.parse(robots[2].splitlines())
                    robots_by_origin[origin] = parser
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (401, 403):
                    robots_by_origin[origin] = False
            except (requests.RequestException, ValueError):
                pass
        parser = robots_by_origin[origin]
        if parser is False or (parser is not None and not parser.can_fetch(HEADERS['User-Agent'], target)):
            blocked += 1
            return False
        return True

    with requests.Session() as session:
        while candidates and html_count < MAX_PAGES and len(seen) < MAX_FETCHES:
            try:
                url = normalize_url(candidates.popleft())
            except (ValueError, TypeError):
                continue
            path = urlparse(url).path
            if not in_scope(url) or url in seen or re.search(r'\.(?:jpg|jpeg|png|gif|svg|mp4|zip|docx?|xlsx?)$', path, re.I):
                continue
            if PDF_LINK.search(path) and pdf_attempts >= MAX_PDFS:
                continue
            seen.add(url)
            try:
                document = _download(url, session, allowed, pdf=pdf_attempts < MAX_PDFS)
                if not document:
                    continue
                final_url, mime, body = document
                if mime == 'application/pdf':
                    pdf_attempts += 1
                    contacts = _pdf_contacts(final_url, body)
                    pages.append(Page(final_url, '', BeautifulSoup('', 'html.parser'), pdf_contacts=contacts))
                    pdf_count += 1
                    continue
                page = parse_page(final_url, body)
            except (requests.RequestException, ValueError, PyPdfError, OverflowError, RecursionError):
                failures += 1
                continue
            html_count += 1
            pages.append(page)
            fresh = {}
            for href, anchor in page.links:
                if not in_scope(href) or href in seen:
                    continue
                path = urlparse(href).path
                profile_link = person_name(anchor) and all(
                    token in _norm(unquote(path)) for token in _norm(anchor).split()
                )
                hint = path + ' ' + anchor
                pdf_link = PDF_LINK.search(path)
                if profile_link or IMPORTANT_PATH.search(hint) or (pdf_link and PDF_TOPIC.search(hint)):
                    priority = 0 if profile_link else 2 if pdf_link else 1
                    fresh[href] = min(priority, fresh.get(href, priority))
            candidates.extend(sorted(fresh, key=lambda href: (fresh[href], len(href), href))[:40])
    logs.append(f'{html_count} pages HTML et {pdf_count} PDF officiels analysés (texte, {MAX_PDF_PAGES} pages maximum par PDF) ; '
                f'{failures} échec(s) ; {blocked} URL exclue(s) par robots.txt.')
    team_pages = sum(1 for p in pages if p.pdf_contacts is None and IMPORTANT_PATH.search(urlparse(p.url).path))
    logs.append(f'{team_pages} page(s) HTML avec une URL liée aux activités, à l’équipe ou aux contacts.')
    if html_count == MAX_PAGES or len(seen) == MAX_FETCHES or pdf_attempts == MAX_PDFS:
        logs.append('Plafond de collecte atteint. Les résultats peuvent être incomplets.')
    return pages


def research(url: str, focus: str = 'wealth_asset', company_name: str = '',
             serper_api_key: str = '', progress=None) -> dict:
    def report(value, message):
        if progress is not None: progress(value, message)
    report(0.05, 'Identification du site et recherche des sources officielles')
    url=normalize_url(url)
    domain=base_domain(url)
    logs=[]
    def lookup(queries):
        # Compatibilité avec les moteurs existants et leurs tests.
        if serper_api_key:
            return _quick_search(queries, logs, serper_api_key)
        return _quick_search(queries, logs)
    # Phase 1 : trouver les pages officielles qui parlent des équipes et produits.
    site_queries=[
        f'site:{domain} team people investment committee fund selection',
        f'site:{domain} gestion équipe sélection fonds CIO',
        f'site:{domain} wealth management open architecture third party funds',
        f'site:{domain} AUM assets under management encours',
        f'site:{domain} filetype:pdf (investment team OR équipe gestion OR fund contact)',
    ]
    official_hits=lookup(site_queries)
    report(0.25, 'Analyse des pages officielles')
    documents = crawl_site(url, official_hits, logs, focus)
    pages = [page for page in documents if page.pdf_contacts is None]
    if not pages:
        if not company_name.strip():
            raise RuntimeError("Site officiel inaccessible. Renseigne le nom exact de la société pour permettre la recherche Web externe.")
        logs.append('Site officiel inaccessible : recherche externe effectuée à partir du nom saisi ; profil société incomplet.')
    company=profile_from_pages(pages,domain,url)
    identification=resolve_company(pages,domain,company_name)
    company['identification']=identification
    company['nom']=identification['nom']
    name=company['nom']
    logs.append('Identification : '+name+' — '+identification['niveau']+' ('+identification['origine']+').')
    if 'à confirmer' in identification['niveau']:
        logs.append('Le nom de la société est incertain : vérifie le nom exact avant de considérer la recherche externe comme exhaustive.')
    company['perimetre_contacts'] = ('Gestion d’actifs / Wealth Management' if focus == 'wealth_asset' else 'Toutes activités')
    # Phase 2 : chercher par nom de société, et non seulement avec son domaine.
    # Les résultats externes restent des pistes et ne prouvent pas l'emploi actuel.
    search_queries=[
        f'"{name}" ("CIO" OR "portfolio manager" OR "investment manager")',
        f'"{name}" ("fund selection" OR "manager selection" OR "asset allocation")',
        f'"{name}" ("CEO" OR "founder" OR "investment team")',
        f'"{name}" ("wealth management" OR "fund analyst" OR "investment director")',
        f'site:linkedin.com/in/ "{name}"',
        f'site:linkedin.com/in/ "{name}" ("investment" OR "portfolio" OR "fund")',
        f'"{name}" ("team" OR "employees" OR "management")',
    ]
    report(0.60, 'Recherche des interlocuteurs sur le Web')
    other_hits=lookup(search_queries)
    all_hits=list({r['url']:r for r in official_hits+other_hits}.values())
    company['sources_externes']=classify_hits(other_hits,name,domain)
    # Un résultat indexé LinkedIn constitue une piste, non un contact confirmé.
    # Aucune requête n’est effectuée vers LinkedIn ou d’autres profils externes.
    company['pistes_web']=indexed_people_candidates(other_hits,name)
    # Conservé pour les résultats historiques de l'interface V4.1.
    company['pistes_linkedin']=[p for p in company['pistes_web']
                                if p['origine'].startswith('LinkedIn')]
    company['liens_recherche']=external_links(name,domain)
    # GLEIF donne des entités juridiques candidates, pas les encours ni les contacts.
    report(0.80, 'Vérification des entités et qualification des contacts')
    company['registre_lei']=search_lei(name,logs)
    logs.append(f'{len(official_hits)} résultats ciblés site, {len(other_hits)} résultats de recherche externe.')
    short=sum(1 for p in pages if len(p.text)<250)
    if short:
        logs.append(f'{short} page(s) avec très peu de texte : site éventuellement rendu par JavaScript; une recherche HTML seule ne suffit pas.')
    if any(re.search(r'[\u0590-\u05FF\u0400-\u04FF]',p.text) for p in pages):
        logs.append('Le site contient des écritures hébraïques/cyrilliques : l’extraction actuelle des noms privilégie les noms en alphabet latin et peut en manquer.')
    logs.append(f"Pistes Web : {len(company['pistes_web'])} personne(s) candidate(s), dont "
                f"{len(company['pistes_linkedin'])} issue(s) de résultats LinkedIn indexés. "
                "Aucune page LinkedIn consultée automatiquement.")
    if not company['pistes_web']:
        logs.append('Aucune piste extraite : résultats absents, non indexés, ou titres sans association explicite à la société.')
    people = []
    for document in documents:
        people.extend(contacts_from_page(document) if document.pdf_contacts is None else document.pdf_contacts)
    people.extend(contacts_from_results(all_hits,name,domain))
    contacts=merge_contacts(people,focus)
    logs.append(f'{len(people)} extractions brutes, {len(contacts)} contacts qualifiés après contrôle/dédoublonnage.')
    logs.append('Un résultat de moteur externe est une piste; la fonction et l’employeur doivent être vérifiés avant prospection.')
    if not contacts:logs.append('Aucun contact public confirmé : ne pas interpréter comme une absence de décideurs.')
    report(1.0, 'Recherche terminée')
    return {'profile':company,'contacts':contacts,'diagnostic':logs,
            'sources':list(dict.fromkeys([p.url for p in documents]+[h['url'] for h in all_hits]))}
