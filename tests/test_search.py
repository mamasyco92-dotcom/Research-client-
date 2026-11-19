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


def _example_pdf():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(400, 500)
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b'''BT /F1 12 Tf 16 TL 20 450 Td
        (Marie Dupont) Tj T* (Head of Fund Selection) Tj T*
        (Email: marie.dupont@example.com) Tj T* (Tel: 01 23 45 67 89) Tj T*
        (Paul Martin | Portfolio Manager | pierre.martin@example.com | Fax: +33123456788) Tj T*
        (Standard: +33123456789) Tj ET''')
    page.replace_contents(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_research_follows_a_bio_and_reads_public_pdf_with_source(monkeypatch):
    import requests
    import research

    root = 'https://example.com'
    bio = root + '/marie-dupont'
    pdf = root + '/documents/annual-report.pdf'
    paths = {
        root + '/robots.txt': ('text/plain', b'User-agent: *\nDisallow: /private/\n'),
        root: ('text/html', b'<title>Alpha</title><a href="/marie-dupont">Marie Dupont</a>'
                            b'<a href="/documents/annual-report.pdf">Annual Report</a>'
                            b'<a href="/team/redirect">Team</a>'),
        bio: ('text/html', b'<main><h1>Marie Dupont</h1><p>Head of Fund Selection</p>'
                           b'<p>' + b'Long professional biography. ' * 40 + b'</p>'
                           b'<p><a href="mailto:marie.dupont%40example.com">Email</a></p>'
                           b'<footer><a href="tel:+33999999999">Standard</a></footer></main>'),
        pdf: ('application/octet-stream', _example_pdf()),
        root + '/team/redirect': ('text/html', b''),
    }
    visited = []

    def get(self, url, **kwargs):
        visited.append(url)
        mime, body = paths[url]
        result = requests.Response()
        result.url = url
        result.status_code = 302 if url.endswith('/redirect') else 200
        result.headers['content-type'] = mime
        if result.status_code == 302:
            result.headers['Location'] = '/private/team.pdf'
        result._content = body
        result._content_consumed = True
        return result

    monkeypatch.setattr(research, '_quick_search', lambda *args: [])
    monkeypatch.setattr(research, 'search_lei', lambda *args: [])
    monkeypatch.setattr(research, '_safe_dns', lambda host: host == 'example.com')
    monkeypatch.setattr(requests.Session, 'get', get)

    result = research.research(root, company_name='Alpha')
    assert bio in visited and pdf in visited
    assert root + '/private/team.pdf' not in visited
    assert '1 PDF officiels analysés' in ' '.join(result['diagnostic'])
    assert len(result['contacts']) == 1
    person = result['contacts'][0]
    assert person['nom'] == 'Marie Dupont'
    assert person['email'] == 'marie.dupont@example.com'
    assert person['telephone'] == '01 23 45 67 89'
    assert person['source'] == pdf + '#page=1'


def test_profile_uses_explicit_direct_phone_and_ignores_shared_footer():
    import research
    page = research.parse_page('https://example.com/people/paul', '''
        <main><h1>Paul Martin</h1><p>Portfolio Manager</p>
        <p>Ligne directe <a href="tel:01-23-45-67-89">Appeler</a></p>
        <p><a href="mailto:pierre.martin@example.com">Assistant</a></p>
        <footer><a href="tel:+33987654321">Standard</a></footer></main>
    ''')
    contacts = research.merge_contacts(research.contacts_from_page(page))
    assert contacts[0]['telephone'] == '01-23-45-67-89'
    assert contacts[0]['email'] == ''
