"""The Hub app's committed bundle must be the one built from its committed source.

`npm run build` in web/app writes hub/static/app/build.json: a fingerprint of each source file and of
each built file. Here both are recomputed. A source edit without a rebuild, or a bundle changed by
hand, fails. The installed package has no web/ folder, so there only the built files are checked.
"""
import hashlib
import json
import re
from pathlib import Path

import hub

BUILT = Path(hub.__file__).parent / 'static' / 'app'
SOURCE = Path(__file__).resolve().parents[1] / 'web' / 'app'
BY_HAND = {'fonts', 'sw.js'}    # placed in the folder by hand; the build leaves them alone and records no hash


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record():
    return json.loads((BUILT / 'build.json').read_text())


def test_the_built_files_are_the_ones_the_build_recorded_and_nothing_unexpected_is_there():
    record = _record()
    assert set(record['built']) == {'app.js', 'app.css'}
    ours = set(record['built']) | {'build.json'}
    there = {p.name for p in BUILT.iterdir()}
    assert ours <= there <= ours | BY_HAND, there
    for name, digest in record['built'].items():
        assert _sha(BUILT / name) == digest, f'{name} was changed after the build'


def test_the_bundle_was_built_from_the_source_in_this_checkout():
    if not SOURCE.is_dir():
        return  # an installed package: the source isn't shipped, and the test above still guards the files
    record = _record()['source']
    # every file under src at any depth; dot files (Finder's, an editor's) are not source, as in record-build.mjs
    inside = (p.relative_to(SOURCE) for p in (SOURCE / 'src').rglob('*') if p.is_file())
    here = {'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json', 'record-build.mjs'} | {
        p.as_posix() for p in inside if not any(part.startswith('.') for part in p.parts)}
    assert set(record) == here, 'web/app has files the last build did not see, or lost some; run tools/build-app'
    for name, digest in record.items():
        assert _sha(SOURCE / name) == digest, f'web/app/{name} changed since the last build; run tools/build-app and commit'


def test_the_bundle_talks_to_no_other_site_from_the_page():
    """Hub's content policy already blocks other origins; this keeps a new one from arriving unnoticed."""
    script = (BUILT / 'app.js').read_text()
    hosts = set(re.findall(r'https?://([a-zA-Z0-9.-]+)', script + (BUILT / 'app.css').read_text()))
    assert hosts <= {'www.w3.org'}, hosts   # the SVG and MathML namespaces Preact names; never requested


def test_the_bundle_needs_nothing_the_content_policy_refuses():
    """`default-src 'self'` refuses eval, inline styles and data: addresses; none may be built in."""
    script, styles = (BUILT / 'app.js').read_text(), (BUILT / 'app.css').read_text()
    assert not re.search(r'\beval\(|\bnew Function\(', script)
    assert 'sourceMappingURL' not in script + styles
    # Every address in the stylesheet is a file Hub ships under /static/. The build only warns about a
    # missing one and leaves its address as written, which the browser would then ask Hub's root for.
    static = BUILT.parent
    for address in re.findall(r'url\(\s*["\']?([^"\')]+)', styles):
        assert address.startswith('/static/'), f'{address} is not a file under /static/'
        assert (static / re.split(r'[?#]', address[len('/static/'):])[0]).is_file(), f'{address} has no file behind it'
