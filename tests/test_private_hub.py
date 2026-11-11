from fastapi.testclient import TestClient
from hub.main import app
from hub import config, store
from preschool.support import config as school_config, store as school_preferences


def test_one_dashboard_links_to_private_preschool_without_external_service():
    client = TestClient(app)
    page = client.get('/tools')
    assert page.status_code == 200 and 'href="/preschool"' in page.text
    assert 'separate service' not in page.text
    assert client.get('/preschool', follow_redirects=False).status_code == 303
    assert client.get('/preschool', headers={'Tailscale-User-Login':'other@example.invalid'}).status_code == 403
    assert client.get('/preschool/static/preschool.css').status_code == 200


def test_school_uses_existing_hub_binding_and_opt_out_store():
    assert school_config is config
    store.set_ai_blocked('synthetic@example.invalid', True)
    assert school_preferences.ai_blocked('synthetic@example.invalid')
    store.set_ai_blocked('synthetic@example.invalid', False)
    assert not school_preferences.ai_blocked('synthetic@example.invalid')


def test_maintenance_reuses_hub_shell_and_all_declared_assets_resolve():
    from html.parser import HTMLParser
    class Assets(HTMLParser):
        urls = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ('script', 'link'):
                url = attrs.get('src') or attrs.get('href')
                if url and url.startswith('/'): self.urls.append(url)
    client = TestClient(app)
    response = client.get('/preschool/manage')
    assert response.status_code == 200
    assets = Assets(); assets.feed(response.text)
    assert '/static/style.css' in assets.urls
    assert '/static/app.js' in assets.urls
    assert any(url.startswith('/preschool/static/') for url in assets.urls)
    for url in assets.urls: assert client.get(url).status_code == 200, url
