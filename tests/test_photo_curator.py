"""The photo curator (deploy/archive/curator.py): choosing a month and acting on marks. Fictional photos only;
the Photos library and Photos itself are never reached (osxphotos and photoscript are stood in for)."""
import datetime as dt
import json
from pathlib import Path

import pytest

from tests.archive_world import program

curator = program('curator')
TZ = dt.timezone(dt.timedelta(hours=-8))


def shot(n, day, minute, score, **more):
    return curator.Shot(f'{n:08d}-0000-0000-0000-000000000000', dt.datetime(2026, 10, day, 12, minute, tzinfo=TZ), score, **more)


def test_one_pick_per_moment_and_favorites_and_people_count():
    burst = [shot(1, 3, 0, .5), shot(2, 3, 1, .7), shot(3, 3, 2, .6)]          # one moment: only its best can be a pick
    fav = shot(4, 4, 0, .3, favorite=True)
    people = shot(5, 5, 0, .65, people=['Alex'])
    plain = shot(6, 6, 0, .72)
    picks, more = curator.choose(burst + [fav, people, plain], count=3, more=5)
    assert [p.id[:8] for p in picks] == ['00000004', '00000005', '00000006']    # in the order they were taken
    assert [p.id[:8] for p in more] == ['00000002']


def test_a_busy_day_gives_way_to_other_days_but_fills_a_quiet_month():
    party = [shot(i, 10, i * 5, .9) for i in range(1, 7)]
    other = [shot(20, 11, 0, .2), shot(21, 12, 0, .1)]
    picks, _ = curator.choose(party + other, count=6, per_day=4)
    assert sum(p.taken.day == 10 for p in picks) == 4 and {p.taken.day for p in picks} == {10, 11, 12}
    picks, _ = curator.choose(party, count=6, per_day=4)
    assert len(picks) == 6


def test_months_and_bounds_and_names():
    assert curator.months_between(dt.date(2025, 10, 20), dt.date(2026, 1, 1)) == ['2025-10', '2025-11', '2025-12', '2026-01']
    assert curator.last_month(dt.date(2026, 1, 15)) == '2025-12'
    start, end = curator.bounds('2025-03', since=dt.date(2025, 3, 12))
    assert start.date() == dt.date(2025, 3, 12) and end.date() == dt.date(2025, 4, 1)
    assert curator.bounds('2025-04', since=dt.date(2025, 3, 12))[0].date() == dt.date(2025, 4, 1)
    assert curator.album_name('2026-10') == 'October 2026'


def chosen(*ids):
    return {'picks': [{'id': i} for i in ids], 'more': []}


A, B, C, D = (f'{c * 8}-0000-0000-0000-000000000000' for c in 'ABCD')


def test_only_chosen_photos_can_be_acted_on_and_each_only_once():
    marks = {A: {'mark': 'keep'}, B: {'mark': 'print'}, C: {'mark': 'skip'}, D: {'mark': 'keep'}}
    todo = curator.pending(marks, {'album': [], 'print': []}, {'2026-10': chosen(A, B, C)})
    assert todo == {'album': {'2026-10': [A, B]}, 'print': {'2026-10': [B]}}          # D was never chosen
    assert curator.pending(marks, {'album': [A, B], 'print': [B]}, {'2026-10': chosen(A, B, C)}) == {'album': {}, 'print': {}}


class FakePhotos:
    """Stands in for photoscript: records what would have been asked of Photos."""
    def __init__(self):
        self.asked = []
        fake = self

        class Album:
            def __init__(self, name):
                self.name = name

            def add(self, photos):
                fake.asked.append(('add', self.name, [p.id for p in photos]))

        class Folder:
            def album(self, name):
                return None

            def create_album(self, name):
                fake.asked.append(('album', name))
                return Album(name)

        class Library:
            def folder(self, name, top_level=True):
                return None

            def create_folder(self, name):
                fake.asked.append(('folder', name))
                return Folder()

        class Photo:
            def __init__(self, uuid):
                self.id = uuid

            def export(self, path, original=False, timeout=0):
                fake.asked.append(('export', self.id, Path(path).name))
                return [path]

        self.PhotosLibrary, self.Photo = Library, Photo


def test_apply_adds_to_its_own_album_saves_prints_and_remembers(tmp_path, monkeypatch):
    monkeypatch.setenv('PHOTO_CURATOR_DIR', str(tmp_path / 'curator'))
    monkeypatch.setenv('PHOTO_CURATOR_PRINTS', str(tmp_path / 'prints'))
    here = tmp_path / 'curator'
    (here / 'months').mkdir(parents=True)
    (here / 'months' / '2026-10.json').write_text(json.dumps(chosen(A, B)))
    (here / 'marks.json').write_text(json.dumps({A: {'mark': 'keep'}, B: {'mark': 'print'}}))
    photos = FakePhotos()
    assert curator.apply(photos) == {'album': 2, 'print': 1}
    assert photos.asked == [('folder', 'Photo picks'), ('album', 'October 2026'), ('add', 'October 2026', [A, B]),
                            ('export', B, 'October 2026')]
    again = FakePhotos()
    assert curator.apply(again) == {'album': 0, 'print': 0} and again.asked == []
