"""Filtres métier explicites ; ils ne prouvent pas un pouvoir d'achat."""
import re
from contact_quality import folded

TARGETS = {
    'Tous les rôles': None,
    'Sélection de fonds / Architecture ouverte': r'fund select|fund research|manager select|selection.*(?:fonds|gerants)|selectionneur|open architecture|architecture ouverte|third.party fund|external (?:fund|manager)',
    'Direction des investissements': r'chief investment officer|\bcio\b|head of investments?|direct(?:eur|rice) des investissements|responsable des investissements',
    'Gestion / Allocation': r'portfolio|asset allocation|allocation d.actifs|multi.asset|multi.manager|gestionnaire|gestion sous mandat|investment manager|fund analyst|analyste.*fonds|head of research',
}


def filter_prospects(rows, target='Tous les rôles', coordinates='Toutes'):
    """Filtre uniquement les fonctions et coordonnées publiées, sans mutation."""
    pattern = TARGETS[target]
    result = []
    for row in rows:
        if pattern and not re.search(pattern, folded(row.get('fonction', ''))):
            continue
        email = bool(str(row.get('email') or '').strip())
        phone = bool(str(row.get('telephone') or '').strip())
        if coordinates == 'Avec email' and not email:
            continue
        if coordinates == 'Avec email ou téléphone' and not (email or phone):
            continue
        result.append(row)
    return result
