"""Morning nudge: count and link only, quiet on clear days, loud when Hub is down. Synthetic data only."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'deploy' / 'morning_brief.py'
if not SCRIPT.exists():  # the installed-package check carries tests but not deployment scripts
    pytest.skip('deploy/morning_brief.py is not part of the installed package', allow_module_level=True)
spec = importlib.util.spec_from_file_location('morning_brief', SCRIPT)
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)
ENV = {'HUB_BRIEF_NOTIFY': 'mobile_app_synthetic', 'HUB_BRIEF_LINK': 'https://hub.example.invalid/',
       'HUB_BRIEF_SOURCE': 'http://127.0.0.1:1/today/brief'}


def test_message_carries_the_count_but_no_titles():
    assert brief.message('2 things for today:\n• Private title\n• Another') == '2 things in Hub for today'
    assert brief.message('Nothing needs you today.') is None and brief.message('') is None


def test_sends_link_on_busy_days_nothing_on_clear_days_and_a_warning_when_hub_is_down(monkeypatch):
    sent = []
    monkeypatch.setattr(brief, 'read', lambda source: '1 thing for today:\n• Private title')
    assert brief.main(ENV, sent.append) == 'sent'
    assert sent == [{'title': 'Hub', 'message': '1 thing in Hub for today', 'data': {'url': ENV['HUB_BRIEF_LINK']}}]
    monkeypatch.setattr(brief, 'read', lambda source: 'Nothing needs you today.')
    assert brief.main(ENV, sent.append) == 'clear' and len(sent) == 1
    def down(source):
        raise OSError('synthetic outage')
    monkeypatch.setattr(brief, 'read', down)
    assert brief.main(ENV, sent.append) == 'sent' and sent[-1]['message'] == 'Hub didn’t answer this morning.'


def test_refuses_non_loopback_sources_and_odd_bindings():
    with pytest.raises(ValueError):
        brief.read('http://example.invalid/today/brief')
    with pytest.raises(ValueError):
        brief.main({**ENV, 'HUB_BRIEF_NOTIFY': 'x/../y'}, lambda body: None)
