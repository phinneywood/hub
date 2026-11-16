"""Searching the index: words, people, places and dates, answered from SQLite alone, in milliseconds.

Words are matched with FTS5 (stemmed, so "serviced" finds "service"; accents ignored), all of a search's words in the
same part (a page, a message, a photo's description), and any of several searches. Nothing typed is read as query
syntax: each word is quoted. When every search finds nothing, the words are tried again with any of them allowed,
and the answer says it was loose.

Each source answers on its own: how many matched and the page asked for, newest first for photos, best first for
documents and conversations. Photos also say how many days they come from.
"""
import re

SOURCES = ('photos', 'documents', 'chats')
WORD = re.compile(r'\w+', re.UNICODE)
TOKEN = re.compile(r'"([^"]*)"|([^\s"]+)')
STOP = set("""a an and are as at be been by can could did do does for from had has have how i in into is it its me my
of on or our ours show find search look get give the their them then there these this those to up us was we were what
when where which who whom whose why will with you your""".split())
MOST_HITS = 20000           # matches looked at per source, at most
PUBLIC = ('id', 'source', 'kind', 'title', 'at', 'day', 'until', 'place', 'folder', 'pages', 'size', 'duration', 'w', 'h',
          'favorite', 'shared', 'local', 'thumb')
MARK, END = '\x02', '\x03'  # around the words that matched, in a snippet


def terms(search):
    """The quoted FTS5 terms of one search: phrases kept together, small words left out."""
    out = []
    for phrase, word in TOKEN.findall(search or ''):
        if phrase:
            words = WORD.findall(phrase.lower())
            if words:
                out.append('"' + ' '.join(words) + '"')
            continue
        for piece in WORD.findall(word.lower()):
            if piece not in STOP:
                out.append(f'"{piece}"')
    return list(dict.fromkeys(out))


def expression(searches, loose=False, prefix=False):
    """One FTS5 expression for several searches, any of which may match; '' when no words are left."""
    groups = []
    for search in searches:
        found = terms(search)
        if not found:
            continue
        if prefix and not found[-1].count(' ') and len(found[-1]) > 4:
            found[-1] += '*'
        groups.append('(' + (' OR ' if loose else ' AND ').join(found) + ')')
    return ' OR '.join(dict.fromkeys(groups))


def body_only(match):
    """The same search, looked for in the body column alone: the words of a page or a message, not its title."""
    return '{body} : (' + match + ')'


def _filters(source, kind=(), people=(), anyof=(), young=False, place=None, after=None, before=None, months=(),
             favorite=False, screenshots=None):
    """SQL conditions on items (as `i`) and their values."""
    where, values = ['i.source = ?'], [source]
    if kind:
        where.append(f'i.kind in ({",".join("?" * len(kind))})')
        values += list(kind)
    if after:
        where.append('i.day >= ?')
        values.append(after)
    if before:
        where.append('i.day < ?')
        values.append(before)
    if months:
        where.append(f'cast(substr(i.day, 6, 2) as integer) in ({",".join("?" * len(months))})')
        values += [int(m) for m in months]
    if source == 'photos':
        for name in people:
            where.append('exists (select 1 from item_people ip join people pe on pe.id = ip.person '
                         'where ip.item = i.id and pe.name = ?)')
            values.append(name)
        if anyof or young:
            either = []
            if anyof:
                either.append('exists (select 1 from item_people ip join people pe on pe.id = ip.person '
                              f'where ip.item = i.id and pe.name in ({",".join("?" * len(anyof))}))')
                values += list(anyof)
            if young:
                either.append('i.young = 1')
            where.append('(' + ' or '.join(either) + ')')
        if place:
            where.append("i.place like ? escape '\\'")
            values.append('%' + re.sub(r'([%_\\])', r'\\\1', place) + '%')
        if favorite:
            where.append('i.favorite = 1')
        if screenshots is False:
            where.append('i.screenshot = 0')
    return ' and '.join(where), values


def _matches(conn, match, source, filters):
    """[(item, best part, rank, at, day, score)] for one source."""
    where, values = _filters(source, **filters)
    if match:
        sql = f"""with hits as (select p.item, p.id as part, bm25(fts, 2.0, 1.0) as rank
                                from fts join parts p on p.id = fts.rowid where fts match ?),
                       best as (select item, part, rank, row_number() over (partition by item order by rank, part) as r
                                from hits)
                  select i.id, b.part, b.rank, i.at, i.day, i.score from best b join items i on i.id = b.item
                  where b.r = 1 and {where} limit {MOST_HITS}"""
        return conn.execute(sql, [match, *values]).fetchall()
    sql = f'select i.id, null as part, 0 as rank, i.at, i.day, i.score from items i where {where} limit {MOST_HITS}'
    return conn.execute(sql, values).fetchall()


def spread(rows, limit):
    """At most `limit` photos that show the whole set: the best of each day, from days spread over its span, in
    the order they were taken."""
    days = {}
    for row in sorted(rows, key=lambda r: -(r['score'] or 0)):
        days.setdefault(row['day'], []).append(row)
    order = sorted(days)
    if len(order) > limit:
        step = len(order) / limit
        picked = [days[order[int(n * step)]][0] for n in range(limit)]
    else:
        picked, round_ = [], 0
        while len(picked) < limit and any(len(days[d]) > round_ for d in order):
            picked += [days[d][round_] for d in order if len(days[d]) > round_][:limit - len(picked)]
            round_ += 1
    return sorted(picked, key=lambda r: r['at'] or 0)


def _ordered(rows, sort, source, limit, offset):
    if sort == 'best' and source == 'photos':
        return spread(rows, limit) if offset == 0 else []
    if sort == 'relevance':
        rows = sorted(rows, key=lambda r: (r['rank'], -(r['at'] or 0)))
    elif sort == 'oldest':
        rows = sorted(rows, key=lambda r: r['at'] or 0)
    else:
        rows = sorted(rows, key=lambda r: -(r['at'] or 0))
    return rows[offset:offset + limit]


def public(row, people=()):
    """What Hub may be told about an item: never where its file is."""
    out = {name: row[name] for name in PUBLIC if name in row.keys()}
    for flag in ('favorite', 'shared', 'local', 'thumb'):
        if flag in out:
            out[flag] = bool(out[flag])
    if row['source'] == 'photos':
        out['people'] = list(people)
    return out


def _people_of(conn, ids):
    found = {}
    if ids:
        for row in conn.execute('select ip.item, pe.name from item_people ip join people pe on pe.id = ip.person '
                                f'where ip.item in ({",".join("?" * len(ids))}) order by pe.photos desc', ids):
            found.setdefault(row['item'], []).append(row['name'])
    return found


def _snippets(conn, match, rows):
    """{item: (snippet, page, role)}: the words around what matched, marked; else how the item begins."""
    out = {}
    for row in rows:
        snip = None
        if match and row['part'] is not None:
            in_body = conn.execute("select p.id from fts join parts p on p.id = fts.rowid where fts match ? and p.item = ? "
                                   "and p.body != '' order by bm25(fts, 0.0, 1.0), p.n limit 1", (body_only(match), row['id'])).fetchone()
            part_id = in_body['id'] if in_body else row['part']     # the passage that says it, rather than the title alone
            found = conn.execute(f"select snippet(fts, 1, '{MARK}', '{END}', '…', 18) as body, "
                                 f"snippet(fts, 0, '{MARK}', '{END}', '…', 12) as head from fts where fts match ? and rowid = ?",
                                 (match, part_id)).fetchone()
            if found:
                snip = found['body'] if MARK in (found['body'] or '') else (found['body'] or found['head'])
            part = conn.execute('select page, role from parts where id = ?', (part_id,)).fetchone()
        else:
            part = conn.execute("select page, role, body from parts where item = ? and body != '' order by n limit 1",
                                (row['id'],)).fetchone()
            snip = part['body'][:220] if part else ''
        out[row['id']] = (snip or '', part['page'] if part else None, part['role'] if part else None)
    return out


def search(conn, q=(), sources=SOURCES, sort=None, limit=24, offset=0, prefix=False, **filters):
    """What matched, source by source: {source: {'count', 'items', ...}, 'loose': bool}."""
    q = [s for s in (q or ()) if s and s.strip()]
    match = expression(q, prefix=prefix)
    found, loose = {}, False
    for attempt in ('all', 'any'):
        found = {}
        for source in sources:
            these = dict(filters)
            if source == 'photos':
                if these.get('screenshots') is None:
                    these['screenshots'] = None if match else False      # screenshots come up only for their words
                source_match = match
                if not match and not any(these.get(k) for k in ('kind', 'people', 'anyof', 'young', 'place', 'after',
                                                                 'before', 'months', 'favorite')):
                    found[source] = []      # nothing to go on: never a list of everything
                    continue
            else:
                for name in ('people', 'anyof', 'young', 'place', 'favorite', 'screenshots'):
                    these.pop(name, None)
                named = [f'"{" ".join(WORD.findall(p.lower()))}"' for p in filters.get('people') or () if WORD.findall(p)]
                if named and not match:
                    found[source] = []      # documents and conversations have no people of their own, only words
                    continue
                source_match = ' AND '.join([f'({match})', *named]) if match and named else match
                if not source_match and not any(these.get(k) for k in ('kind', 'after', 'before', 'months')):
                    found[source] = []      # nothing to go on: never a list of everything
                    continue
            found[source] = _matches(conn, source_match, source, these)
        if any(found.values()) or attempt == 'any' or not match or not any(len(terms(s)) > 1 for s in q):
            break
        match, loose = expression(q, loose=True, prefix=prefix), True
    out = {'loose': loose}
    for source, rows in found.items():
        chosen = _ordered(rows, sort or ('newest' if source == 'photos' or not match else 'relevance'), source, limit, offset)
        ids = [r['id'] for r in chosen]
        full = {r['id']: r for r in conn.execute(f'select * from items where id in ({",".join("?" * len(ids))})', ids)} if ids else {}
        people = _people_of(conn, ids) if source == 'photos' else {}
        snippets = _snippets(conn, match, chosen) if source != 'photos' else {}
        items = []
        for row in chosen:
            item = public(full[row['id']], people.get(row['id'], ()))
            if source != 'photos':
                item['snippet'], item['page'], item['role'] = snippets.get(row['id'], ('', None, None))
            items.append(item)
        section = {'count': len(rows), 'items': items}
        if source == 'photos':
            days = sorted({r['day'] for r in rows if r['day']})
            section.update(days=len(days), first=days[0] if days else None, last=days[-1] if days else None)
        out[source] = section
    return out


PASSAGES = 6                    # the most passages of one item that a search's words are given back with


def item(conn, ident, most=120_000, q=()):
    """One item in full, for Hub to show: its facts, and its text page by page or message by message. With `q`, only
    the passages that hold those words (the best PASSAGES), or its first one when only its title did: what Ask's
    model may read of it."""
    row = conn.execute('select * from items where id = ?', (ident,)).fetchone()
    if row is None:
        return None
    import json
    meta = json.loads(row['meta'] or '{}')
    out = public(row, [r['name'] for r in conn.execute(
        'select pe.name from item_people ip join people pe on pe.id = ip.person where ip.item = ? order by pe.photos desc', (ident,))])
    if row['source'] == 'photos':
        out.update({k: meta.get(k) for k in ('albums', 'labels', 'keywords', 'description')})
        return out
    match = expression([w for w in q if w and w.strip()]) if q else ''
    if match:
        parts = conn.execute("select p.page, p.role, p.at, p.body from fts join parts p on p.id = fts.rowid where fts match ? "
                             "and p.item = ? and p.body != '' order by bm25(fts, 0.0, 1.0), p.n limit ?",
                             (body_only(match), ident, PASSAGES)).fetchall()
        parts = sorted(parts, key=lambda p: (p['page'] or 0)) or conn.execute(
            "select page, role, at, body from parts where item = ? and body != '' order by n limit 1", (ident,)).fetchall()
        out['passages'] = True
    else:
        parts = conn.execute("select page, role, at, body from parts where item = ? and body != '' order by n", (ident,)).fetchall()
    kept, text = 0, []
    for part in parts:
        if kept >= most:
            out['cut'] = True
            break
        text.append({'page': part['page'], 'role': part['role'], 'at': part['at'], 'text': part['body'][:most - kept]})
        kept += len(part['body'])
    if row['source'] == 'documents':
        out['ext'] = meta.get('ext')
        out['pages_text'] = text
    else:
        messages = []
        for part in text:
            if messages and messages[-1]['n'] == part['page'] and messages[-1]['role'] == part['role']:
                messages[-1]['text'] += '\n\n' + part['text']
            else:
                messages.append({'n': part['page'], 'role': part['role'], 'at': part['at'], 'text': part['text']})
        out['messages'] = messages
        out['attachments'] = meta.get('attachments', [])
    return out


def people(conn):
    """The people Photos knows by name, most photographed first, with whether they look like a kid."""
    return [{'name': r['name'], 'photos': r['photos'], 'kid': bool(r['kid']), 'last': r['last']}
            for r in conn.execute('select * from people where photos > 0 order by photos desc, name')]


def on_this_day(conn, month_day, year, per_year=8, most=40):
    """Photos taken on this day of the year in earlier years, the best of each year, newest year first."""
    rows = conn.execute("select * from items where source = 'photos' and screenshot = 0 and substr(day, 6, 5) = ? "
                        'and substr(day, 1, 4) < ? order by day desc, score desc', (month_day, str(year))).fetchall()
    years = {}
    for row in rows:
        years.setdefault(row['day'][:4], []).append(row)
    out, total = [], 0
    for year_found in sorted(years, reverse=True):
        best = sorted(years[year_found], key=lambda r: -(r['score'] or 0))[:per_year]
        best = sorted(best, key=lambda r: r['at'] or 0)[:max(0, most - total)]
        if not best:
            break
        people = _people_of(conn, [r['id'] for r in best])
        out.append({'year': int(year_found), 'count': len(years[year_found]),
                    'items': [public(r, people.get(r['id'], ())) for r in best]})
        total += len(best)
    return out


def counts(conn):
    """How much the index holds, source by source and kind by kind."""
    out = {}
    for row in conn.execute('select source, kind, count(*) as n, sum(thumb) as previews, sum(local = 0) as away, '
                            'min(day) as first, max(day) as last from items group by source, kind'):
        here = out.setdefault(row['source'], {'items': 0, 'kinds': {}, 'previews': 0, 'first': row['first'], 'last': row['last']})
        here['items'] += row['n']
        here['previews'] += row['previews'] or 0
        here['kinds'][row['kind']] = row['n']
        here['first'] = min(here['first'] or row['first'], row['first'] or here['first']) if row['first'] else here['first']
        here['last'] = max(here['last'] or row['last'], row['last'] or here['last']) if row['last'] else here['last']
        if row['source'] == 'documents':
            here['in_icloud'] = here.get('in_icloud', 0) + (row['away'] or 0)
    return out
