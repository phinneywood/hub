"""The Ask page's committed bundle must be the one built from its committed source.

`npm run build` in web/ask writes hub/static/ask/build.json: a fingerprint of each source file and of
each built file. Here both are recomputed. A source edit without a rebuild, or a bundle changed by
hand, fails. The installed package has no web/ folder, so there only the built files are checked.
"""
import hashlib
import json
from pathlib import Path

import hub

BUILT = Path(hub.__file__).parent / 'static' / 'ask'
SOURCE = Path(__file__).resolve().parents[1] / 'web' / 'ask'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record():
    return json.loads((BUILT / 'build.json').read_text())


def test_the_built_files_are_the_ones_the_build_recorded_and_nothing_else_is_there():
    record = _record()
    assert set(record['built']) == {'ask.js', 'ask.css'}
    assert {p.name for p in BUILT.iterdir()} == set(record['built']) | {'build.json'}
    for name, digest in record['built'].items():
        assert _sha(BUILT / name) == digest, f'{name} was changed after the build'


def test_the_bundle_was_built_from_the_source_in_this_checkout():
    if not SOURCE.is_dir():
        return  # an installed package: the source isn't shipped, and the test above still guards the files
    record = _record()['source']
    here = {'package.json', 'package-lock.json', 'vite.config.js', 'record-build.mjs'} | {
        'src/' + p.name for p in (SOURCE / 'src').iterdir() if p.is_file()}
    assert set(record) == here, 'web/ask has files the last build did not see; run npm run build'
    for name, digest in record.items():
        assert _sha(SOURCE / name) == digest, f'web/ask/{name} changed since the last build; run npm run build and commit'


def test_the_bundle_talks_to_no_other_site_from_the_page():
    """Hub's content policy already blocks other origins; this keeps a new one from arriving unnoticed."""
    import re
    hosts = set(re.findall(r'https?://([a-zA-Z0-9.-]+)', (BUILT / 'ask.js').read_text()))
    assert hosts <= {'www.w3.org', 'json-schema.org', 'react.dev', 'github.com', 'backend.assistant-api.com'}, hosts
