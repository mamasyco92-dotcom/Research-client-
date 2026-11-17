import search_provider


def test_parallel_search_deduplicates_queries_and_links_without_leaking_key(monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'organic': [{'link': 'https://example.com/person', 'title': 'Marie Dupont', 'snippet': 'Fund Selection'}]}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            query = kwargs['json']['q']
            calls.append(query)
            if query == 'failed': raise RuntimeError('secret-test-value')
            return Response()
    monkeypatch.setattr(search_provider.requests, 'Session', Session)
    logs = []
    hits = search_provider.search_web(['one', 'one', 'two', 'failed'], logs, api_key='secret-test-value')
    assert sorted(calls) == ['failed', 'one', 'two']
    assert len(hits) == 1
    assert '2/3 requêtes réussies' in ' '.join(logs)
    assert 'secret-test-value' not in ' '.join(logs)


def test_research_reports_progress_and_keeps_public_evidence(monkeypatch):
    import research
    page = research.parse_page('https://example.com/team', '<title>Alpha</title><article><h2>Marie Dupont</h2><p>Head of Fund Selection</p></article>')
    monkeypatch.setattr(research, '_quick_search', lambda *args: [])
    monkeypatch.setattr(research, 'crawl_site', lambda *args: [page])
    monkeypatch.setattr(research, 'search_lei', lambda *args: [])
    progress = []
    result = research.research('https://example.com', company_name='Alpha', progress=lambda n, msg: progress.append(n))
    assert progress == sorted(progress)
    assert progress[-1] == 1
    assert result['contacts'][0]['nom'] == 'Marie Dupont'
    assert result['contacts'][0]['source'] == page.url
    assert result['contacts'][0]['email'] == ''
