"""The archive program (deploy/archive): what it indexes and leaves out, and how it searches. A made-up archive
(tests/archive_world.py) with stand-ins for Photos and macOS; no real library, document or export is read."""
import datetime as dt
import json
import os
import sqlite3

import pytest

from tests import archive_world as world

store = world.program('store')
search = world.program('search')
text = world.program('text')
chats = world.program('chats')
documents = world.program('documents')
photos = world.program('photos')
index = world.program('index')


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        home, mac, uuids = world.build(tmp_path_factory.mktemp('archive'), patch)
        conn = store.connect(read_only=True)
        yield conn, home, mac, uuids
        conn.close()


def ids(section):
    return [i['id'] for i in section['items']]


def find(conn, *q, **more):
    return search.search(conn, q=list(q), **more)


# ---- what is indexed, and what is left out ----

def test_the_folder_and_everything_in_it_is_owner_only(built):
    _, home, _, _ = built
    assert home.stat().st_mode & 0o777 == 0o700
    assert (home / 'archive.db').stat().st_mode & 0o777 == 0o600
    previews = [p for p in (home / 'thumbs').rglob('*') if p.is_file()]
    assert previews and all(p.stat().st_mode & 0o777 == 0o600 for p in previews)
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in [home / 'thumbs', *(home / 'thumbs').rglob('*')] if p.is_dir())


def test_hidden_deleted_and_burst_extras_are_left_out(built):
    conn, _, _, _ = built
    kept = {r['id'][2:10] for r in conn.execute("select id from items where source = 'photos'")}
    assert not {f'{n:08X}' for n in (16, 17, 18)} & kept           # hidden, deleted, the extra shot of a burst
    assert f'{15:08X}' in kept and len(kept) == 39


def test_what_photos_read_in_an_id_document_is_never_searchable(built):
    conn, _, _, _ = built
    assert find(conn, 'license')['photos']['count'] == 0
    assert find(conn, '9876')['photos']['count'] == 0
    assert find(conn, 'furnace')['photos']['count'] == 1           # a service sticker is found by its text


def test_documents_only_never_code_projects_hidden_files_or_the_export(built):
    conn, _, _, _ = built
    titles = {r['title'] for r in conn.execute("select title from items where source = 'documents'")}
    assert titles == {'Furnace service invoice.pdf', 'Paint.md', 'Scan of warranty.pdf', '2024 summary.csv', 'receipt.jpg',
                      'Budget.numbers', 'Field trip form.pdf', 'label.jpg'}
    assert 'sunset.jpg' not in titles and 'beach.jpg' not in titles         # pictures are kept only for their text


def test_a_scanned_page_is_read_by_vision_and_text_is_kept_page_by_page(built):
    conn, _, _, _ = built
    found = find(conn, 'warranty', sources=['documents'])['documents']['items']
    assert found[0]['title'] == 'Scan of warranty.pdf' and found[0]['page'] == 1 and found[0]['pages'] == 2


def test_icloud_documents_are_fetched_documents_first_and_read(built):
    conn, _, mac, _ = built
    assert mac.asked[0] == 'Field trip form.pdf' and sorted(mac.asked[1:]) == ['beach.jpg', 'label.jpg']
    found = find(conn, 'aquarium', sources=['documents'])['documents']['items']
    assert [d['title'] for d in found] == ['Field trip form.pdf'] and found[0]['local'] is True


def test_conversations_follow_the_branch_they_ended_on_without_thinking(built):
    conn, _, _, _ = built
    furnace = search.item(conn, 'c:c0ffee00-0000-4000-8000-000000000001')
    said = [m['text'] for m in furnace['messages'] if m['role'] != 'file']
    assert said == ['What size furnace filter fits a 16x25 return?', 'A 16x25x1 filter fits. Replace it every three months.',
                    'Thanks, ordering MERV 8.']
    assert find(conn, 'zeppelins', sources=['chats'])['chats']['count'] == 0      # said on a branch that was left
    assert find(conn, '"thought for"', sources=['chats'])['chats']['count'] == 0
    assert sorted(furnace['attachments']) == ['Warranty.pdf', 'filter notes.md']
    files = [m['text'] for m in furnace['messages'] if m['role'] == 'file']
    assert any('MERV 8 is fine' in t for t in files) and any('Ten year parts warranty' in t for t in files)


def test_voice_conversations_and_the_group_chat_are_found(built):
    conn, _, _, _ = built
    assert [c['title'] for c in find(conn, 'sunscreen', sources=['chats'])['chats']['items']] == ['Beach packing list']
    group = find(conn, 'umbrella', sources=['chats'])['chats']['items']
    assert [c['title'] for c in group] == ['Family trip'] and group[0]['id'].startswith('g:')
    assert find(conn, '"low tide"', sources=['chats'])['chats']['count'] == 1


def test_kids_are_people_whose_recent_faces_are_a_childs(built):
    conn, _, _, _ = built
    assert {p['name']: p['kid'] for p in search.people(conn)} == {'Alex': False, 'Sam': True}


def test_the_age_rule_on_its_own():
    now = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc)
    recent = [(now - dt.timedelta(days=d), 2) for d in range(0, 300, 20)]
    grew_up = [(now - dt.timedelta(days=365 * 20 + d), 2) for d in range(15)] + [(now - dt.timedelta(days=d), 5) for d in range(15)]
    long_ago = [(now - dt.timedelta(days=365 * 5 + d), 1) for d in range(30)]
    few = [(now, 2)] * 3
    assert photos.kids({'Sam': recent, 'Alex': grew_up, 'Old': long_ago, 'Few': few}, now) == {'Sam'}


# ---- searching ----

def test_kids_at_the_beach_in_2024(built):
    conn, _, _, _ = built
    kids = find(conn, 'beach', sources=['photos'], anyof=['Sam'], young=True, after='2024-01-01', before='2025-01-01')
    assert kids['photos']['count'] == 9 and kids['photos']['days'] == 2
    assert (kids['photos']['first'], kids['photos']['last']) == ('2024-07-13', '2024-08-02')
    unnamed = find(conn, 'kite', sources=['photos'], anyof=['Sam'], young=True)['photos']
    assert unnamed['count'] == 1                                   # an unnamed child counts as one of the kids


def test_named_people_must_all_be_there(built):
    conn, _, _, _ = built
    both = find(conn, sources=['photos'], people=['Alex', 'Sam'])['photos']
    assert both['count'] == 4 and all({'Alex', 'Sam'} <= set(p['people']) for p in both['items'])


def test_months_places_and_dates(built):
    conn, _, _, _ = built
    assert find(conn, sources=['photos'], months=[11], people=['Alex'])['photos']['count'] == 2
    assert find(conn, sources=['photos'], place='seaside')['photos']['count'] == 9
    assert find(conn, 'cake', sources=['photos'], before='2023-01-01')['photos']['count'] == 0


def test_words_are_stemmed_and_never_read_as_query_syntax(built):
    conn, _, _, _ = built
    assert find(conn, 'servicing', sources=['documents'])['documents']['count'] == 1
    for odd in ('furnace AND', 'NEAR(furnace', '"', 'furnace*', '^furnace', 'title:furnace', '(()', "furnace's"):
        find(conn, odd)                                            # never an error


def test_all_words_or_else_any_and_the_answer_says_so(built):
    conn, _, _, _ = built
    strict = find(conn, 'furnace invoice', sources=['documents'])
    assert not strict['loose'] and [d['title'] for d in strict['documents']['items']] == ['Furnace service invoice.pdf']
    loose = find(conn, 'furnace zebra', sources=['documents'])
    assert loose['loose'] and loose['documents']['count'] >= 1


def test_snippets_mark_what_matched(built):
    conn, _, _, _ = built
    found = find(conn, 'tune-up', sources=['documents'])['documents']['items'][0]
    assert f'{search.MARK}tune{search.END}' in found['snippet'] and found['page'] == 1


def test_several_searches_any_of_which_may_match(built):
    conn, _, _, _ = built
    found = find(conn, 'aquarium', 'tune-up', sources=['documents'])['documents']
    assert {d['title'] for d in found['items']} == {'Field trip form.pdf', 'Furnace service invoice.pdf'}


def test_nothing_to_go_on_lists_nothing(built):
    conn, _, _, _ = built
    found = find(conn, 'the of and', sources=['photos', 'documents', 'chats'])
    assert all(found[s]['count'] == 0 for s in ('photos', 'documents', 'chats'))


def test_a_spread_of_the_best_shows_every_day(built):
    conn, _, _, _ = built
    picked = find(conn, 'beach', sources=['photos'], sort='best', limit=3)['photos']['items']
    assert len(picked) == 3 and {p['day'] for p in picked} == {'2024-07-13', '2024-08-02'}
    assert [p['at'] for p in picked] == sorted(p['at'] for p in picked)


def test_screenshots_come_up_only_for_their_words(built):
    conn, _, _, _ = built
    assert find(conn, sources=['photos'], after='2025-06-01', before='2025-06-02')['photos']['count'] == 0
    assert find(conn, 'boarding', sources=['photos'])['photos']['count'] == 1


def test_on_this_day(built):
    conn, _, _, _ = built
    years = search.on_this_day(conn, '11-16', 2026)
    assert [y['year'] for y in years] == [2023, 2022] and set(years[0]['items'][0]['people']) == {'Alex', 'Sam'}


def test_what_hub_is_told_never_says_where_a_file_is(built):
    conn, _, _, _ = built
    every = find(conn, 'furnace')
    for source in ('photos', 'documents', 'chats'):
        for item in every[source]['items']:
            assert 'path' not in item and 'meta' not in item and 'sig' not in item
    assert 'path' not in json.dumps(search.item(conn, every['documents']['items'][0]['id']))


# ---- running it again ----

def test_a_second_run_changes_nothing_and_a_gone_document_is_forgotten(tmp_path, monkeypatch):
    home, mac, _ = world.build(tmp_path, monkeypatch)
    conn = store.connect()
    before = conn.execute('select count(*) from items').fetchone()[0]
    counts, _ = documents.index(conn, mac=mac, wait=0, say=lambda *_: None)
    assert counts['new'] == counts['changed'] == counts['removed'] == 0
    invoice = tmp_path / 'files' / 'Documents' / 'Home' / 'Furnace service invoice.pdf'
    gone_id = documents.item_id(invoice)
    assert store.thumb_path(gone_id).exists()
    invoice.unlink()
    counts, _ = documents.index(conn, mac=mac, wait=0, say=lambda *_: None)
    assert counts['removed'] == 1 and not store.thumb_path(gone_id).exists()
    assert conn.execute('select count(*) from items').fetchone()[0] == before - 1
    assert not (tmp_path / 'files' / 'Documents' / 'Home').joinpath('Furnace service invoice.pdf').exists()
    assert (tmp_path / 'files' / 'Documents' / 'Home' / 'Paint.md').exists()       # sources are only read


def test_a_library_that_answers_with_nothing_removes_nothing(tmp_path, monkeypatch):
    world.build(tmp_path, monkeypatch)
    conn = store.connect()
    before = conn.execute("select count(*) from items where source = 'photos'").fetchone()[0]
    with pytest.raises(RuntimeError):
        photos.index(conn, db=world.Library([]), thumb=lambda item: None, say=lambda *_: None)
    assert conn.execute("select count(*) from items where source = 'photos'").fetchone()[0] == before


def test_a_run_records_each_source_and_one_failing_leaves_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv('HUB_ARCHIVE_DIR', str(tmp_path / 'archive'))
    monkeypatch.setenv('HUB_ARCHIVE_ROOTS', json.dumps({'Documents': str(tmp_path / 'none')}))
    monkeypatch.setenv('HUB_ARCHIVE_CHATGPT', str(tmp_path / 'no-export'))

    def broken(conn, say=print):
        raise sqlite3.OperationalError('a library that cannot be opened')
    monkeypatch.setitem(index.PASSES, 'photos', broken)
    done = index.run(say=lambda *_: None)
    assert done['photos'] is None and done['documents']['items'] == 0 and done['chats']['items'] == 0
    runs = store.status(store.connect(read_only=True))
    assert runs['photos']['errors'] == {'OperationalError': 1} and runs['photos']['ok'] is None
    assert runs['chats']['ok'] is not None


def test_one_run_at_a_time(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv('HUB_ARCHIVE_DIR', str(tmp_path))
    with open(tmp_path / 'index.lock', 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        said = []
        assert index.run(say=said.append) == {} and said == ['Another run is indexing already.']


# ---- the pieces ----

def test_text_is_cut_where_paragraphs_and_sentences_end():
    pieces = text.chunks('One. ' * 300 + '\n\n' + 'Two words.')
    assert all(len(p) <= text.SIZE for p in pieces) and pieces[-1].endswith('Two words.')
    assert text.chunks('word ' * 1000)[0].endswith('word')
    assert text.clean('a\x00b \t c\r\n\n\n\nd') == 'a b c\n\nd'


def test_the_query_quotes_every_word():
    assert search.expression(['furnace NEAR(x', 'the AND or']) == '("furnace" AND "near" AND "x")'
    assert search.expression(['"tune up" filter']) == '("tune up" AND "filter")'
    assert search.expression(['furnace filter'], loose=True) == '("furnace" OR "filter")'


def test_real_pdfkit_and_vision_read_the_made_up_invoice(tmp_path):
    macos = world.program('macos')
    pytest.importorskip('Quartz')
    path = tmp_path / 'invoice.pdf'
    path.write_bytes(world.pdf(world.INVOICE, ['Page two']))
    pages = macos.pdf_pages(path)
    assert len(pages) == 2 and 'Furnace service' in pages[0] and 'Page two' in pages[1]
    data, w, h = macos.pdf_page(path, 1, 400)
    assert data[:2] == b'\xff\xd8' and max(w, h) == 400
    assert 'Furnace' in macos.ocr_pdf_page(path, 1)                 # Vision reads a page's picture
    assert os.path.getsize(path) > 0
