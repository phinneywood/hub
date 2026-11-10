from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
from preschool import presentation, publication, private_files
from hub.main import app


def test_finished_reader_is_private_and_uses_one_presentation(monkeypatch):
    snapshot = {'schema': 1, 'title': 'Synthetic class', 'revision': 7, 'updated': 1,
      'timezone': 'America/Los_Angeles', 'events': [], 'resources': [],
      'pages': [{'slug': 'routines', 'title': 'Class routines', 'body': 'Bring boots.\n\n- Coat\n- Hat'}]}
    monkeypatch.setattr(publication, 'latest', lambda: {'revision': 7, 'payload': snapshot})
    client = TestClient(app)
    for path in ('/preschool', '/preschool/', '/preschool/family-preview/'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'class-reference' in response.text and 'guide-topic' in response.text
        assert 'authored-list' in response.text and '/preschool/style.css' in response.text
        assert 'Download calendar' in response.text and 'Subscribe' not in response.text
        assert '/login' not in response.text and '/logout' not in response.text
        assert 'cloud calendar' in response.text.lower()
        assert client.get(path, headers={'Tailscale-User-Login': 'other@example.invalid'}).status_code == 403
    assert client.get('/preschool/style.css').status_code == 200
    snapshot['pages'][0]['body'] = '<script>alert(1)</script>'
    assert '<script>' not in client.get('/preschool/').text


def test_display_helpers_preserve_uncertainty():
    assert presentation.date_label('2027-05-07') == 'Fri, May 7, 2027'
    assert presentation.date_label('not a date') == 'Date not confirmed'
    assert presentation.time_label('09:30', '11:30') == '9:30–11:30 AM'
    assert presentation.time_label('10:00', '09:00') == 'Time not confirmed'
    assert presentation.time_label(None) == ''
    assert presentation.prose_blocks('Label: text\n\n- one\n- two')[1]['kind'] == 'list'


def test_local_journal_never_replays_confirmed_or_uncertain_effect(tmp_path):
    assert private_files.mutate('edition-1', lambda: {'version': 1}, tmp_path) == {'version': 1}
    with pytest.raises(ValueError):
        private_files.mutate('edition-1', lambda: pytest.fail('Do not replay'), tmp_path)
    def interrupted(): raise RuntimeError('Synthetic interruption')
    with pytest.raises(RuntimeError): private_files.mutate('edition-2', interrupted, tmp_path)
    assert json.loads((tmp_path / 'operations/edition-2.json').read_text())['phase'] == 'in_flight'
    with pytest.raises(ValueError):
        private_files.mutate('edition-2', lambda: pytest.fail('Do not replay'), tmp_path)
