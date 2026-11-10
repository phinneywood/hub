"""Topic-first class website projection. No model calls, mail access or source writes.

Only exact duplicate facts are combined. Different facts are not silently treated
as corrections; a maintained topic page can replace their presentation, with history.
"""
import hashlib
import re
from datetime import date, timedelta

from .extract import safe_url

TOPICS = {
    'routines': ('Class routines', 'The class day, arrival, pickup and regular routines.'),
    'preparation': ('What to bring & prepare', 'Supplies, clothing and preparation for class.'),
    'participation': ('Parent participation', 'Class duties, meetings and co-op responsibilities.'),
    'fees': ('Tuition & fees', 'Stated class costs, contributions and payment deadlines.'),
    'updates': ('Class announcements', 'Classroom news and information families need to know.'),
}


def normalized(value):
    return ' '.join(str(value or '').split()).casefold()


def topic_for(item):
    text = normalized(item.get('title')) + ' ' + normalized(item.get('details'))
    if item.get('kind') == 'resource':
        return 'resources'
    if item.get('kind') == 'event':
        return 'dates'
    if re.search(r'\b(tuition|fees?|contribution|dues|billing|payment)\b', text):
        return 'fees'
    if re.search(r'\b(bring|boots|clothing|supplies|prepare|preparation|snacks?|gloves)\b', text):
        return 'preparation'
    if re.search(r'\b(pickup|pick.up|drop.off|arrival|routine|class day|daily schedule)\b', text):
        return 'routines'
    if item.get('kind') == 'action' or re.search(r'\b(duty|duties|participation|volunteer|parent meeting|work party)\b', text):
        return 'participation'
    return 'updates'


def uncertain(item):
    text = str(item.get('uncertainty') or '').strip()
    for prefix in ('From an attachment.', 'From an allowlisted linked source.'):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return bool(text)


def date_label(value):
    try:
        parsed = date.fromisoformat(value)
        return parsed.strftime('%a, %b ') + str(parsed.day) + parsed.strftime(', %Y')
    except (TypeError, ValueError):
        return value or 'Date not stated'


def facts(items):
    grouped = {}
    for item in items:
        identity = tuple(normalized(item.get(key)) for key in
                         ('kind', 'title', 'details', 'date', 'time', 'location', 'url'))
        key = hashlib.sha256('\0'.join(identity).encode()).hexdigest()[:24]
        if key not in grouped:
            grouped[key] = {**item, 'key': key, 'topic': topic_for(item), 'sources': [],
                            'href': '/preschool/guide/' + topic_for(item) + '#fact-' + key,
                            'date_display': date_label(item.get('date'))}
            grouped[key]['url']=safe_url(item.get('url'),item.get('url') or '')
        row = grouped[key]
        if item.get('view_key') not in {source['view_key'] for source in row['sources']}:
            row['sources'].append(item)
    for row in grouped.values():
        if row['topic'] in ('dates', 'resources'):
            row['href'] = '/preschool/' + row['topic'] + '#fact-' + row['key']
    return sorted(grouped.values(), key=lambda row: (row.get('date') or '9999', normalized(row['title']), row['key']))


def page_text(topic):
    """Faithful initial page text from known facts; no inferred corrections or dates."""
    paragraphs=[]
    for row in topic['supporting_facts']:
        lines=[row['title']]
        if row.get('details'):lines.append(row['details'])
        metadata=[row['date_display']] if row.get('date') else []
        metadata += [row[key] for key in ('time','location') if row.get(key)]
        if metadata:lines.append(' · '.join(metadata))
        paragraphs.append('\n'.join(lines))
    return '\n\n'.join(paragraphs)


def project(items, calendar, articles, today, timetables=()):
    today = date.fromisoformat(today)
    all_facts = facts([item for item in items if not item.get('dismissed') and not uncertain(item)])
    past = [row for row in all_facts if row.get('date') and row['date'] < today.isoformat()]
    current = [row for row in all_facts if row not in past]
    from . import schedule
    generated=schedule.occurrences(timetables,today.isoformat(),items)
    for row in generated:row['date_display']=date_label(row['date'])
    current+=generated
    current.sort(key=lambda row:(row.get('date') or '9999',row['title']))
    all_facts+=generated
    topics = []
    for slug, (title, description) in TOPICS.items():
        topic_facts = [row for row in current if row['topic'] == slug]
        article = articles.get(slug)
        # An authored page is the current account of a topic. New source facts do
        # not append competing summaries to it; they wait in maintenance.
        published_keys = set(article['source_keys']) if article else set()
        unincorporated = [row for row in topic_facts if not {s['view_key'] for s in row['sources']} <= published_keys] if article else []
        source_keys = published_keys if article else {s['view_key'] for row in topic_facts for s in row['sources']}
        sources = [item for item in items if item['view_key'] in source_keys]
        topics.append({'slug':slug, 'title':title, 'description':description,
                       'href':'/preschool/guide/' + slug, 'article':article,
                       'facts':[] if article else topic_facts, 'supporting_facts':topic_facts,
                       'sources':sources, 'pending':unincorporated,
                       'has_content':bool(article or topic_facts)})
    # Facts covered by an authored topic are no longer presented independently
    # elsewhere on the site. This prevents an old deadline contradicting a correction.
    covered = {topic['slug'] for topic in topics if topic['article']}
    visible = [row for row in current if row['topic'] not in covered]
    dated = [row for row in visible if row.get('date') and row.get('kind') in ('action', 'event')]
    week_end = today + timedelta(days=7)
    this_week = [row for row in dated if row['date'] < week_end.isoformat()]
    later = [row for row in dated if row['date'] >= week_end.isoformat()]
    school = [{**event, 'date_display':date_label(event.get('date_iso'))} for event in calendar
              if event.get('date_iso') and event['date_iso'] >= today.isoformat()]
    school.sort(key=lambda event:(event['date_iso'],event.get('start_time') or '',event['event']))
    school_past=[{**event,'date_display':date_label(event.get('date_iso'))} for event in calendar
                 if event.get('date_iso') and event['date_iso'] < today.isoformat()]
    return {'topics':topics, 'facts':all_facts, 'current':visible, 'past':past, 'dated':dated,
            'this_week':this_week[:8], 'this_week_more':len(this_week)>8, 'coming_up':later[:5],
            'responsibilities':[row for row in visible if row['kind']=='action'],
            'past_due':[row for row in past if row['kind']=='action' and row['topic'] not in covered],
            'events':[row for row in visible if row['kind']=='event'],
            'resources':[row for row in visible if row['kind']=='resource'],
            'school_calendar':school, 'school_past':school_past, 'today':today.isoformat(),
            'week_label':date_label(today.isoformat()) + ' – ' + date_label((week_end-timedelta(days=1)).isoformat()),
            'held_source_count':sum(uncertain(item) for item in items if not item.get('dismissed'))}
