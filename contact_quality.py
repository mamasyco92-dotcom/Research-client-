"""Validation conservatrice des contacts B2B : qualité plutôt que volume.

Aucun email / téléphone / poste n'est généré par heuristique. Les informations
restent à vérifier à leur source, même lorsqu'elles sont issues du site officiel.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse


def cleaned(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def folded(value: str) -> str:
    s = unicodedata.normalize('NFKD', cleaned(value).casefold())
    return ''.join(c for c in s if not unicodedata.combining(c))


# Un intitulé qui suit un nom est retiré ; il ne fait jamais partie du patronyme.
TITLE_END = re.compile(
    r'\s+(?:general partner|managing partner|managing director|partner|director|'
    r'chief investment officer|chief executive officer|chief financial officer|'
    r'head of .+|portfolio manager|fund analyst|cio|ceo)\b.*$', re.I)
BAD_NAME = re.compile(
    r'\b(?:general|partner|director|managing|chief|investment|officer|portfolio|'
    r'architecture|wealth|management|outlook|july|june|october|the|midterms|'
    r'sustainable|investments|discretionary|contact|team|service|fund|funds|'
    r'asset|assets|news|markets|strategy|private|equity|advisory|global|'
    r'about|website|reports|performance|selection|research|relationships|'
    r'finance|financial|banking|solutions|company|group|our|people|'
    r'invest|investments|securities|authority|regulator|regulatory|corporation|limited)\b', re.I)
WORD = r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:[-'][A-ZÀ-ÖØ-Þ]?[a-zà-öø-ÿ]+)*"
PARTICLE = r'(?:de|du|des|del|da|di|la|le|van|von|der|den|bin|al)'
NAME_RE = re.compile(rf'^({WORD}(?:\s+(?:{WORD}|{PARTICLE})){{1,4}})$')
# Repère le début d'une autre personne dans une fonction contaminée.
NEXT_NAME = re.compile(rf'\s+({WORD}\s+{WORD})(?=\s+(?:open architecture|portfolio|fund|chief|head|investment|director|managing|partner|cio)\b)',re.I)


def person_name(raw: str) -> str:
    name = cleaned(raw).strip(' ,;:.–—|-')
    name = TITLE_END.sub('', name)
    if len(name) < 6 or len(name) > 65 or not NAME_RE.fullmatch(name):
        return ''
    if BAD_NAME.search(name):
        return ''
    words = name.split()
    if len(words) < 2 or len(words) > 5 or any(len(w) > 30 for w in words):
        return ''
    return name


# Une fonction générique (Partner / Managing Director) ne suffit pas à démontrer
# qu'un contact peut choisir ou référencer des fonds externes.
HIGH_ROLE = re.compile(
    r'\b(?:fund selection|fund selector|fund selectors|manager selection|'
    r'fund research|selection de fonds|selection de gerants|selectionneur de fonds|'
    r'open architecture|third.party fund|external fund|external manager|'
    r'chief investment officer|\bcio\b|head of investments?|head of portfolio|'
    r'directeur des investissements|responsable des investissements)\b', re.I)
MED_ROLE = re.compile(
    r'\b(?:portfolio manager|fund analyst|investment manager|asset allocation|'
    r'multi.manager|multi.asset|head of research|portfolio management|'
    r'investment strategist|gestionnaire de portefeuille|analyste fonds|'
    r'analyste de fonds|gestion sous mandat|responsable de la gestion)\b', re.I)
ROLE = re.compile(
    r'\b(?:partner and cio|chief investment officer|chief executive officer|'
    r'head of (?:fund selection|manager selection|investments?|portfolio(?: management)?|'
    r'asset allocation|research|[a-z ]{3,35}equities)|'
    r'fund selection|fund selector|fund analyst|open architecture|'
    r'manager selection|portfolio manager|portfolio management|'
    r'investment manager|investment strategist|asset allocation|'
    r'multi.manager|analyste (?:de )?fonds|gestionnaire de portefeuille|'
    r'directeur des investissements|responsable des investissements|\bcio\b|'
    r'partner relations manager|managing director|managing partner|general partner|partner)\b', re.I)

# Départements qui ne représentent pas, par défaut, une équipe acheteuse
# de fonds UCITS de tiers. Ce sont des exclusions de *périmètre commercial*,
# pas des affirmations sur l'entreprise ou les personnes.
EXCLUDED_PATH = re.compile(
    r'(?:^|/)(?:five-arrows|global-advisory|investment-banking|'
    r'mergers-and-acquisitions|corporate-finance|corporate-governance|'
    r'private-equity|venture-capital|careers|jobs|recruitment|'
    r'newsroom|press-releases|media|geostrategic-advisory)(?:/|$)',re.I)


def in_target_division(source_url: str, focus: str = 'wealth_asset') -> bool:
    if focus == 'all':
        return True
    path = urlparse(source_url or '').path
    return not EXCLUDED_PATH.search(path)


def role_label(raw: str) -> str:
    """Prend un intitulé court, sans avaler le profil de la personne suivante."""
    value = cleaned(raw)
    if not value:
        return ''
    match = ROLE.search(value)
    if not match:
        return ''
    role = value[match.start():]
    second_person = NEXT_NAME.search(role)
    if second_person:
        role = role[:second_person.start()]
    role = re.split(r'\s[|•–—]\s|[.!?;]|\s{3,}', role, maxsplit=1)[0]
    # Lorsque le texte restant est un long paragraphe, ne conserver que
    # l'intitulé explicite trouvé, jamais des phrases adjacentes.
    if len(role) > 85 or len(role.split()) > 12:
        role = match.group(0)
    return cleaned(role).strip(' ,:;–—-|')[:85]


def classify(role: str) -> tuple[str, str]:
    n = folded(role)
    if HIGH_ROLE.search(n):
        return 'Haute', 'Sélection / décision d’investissement potentielle'
    if MED_ROLE.search(n):
        return 'Moyenne', 'Gestion / allocation : intérêt à qualifier'
    return 'À qualifier', 'Aucun rôle de sélection de fonds établi'


def quality_contact(item: dict, focus: str = 'wealth_asset') -> dict | None:
    """Renvoie un contact propre seulement si une preuve nom+fonction existe.

    La fonction ne change pas les sources et ne fabrique jamais de coordonnées.
    Elle n'exige pas une relation contractuelle existante avec des fonds tiers.
    """
    raw_name = item.get('nom', item.get('name', ''))
    name = person_name(raw_name)
    source = item.get('source','')
    if not name or not in_target_division(source, focus):
        return None
    original = cleaned(item.get('fonction', item.get('role','')))
    role = role_label(original)
    if not role:
        return None
    priority, decision = classify(role)
    if priority == 'À qualifier':
        return None
    result = item.copy()
    result.update({'nom':name,'fonction':role,'priorite':priority,'role_decisionnel':decision,
                   'statut':item.get('statut', item.get('status','À vérifier'))})
    # Si l'intitulé d'origine était contaminé, sa provenance officielle ne suffit
    # pas à prouver l'association nom/fonction : signaler la vérification requise.
    if role != original and (len(original) > 85 or NEXT_NAME.search(original)):
        result['statut'] = 'Intitulé nettoyé — à vérifier'
    return result


def shortlist(items: list[dict], focus: str = 'wealth_asset') -> list[dict]:
    """Filtrage et dédoublonnage sans mutation de la liste ou de SQLite."""
    result = {}
    for raw in items:
        c = quality_contact(raw, focus)
        if c is None:
            continue
        key = folded(c['nom'])
        previous = result.get(key)
        if previous is None:
            result[key] = c
            continue
        # Garder source officielle, priorité plus forte, ou dossier plus complet.
        def rank(x):
            return (int(x['priorite']=='Haute'),
                    int(x.get('statut') == 'Source officielle'),
                    int(bool(x.get('email')))+int(bool(x.get('telephone'))),
                    -len(x['fonction']))
        preferred = c if rank(c)>rank(previous) else previous
        other = previous if preferred is c else c
        added = []
        for field in ('email', 'telephone'):
            if not preferred.get(field) and other.get(field):
                preferred[field] = other[field]
                added.append(other[field])
        if added and other.get('source') and other['source'] != preferred.get('source'):
            citation = f"Coordonnées ({', '.join(added)}) : {other['source']}"
            preferred['preuve'] = ' — '.join(filter(None, (citation, preferred.get('preuve'))))
        result[key] = preferred
    return sorted(result.values(), key=lambda x: (x['priorite']!='Haute', x['nom']))
