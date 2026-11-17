"""Recherche Web par API Google (Serper, facultatif) ou DDGS (sans clé).

Aucune requête directe aux pages LinkedIn et aucune clé sauvegardée dans SQLite.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
import re
from urllib.parse import urlparse

import requests

SERPER_SEARCH_URL = 'https://google.serper.dev/search'
MAX_RESULTS_PER_QUERY = 10


def _clean(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _make_hit(raw: dict, provider: str) -> dict | None:
    url = str(raw.get('link') or raw.get('href') or raw.get('url') or '')
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname:
        return None
    return {'url':url, 'title':_clean(raw.get('title',''))[:250],
            'snippet':_clean(raw.get('snippet') or raw.get('body') or '')[:800],
            'provider':provider}


def search_web(queries: list[str], logs: list[str], api_key: str = '') -> list[dict]:
    """Recherche des titres + snippets des SERP : ne lit PAS les profils sociaux.

    En cas d'erreur de l'API explicitement sélectionnée, on indique l'erreur
    sans révéler la clé; aucun basculement silencieux de moteur.
    """
    key = (api_key or os.environ.get('SERPER_API_KEY') or '').strip()
    provider = 'Google via Serper' if key else 'DDGS (moteur alternatif)'
    results: dict[str,dict] = {}
    ok_queries = 0
    errors = 0
    if not key:
        try:
            from ddgs import DDGS
        except ImportError:
            logs.append('DDGS non installé : exécute pip install -r requirements.txt.')
            return []
    # Ordre stable et au plus trois requêtes simultanées ; chaque tâche possède
    # sa session, aucune clé ni réponse brute n'est placée dans le diagnostic.
    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    def execute(query):
        try:
            if key:
                with requests.Session() as session:
                    response = session.post(SERPER_SEARCH_URL,
                        headers={'X-API-KEY':key,'Content-Type':'application/json'},
                        json={'q':query[:350],'num':MAX_RESULTS_PER_QUERY,'gl':'fr','hl':'en'},
                        timeout=(5,15))
                    response.raise_for_status()
                    payload = response.json()
                entries = payload.get('organic', []) if isinstance(payload, dict) else []
            else:
                entries = DDGS(timeout=12).text(query, max_results=MAX_RESULTS_PER_QUERY) or []
            if not isinstance(entries, list):
                raise ValueError('Format de résultats inattendu')
            return [hit for raw in entries[:MAX_RESULTS_PER_QUERY]
                    if isinstance(raw, dict) and (hit := _make_hit(raw, provider))], ''
        except Exception as exc:
            return [], type(exc).__name__
    with ThreadPoolExecutor(max_workers=3) as pool:
        for hits, error in pool.map(execute, queries):
            if error:
                errors += 1
                logs.append(f'{provider} : échec d’une requête ({error}).')
            else:
                ok_queries += 1
                for hit in hits:
                    results.setdefault(hit['url'], hit)
    logs.append(f'Recherche Web : {provider} ; {ok_queries}/{len(queries)} requêtes réussies ; '
                f'{len(results)} liens distincts ; {errors} échec(s).')
    if not key:
        logs.append('Résultats DDGS différents de Google : saisir une clé API Serper pour interroger Google.')
    return list(results.values())
