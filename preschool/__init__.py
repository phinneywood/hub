"""Topic-first class website with separate source maintenance; no calendar writes."""
import re
import threading
import time
import hashlib
from datetime import date, datetime
from email.utils import getaddresses, parsedate_to_datetime
from functools import wraps
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .support import bridge, luna, senders
from .templating import render
from . import criteria, extract, mail, publication, scanner, schedule, school, school_calendar, site_store, store, website

router = APIRouter()
ID_RE = re.compile(r'^[a-fA-F0-9]{8,32}$')
PAGE_RE = re.compile(r'^[A-Za-z0-9_=-]{1,2048}$')
LABELS = {'action': 'To-dos & deadlines', 'event': 'Dates & events',
          'update': 'Class notes', 'resource': 'Forms & resources'}
REVIEW_REASON_TEXT = {
    'changed_source': 'A cancellation, postponement or rescheduled date needs direct source review.',
    'jev_uncertain': 'The automatic relevance check was uncertain under the handbook rules.',
    'jev_unavailable': 'The automatic relevance check could not complete; no automatic retry was made.',
    'luna_failed': 'The automatic summary could not be verified against its source; no automatic retry was made.',
    'gmail_read_failed': 'The message or attachment could not be read safely.',
    'source_unavailable': 'A linked public source was unavailable or redirected outside the allowlist.',
    'unsupported_attachment': 'An attachment type is not supported by the local reader.',
    'unreadable_attachment': 'An attachment could not be read within the configured limits.',
    'attachment_read_failed': 'An attachment could not be parsed safely.',
    'too_many_attachments': 'There are more attachments than the automatic reader can safely inspect.',
    'source_too_large': 'The combined email and source material exceeded the extraction limits.',
    'source_truncated': 'A source was truncated and needs direct inspection.',
    'off_allowlist': 'A relevant link points outside the approved public-source allowlist.',
    'query_requires_review': 'A link contains access parameters and was not followed.',
    'too_many_resource_links': 'There are more linked resources than the automatic reader can safely inspect.',
    'empty_link_source': 'A linked public source contained no readable text.',
    'extraction_invalid': 'The extraction output could not be verified against its source.',
    'validation_failed': 'A source identity or field failed validation.',
    'internal_error': 'Processing stopped safely; inspect the original email before retrying.',
    'interrupted': 'Processing was interrupted; no automatic retry was made.'
}


def go(path='/preschool'):
    path = path.replace('/preschool/shared', '/preschool', 1)
    if path in ('/preschool', '/preschool/') or path.startswith('/preschool#'):
        path = '/preschool/manage'
    return RedirectResponse(path, status_code=303)


def page(request, name, **context):
    shared = store.current_account() == 'shared'
    mailbox_name = store.setting('mailbox', '') if shared else ''
    gmail_base = ('https://mail.google.com/mail/?authuser=' + quote(mailbox_name, safe='') + '#all/'
                  if mailbox_name else 'https://mail.google.com/mail/#all/')
    return render(request, 'preschool/' + name + '.html', active='preschool',
                  base_path='/preschool', shared_account=shared,
                  gmail_base=gmail_base, **context)


def _view_key(kind, account, local_id):
    return hashlib.sha256(f'preschool\0{kind}\0{account}\0{local_id}'.encode()).hexdigest()


def _gmail_url(account, mailbox_name, message_id):
    base = ('https://mail.google.com/mail/?authuser=' + quote(mailbox_name, safe='') + '#all/'
            if account == 'shared' and mailbox_name else 'https://mail.google.com/mail/#all/')
    return base + str(message_id)


def _all_mailbox_data(dismissed=False):
    items, candidates, needs_review, filtered, messages, senders_all = [], [], [], [], [], set()
    counts = {}
    auto_modes, statuses, scan_times, scan_completions, backfills = [], [], [], [], []
    for account in ('personal', 'shared'):
        with store.account(account):
            mailbox_name = store.setting('mailbox', '')
            for item in store.all_items(include_dismissed=dismissed):
                item['view_key'] = _view_key('item', account, item['id'])
                item['message_key'] = _view_key('message', account, item['message_id'])
                item['gmail_url'] = _gmail_url(account, mailbox_name, item['message_id'])
                items.append(item)
            rows = store.messages()
            for message in rows:
                message['view_key'] = _view_key('message', account, message['id'])
                message['gmail_url'] = _gmail_url(account, mailbox_name, message['id'])
                messages.append(message)
            for state, target in (('new', candidates), ('ready', candidates), ('needs_review', needs_review), ('filtered', filtered)):
                for candidate in store.candidates(state):
                    candidate['view_key'] = _view_key('candidate', account, candidate['id'])
                    candidate['gmail_url'] = _gmail_url(account, mailbox_name, candidate['id'])
                    target.append(candidate)
            senders_all.update(store.senders())
            counts[account] = store.candidate_counts()
            auto_modes.append(store.setting('auto_enabled', '0') == '1')
            statuses.append(store.setting('scan_status', 'Waiting for first check'))
            try:
                scan_times.append(float(store.setting('scan_last_attempt', '0')))
            except (TypeError, ValueError):
                pass
            try:
                scan_completions.append(float(store.setting('scan_complete', '0')))
            except (TypeError, ValueError):
                scan_completions.append(0)
            if store.setting('backfill_enabled', '0') == '1':
                backfills.append(store.setting('backfill_status', 'Backfill running'))
    return items, candidates, needs_review, filtered, messages, sorted(senders_all), counts, all(auto_modes), statuses, max(scan_times, default=0), backfills, min(scan_completions, default=0)


def _resolve_candidate(key):
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        return None
    for account in ('personal', 'shared'):
        with store.account(account):
            for state in ('new', 'ready', 'needs_review', 'filtered', 'dismissed', 'processed'):
                for candidate in store.candidates(state):
                    if _view_key('candidate', account, candidate['id']) == key:
                        return account, candidate
    return None


def _resolve_item(key):
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        return None
    for account in ('personal', 'shared'):
        with store.account(account):
            for item in store.all_items(include_dismissed=True):
                if _view_key('item', account, item['id']) == key:
                    return account, item
    return None


def _merge_backfill():
    active = []
    statuses = []
    can_start = False
    for account in ('personal', 'shared'):
        with store.account(account):
            enabled = store.setting('backfill_enabled', '0') == '1'
            status = store.setting('backfill_status', 'Not started')
            statuses.append(status)
            if enabled:
                active.append(account)
            elif not status.startswith('Complete'):
                can_start = True
    return bool(active), ' · '.join(dict.fromkeys(statuses)), can_start


def address(header):
    found = getaddresses([header if isinstance(header, str) else ''])
    if len(found) != 1 or not senders.valid_sender(found[0][1]):
        return ''
    return found[0][1].lower()


def mailbox():
    value = mail.get('/gmail/profile', ttl=0).get('mailbox', '')
    if not senders.valid_sender(value) or not store.mailbox_matches(value):
        raise ValueError('The Gmail connection does not match this catalog’s mailbox.')
    return value.lower()


def sent_context(value):
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(ZoneInfo('America/Los_Angeles')).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def similarities(items):
    # Exact normalized titles are only a hint; we never automatically merge or overwrite.
    result = {}
    for item in items:
        item_key = item.get('view_key', str(item['id']))
        matches = [other for other in items if other.get('view_key', str(other['id'])) != item_key and other['kind'] == item['kind']
                   and extract.normalized(other['title']).casefold() == extract.normalized(item['title']).casefold()]
        if matches:
            result[item_key] = matches
    return result


@router.get('', response_class=HTMLResponse)
def home(request: Request, dismissed: int = 0, q: str = ''):
    if q.strip():
        params={'q':q.strip()[:200]}
        for key in ('past','dismissed'):
            if key in request.query_params:params[key]=request.query_params[key]
        return RedirectResponse('/preschool/search?'+urlencode(params),status_code=303)
    if request.query_params.get('past')=='1' or dismissed:
        return RedirectResponse('/preschool/history'+('?dismissed=1' if dismissed else ''),status_code=303)
    return RedirectResponse('/preschool/', status_code=303)


def class_context(include_archived=False):
    items=[]; modes=[]
    for account in ('personal','shared'):
        with store.account(account):
            mailbox_name=store.setting('mailbox','')
            modes.append(store.setting('auto_enabled','0')=='1')
            for item in store.all_items(include_dismissed=True):
                item['view_key']=_view_key('item',account,item['id'])
                item['message_key']=_view_key('message',account,item['message_id'])
                item['gmail_url']=_gmail_url(account,mailbox_name,item['message_id'])
                items.append(item)
    with store.account('personal'):
        calendar=store.school_events()
        school_sync=store.setting('school_sync')
        school_status=store.setting('school_status','Not yet checked')
    articles=site_store.pages()
    timetables=site_store.timetables()
    items+=schedule.public_references(timetables)
    today=datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
    data=website.project(items,calendar,articles,today,timetables)
    published_sources=[source for row in data['current'] for source in row['sources']]
    data.update(all_sources=items,articles=articles,additions_paused=not all(modes),
                school_source=school_calendar.source(),school_sync=school_sync,school_status=school_status,
                site_updated=max([float(row.get('saved_at') or 0) for row in published_sources]+
                                 [float(row['updated']) for row in articles.values()]+[0]))
    return data


def class_page(request,name,*,data=None,status_code=200,**context):
    return render(request,'preschool/site/'+name+'.html',active='preschool',status_code=status_code,
                  site=data if data is not None else class_context(),**context)


@router.get('/guide',response_class=HTMLResponse)
def class_guide(request: Request):
    return class_page(request,'guide',section='guide')


@router.get('/guide/{slug}',response_class=HTMLResponse)
def class_topic(request: Request,slug: str):
    data=class_context()
    topic=next((topic for topic in data['topics'] if topic['slug']==slug),None)
    if not topic:return RedirectResponse('/preschool/guide',status_code=303)
    return class_page(request,'topic',data=data,topic=topic,section='guide')


@router.get('/responsibilities',response_class=HTMLResponse)
def class_responsibilities(request: Request):
    return class_page(request,'responsibilities',section='responsibilities')


@router.get('/dates',response_class=HTMLResponse)
def class_dates(request: Request):
    return class_page(request,'dates',section='dates')


@router.get('/resources',response_class=HTMLResponse)
def class_resources(request: Request):
    return class_page(request,'resources',section='resources')


@router.get('/history',response_class=HTMLResponse)
def class_history(request: Request,dismissed: int=0):
    data=class_context(bool(dismissed))
    historical=list(data['past'])
    historical += [row for row in data['facts'] if row not in historical and row['topic'] in data['articles']]
    if dismissed:
        historical+=website.facts([item for item in data['all_sources'] if item.get('dismissed') and not item.get('reference_only')])
    return class_page(request,'history',data=data,section='history',historical=historical,
                      revisions=site_store.history(),showing_dismissed=bool(dismissed))


@router.get('/history/{slug}/{revision}',response_class=HTMLResponse)
def class_revision(request: Request,slug: str,revision: int):
    if slug not in website.TOPICS:return RedirectResponse('/preschool/history',status_code=303)
    snapshot=next((row for row in site_store.history(slug) if row['revision']==revision),None)
    if not snapshot:return RedirectResponse('/preschool/history',status_code=303)
    data=class_context(True)
    sources=[item for item in data['all_sources'] if item['view_key'] in snapshot['source_keys']]
    return class_page(request,'revision',data=data,section='history',snapshot=snapshot,sources=sources)


@router.get('/search',response_class=HTMLResponse)
def class_search(request: Request,q: str='',past: int=1,dismissed: int=0):
    q=q.strip()[:200]; needle=q.casefold(); data=class_context(bool(dismissed)); results=[]
    if q:
        for topic in data['topics']:
            article=topic['article']
            if article and needle in (article['title']+' '+article['body']).casefold():
                results.append({'title':topic['title'],'href':topic['href'],'details':article['body'][:220],'label':'Class guide'})
        search_facts=data['facts']+(website.facts([item for item in data['all_sources'] if item.get('dismissed')]) if dismissed else [])
        for fact in search_facts:
            if not past and (fact in data['past'] or fact['topic'] in data['articles']):continue
            if needle not in ' '.join(str(fact.get(key) or '') for key in ('title','details','date','time','location')).casefold():continue
            historical=fact in data['past'] or bool(fact.get('dismissed')) or bool(data['articles'].get(fact['topic']))
            results.append({'title':fact['title'],'href':'/preschool/history#fact-'+fact['key'] if historical else fact['href'],
                            'details':fact['details'],'label':'Previous source information' if historical else 'Class information'})
        for event in data['school_calendar']+(data['school_past'] if past else []):
            if needle in ' '.join(str(event.get(key) or '') for key in ('event','date_text','date_iso','location')).casefold():
                results.append({'title':event['event'],'href':'/preschool/history#school-history' if event in data['school_past'] else '/preschool/dates#school-calendar','details':event['date_display'],'label':'School-wide calendar'})
    return class_page(request,'search',data=data,section='search',q=q,results=results,include_past=bool(past),showing_dismissed=bool(dismissed))


@router.get('/manage/topics/{slug}',response_class=HTMLResponse)
def edit_class_topic(request: Request,slug: str):
    if slug not in website.TOPICS:return RedirectResponse('/preschool/manage',status_code=303)
    data=class_context(); topic=next(topic for topic in data['topics'] if topic['slug']==slug)
    article=topic['article']
    body=article['body'] if article else '\n\n'.join(row['title']+('\n'+row['details'] if row['details'] else '') for row in topic['facts'])
    return class_page(request,'edit',data=data,section='manage',topic=topic,body=body,
                      revision=article['revision'] if article else 0,source_version=topic_source_version(topic),error=None)


def topic_source_version(topic):
    keys=[fact['key'] for fact in topic['supporting_facts']]
    if topic['article']:keys+=topic['article']['source_keys']
    return hashlib.sha256('\0'.join(sorted(keys)).encode()).hexdigest()


@router.post('/manage/topics/{slug}',response_class=HTMLResponse)
def save_class_topic(request: Request,slug: str,body: str=Form(''),revision: int=Form(0),source_version: str=Form('')):
    if slug not in website.TOPICS:return RedirectResponse('/preschool/manage',status_code=303)
    data=class_context(); topic=next(topic for topic in data['topics'] if topic['slug']==slug)
    keys={source['view_key'] for source in topic['sources']}
    if topic['article']:keys.update(topic['article']['source_keys'])
    keys.update(source['view_key'] for fact in topic['supporting_facts'] for source in fact['sources'])
    try:
        if source_version and source_version!=topic_source_version(topic):
            raise site_store.RevisionConflict('Supporting class information changed while you were editing. Reopen the editor before saving.')
        site_store.save(slug,topic['title'],body,keys,revision)
    except ValueError as error:
        return class_page(request,'edit',data=data,status_code=409 if isinstance(error,site_store.RevisionConflict) else 400,
                          section='manage',topic=topic,body=body,revision=revision,source_version=source_version,error=str(error))
    return RedirectResponse(topic['href'],status_code=303)


@router.get('/manage',response_class=HTMLResponse)
def maintenance(request: Request, dismissed: int = 0, q: str = ''):
    items, candidates, needs_review, filtered, messages, senders_all, counts, auto_enabled, statuses, scan_last, backfills, scan_last_success = _all_mailbox_data(bool(dismissed))
    matches = similarities(items)
    q = q.strip()[:200]
    today = datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
    items.sort(key=lambda item: (0 if item['date'] and item['date'] >= today else 1 if not item['date'] else 2,
                                  item['date'] or '', -item['id']))
    has_prior_items = any(item.get('criteria_version') != criteria.VERSION for item in items)
    def matches_search(record, fields):
        return not q or q.casefold() in ' '.join(str(record.get(key) or '') for key in fields).casefold()
    # A handbook search includes history unless explicitly restricted to current entries.
    show_past = request.query_params.get('past', '1' if q else '0') == '1'
    all_items = items
    item_search_fields=('title','details','date','time','location','uncertainty','source_quote','subject','sender','url')
    past_items = [item for item in items if item.get('date') and item['date'] < today
                  and matches_search(item,item_search_fields)]
    past_todo_count = sum(item['kind'] == 'action' for item in past_items)
    if not show_past:
        items = [item for item in items if not item.get('date') or item['date'] >= today]
    if q:
        items = [item for item in items if matches_search(item,item_search_fields)]
    with store.account('personal'):
        events = store.school_events()
        school_status, school_sync = store.setting('school_status', 'Waiting for first check'), store.setting('school_sync')
    calendar_search_fields=('event','date_text','date_iso','start_time','end_time','location')
    past_calendar_count = sum(bool(event.get('date_iso') and event['date_iso'] < today
                                   and matches_search(event,calendar_search_fields)) for event in events)
    calendar_items = events if show_past else [event for event in events
                                               if not event.get('date_iso') or event['date_iso'] >= today]
    if q:
        calendar_items=[event for event in calendar_items if matches_search(event,calendar_search_fields)]
    upcoming_calendar_items=sorted((event for event in calendar_items
                                    if event.get('date_iso') and event['date_iso'] >= today),
                                   key=lambda event:(event['date_iso'],event.get('start_time') or '',event.get('event') or ''))
    calendar_preview=upcoming_calendar_items[:3]
    calendar_rest=[event for event in calendar_items if event not in calendar_preview]
    upcoming_map={}
    def display_date(value):
        try:
            parsed=date.fromisoformat(value)
            return parsed.strftime('%b ') + str(parsed.day) + parsed.strftime(', %Y')
        except (TypeError,ValueError):
            return value
    def add_upcoming(title, date_value, time_value, kind, target, source_url, source_label):
        key=(' '.join(str(title or '').split()).casefold(),date_value)
        row=upcoming_map.setdefault(key,{'title':title,'date':date_value,'time':time_value or '',
                                         'date_display':display_date(date_value),
                                         'kind':kind,'target':target,'sources':[]})
        if source_url and not any(source['url']==source_url for source in row['sources']):
            row['sources'].append({'url':source_url,'label':source_label})
    for item in items:
        if item.get('kind') in ('action','event') and item.get('date') and item['date'] >= today:
            add_upcoming(item['title'],item['date'],item.get('time'),
                         'To-do' if item['kind']=='action' else 'Class event',
                         '#entry-'+item['view_key'],item['gmail_url'],'Email source')
    for event in upcoming_calendar_items:
        add_upcoming(event['event'],event['date_iso'],event.get('start_time'),'Published calendar',
                     '#dates',school_calendar.source(),'Calendar source')
    upcoming_items=sorted(upcoming_map.values(),key=lambda row:(row['date'],row['time'],row['title']))[:5]
    messages.sort(key=lambda message:float(message.get('processed') or 0),reverse=True)
    messages_with_entries=[message for message in messages if message.get('item_count',0)>0]
    if q:
        matched_message_keys={item['message_key'] for item in items}
        recent_messages=[message for message in messages_with_entries if message['view_key'] in matched_message_keys or
                         q.casefold() in ' '.join(str(message.get(k) or '') for k in ('subject','sender')).casefold()]
    else:
        recent_messages=messages_with_entries
    recent_messages=recent_messages[:5]
    for message in recent_messages:
        entries=[item for item in all_items if item['message_key']==message['view_key']]
        if entries:
            entry=next((item for item in entries if item in items),entries[0])
            message['entry_url']=('/preschool?past=1' if entry not in items else '')+'#entry-'+entry['view_key']
    def view_url(*, past=show_past, archived=bool(dismissed), search=q, anchor='history'):
        params={}
        if search:params['q']=search
        if past or search:params['past']='1' if past else '0'
        if archived:params['dismissed']='1'
        return '/preschool'+('?' + urlencode(params) if params else '')+'#'+anchor
    with store.account('shared'):
        group = store.setting('recipient_group', '')
    backfill_enabled, backfill_status, backfill_can_start = _merge_backfill()
    backfill_can_start = backfill_can_start and auto_enabled
    candidate_counts = {key: sum(values.get(key, 0) for values in counts.values()) for key in
                        ('new', 'ready', 'processing', 'processed', 'filtered', 'needs_review')}
    scan_status = 'Up to date' if statuses and all(x == 'Up to date' for x in statuses) else next((x for x in statuses if x != 'Up to date'), 'Waiting for first check')
    backfill_status = ' · '.join(x for x in backfills if x) if backfills else backfill_status
    return page(request, 'manage', items=items, labels=LABELS, matches=matches, q=q,
                senders=senders_all, count=len(messages), messages=messages,
                showing_dismissed=bool(dismissed), candidates=candidates,
                needs_review=needs_review, school_events=events,
                filtered=filtered,
                school_source=school_calendar.source(), today=today, scan_status=scan_status,
                 scan_last=scan_last, scan_last_success=scan_last_success, school_status=school_status,
                school_sync=school_sync, group=group, calendar_preview=calendar_preview,
                calendar_rest=calendar_rest, upcoming_items=upcoming_items,
                recent_messages=recent_messages,
                backfill_status=backfill_status, backfill_enabled=backfill_enabled,
                backfill_can_start=backfill_can_start,
                 candidate_counts=candidate_counts, auto_enabled=auto_enabled,
                 criteria_version=criteria.VERSION, review_reasons=REVIEW_REASON_TEXT,
                 has_prior_items=has_prior_items, show_past=show_past,
                 past_item_count=len(past_items), past_todo_count=past_todo_count,
                  past_calendar_count=past_calendar_count,
                  past_entry_count=len(past_items)+past_calendar_count,
                  past_url=view_url(past=True), current_url=view_url(past=False),
                  archive_url=view_url(archived=not bool(dismissed)),
                    clear_search_url=view_url(search='',past=False,anchor='saved'),class_site=class_context())


def publication_review(request, error=None, status_code=200):
    proposal = publication.candidate(class_context(), site_store.timetables())
    current = publication.latest()
    return class_page(request, 'publication', data={}, section='manage', proposal=proposal,
                      fingerprint=publication.fingerprint(proposal), current=current,
                      error=error, status_code=status_code)


@router.get('/manage/publication', response_class=HTMLResponse)
def review_family_publication(request: Request):
    return publication_review(request)


@router.post('/manage/publication', response_class=HTMLResponse)
def prepare_family_preview(request: Request, fingerprint: str=Form(''), revision: int=Form(0), reviewed: str=Form('')):
    proposal = publication.candidate(class_context(), site_store.timetables())
    try:
        if fingerprint != publication.fingerprint(proposal):
            raise site_store.RevisionConflict('Class information changed. Review the new proposal before preparing a preview.')
        publication.save_preview(proposal, revision, reviewed=reviewed == 'yes')
    except ValueError as error:
        return publication_review(request, str(error), 409 if isinstance(error, site_store.RevisionConflict) else 400)
    return RedirectResponse('/preschool/', status_code=303)


@router.get('/', response_class=HTMLResponse)
@router.get('/family-preview/', response_class=HTMLResponse)
def family_preview():
    current = publication.latest()
    if not current:
        return HTMLResponse('No class guide edition is ready yet. Open Manage to prepare one.', status_code=404)
    return HTMLResponse(publication.family_html(current['payload']), headers={'X-Robots-Tag': 'noindex, nofollow'})


@router.get('/style.css')
@router.get('/family-preview/style.css')
def family_preview_style():
    return Response(publication.stylesheet(), media_type='text/css')


@router.get('/family-preview/snapshot.json')
def family_preview_snapshot():
    current = publication.latest()
    if not current:
        return JSONResponse({'error': 'No class guide edition is ready yet. Open Manage to prepare one.'}, status_code=404)
    return JSONResponse(current['payload'], headers={'X-Robots-Tag': 'noindex, nofollow'})


@router.get('/calendar.ics')
@router.get('/family-preview/calendar.ics')
def family_preview_calendar():
    current = publication.latest()
    if not current:
        return Response('No class guide edition is ready yet. Open Manage to prepare one.', status_code=404, media_type='text/plain')
    return Response(publication.calendar_feed(current['payload']), media_type='text/calendar',
                    headers={'Content-Disposition': f'inline; filename="{school.calendar_filename()}"',
                             'X-Robots-Tag': 'noindex, nofollow'})


def _settings_for_both(key):
    values = []
    for account in ('personal', 'shared'):
        with store.account(account):
            values.append(store.setting(key, '0') == '1')
    return values


@router.post('/scan')
def scan():
    def job():
        for name in ('personal', 'shared'):
            with store.account(name):
                scanner.run(force=True)
    threading.Thread(target=job, daemon=True).start()
    return go('/preschool#inbox')


@router.post('/test-luna')
def test_luna():
    """Bounded live extraction check with invented text only."""
    try:
        found = extract.extract(f'{school.short_name()} work party on May 5, 2027 at 10:00 AM. Please bring gloves.',
                                '2027-04-25T09:00:00-07:00')
        return JSONResponse({'ok': True, 'items': len(found)})
    except (luna.LunaError, extract.ExtractionError, ValueError):
        return JSONResponse({'ok': False})


@router.post('/auto')
def auto_mode(enabled: str = Form('0')):
    for account in ('personal', 'shared'):
        with store.account(account):
            store.set_setting('auto_enabled', '1' if enabled == '1' else '0')
            if enabled == '1':
                store.set_setting('auto_started', time.time())
    return go('/preschool#inbox')


@router.get('/candidate/{mid}', response_class=HTMLResponse)
def candidate_page(request: Request, mid: str):
    candidate = store.candidate(mid) if ID_RE.fullmatch(mid) else None
    if not candidate:
        return go()
    account = store.current_account()
    candidate['view_key'] = _view_key('candidate', account, mid)
    candidate['gmail_url'] = _gmail_url(account, store.setting('mailbox', ''), mid)
    return page(request, 'candidate', candidate=candidate)


@router.get('/review/{key}', response_class=HTMLResponse)
def unified_candidate_page(request: Request, key: str):
    found = _resolve_candidate(key)
    if not found:
        return go()
    account, candidate = found
    with store.account(account):
        candidate['view_key'] = key
        candidate['gmail_url'] = _gmail_url(account, store.setting('mailbox', ''), candidate['id'])
        return page(request, 'candidate', candidate=candidate)


@router.get('/record/{key}/edit', response_class=HTMLResponse)
def unified_edit(request: Request, key: str):
    found = _resolve_item(key)
    if not found:
        return go('/preschool#saved')
    account, item = found
    with store.account(account):
        return page(request, 'edit', item=item, labels=LABELS, error=None)


@router.post('/record/{key}/edit', response_class=HTMLResponse)
def unified_save_edit(request: Request, key: str, kind: str = Form(...), title: str = Form(...),
                      details: str = Form(''), date_value: str = Form(''), time: str = Form(''),
                      location: str = Form(''), uncertainty: str = Form('')):
    found = _resolve_item(key)
    if not found:
        return go('/preschool#saved')
    account, item = found
    with store.account(account):
        return save_edit(request, item['id'], kind, title, details, date_value, time, location, uncertainty)


@router.post('/record/{key}/dismiss')
def unified_dismiss(key: str, dismissed: int = Form(1)):
    found = _resolve_item(key)
    if found:
        account, item = found
        with store.account(account):
            store.dismiss(item['id'], dismissed == 1)
    return RedirectResponse('/preschool?dismissed=1#saved' if dismissed != 1 else '/preschool#saved', status_code=303)


@router.post('/sender')
def add_sender(sender: str = Form(...)):
    sender = sender.strip().lower()
    if not senders.valid_sender(sender):
        return go('/preschool?error=sender')
    store.add_sender(sender)
    return go()


@router.post('/sender/remove')
def remove_sender(sender: str = Form(...)):
    store.remove_sender(sender.strip().lower())
    return go()


@router.get('/discover', response_class=HTMLResponse)
def discover(request: Request):
    candidates = {}
    error = None
    try:
        mailbox()
        name = school.configured('name')
        if not name:
            raise ValueError('The school name is not configured.')
        found = mail.get('/gmail/search', ttl=0, query='"' + name.replace('"', '') + '"', limit=25)
        for msg in found.get('messages', []):
            sender = address(msg.get('from', ''))
            if sender:
                entry = candidates.setdefault(sender, {'address': sender, 'count': 0, 'subjects': []})
                entry['count'] += 1
                if len(entry['subjects']) < 3:
                    entry['subjects'].append(str(msg.get('subject') or '(no subject)')[:200])
    except (ValueError, bridge.BridgeError):
        error = 'Could not search this mailbox, or it does not match the catalog.'
    return page(request, 'discover', candidates=sorted(candidates.values(), key=lambda x: (-x['count'], x['address'])),
                approved=set(store.senders()), error=error)


@router.get('/preview', response_class=HTMLResponse)
def preview(request: Request, sender: str = '', page_token: str = ''):
    sender = sender.strip().lower()
    error, messages, next_page = None, [], None
    if sender not in store.senders():
        error = 'Choose an approved sender first.'
    elif page_token and not PAGE_RE.fullmatch(page_token):
        error = 'That search page is invalid. Start again.'
    else:
        try:
            mailbox()
            found = mail.get('/gmail/search', ttl=0, query='from:' + sender, limit=25, page_token=page_token)
            for msg in found.get('messages', []):
                if ID_RE.fullmatch(str(msg.get('id', ''))) and address(msg.get('from', '')) == sender:
                    messages.append({**msg, 'processed': store.processed(msg['id'])})
            next_page = found.get('next_page_token')
            if next_page and not PAGE_RE.fullmatch(next_page):
                next_page = None
        except (ValueError, bridge.BridgeError):
            error = 'Could not search this mailbox, or it does not match the catalog.'
    return page(request, 'preview', sender=sender, messages=messages, next_page=next_page,
                next_url=('/preschool/shared/preview?' if store.current_account() == 'shared' else '/preschool/preview?') + urlencode({'sender': sender, 'page_token': next_page or ''}),
                error=error)


@router.get('/item/{item_id}/edit', response_class=HTMLResponse)
def edit(request: Request, item_id: int):
    item = store.get_item(item_id)
    if not item:
        return go()
    return page(request, 'edit', item=item, labels=LABELS, error=None)


@router.post('/item/{item_id}/edit', response_class=HTMLResponse)
def save_edit(request: Request, item_id: int, kind: str = Form(...), title: str = Form(...),
              details: str = Form(''), date_value: str = Form(''), time: str = Form(''),
              location: str = Form(''), uncertainty: str = Form('')):
    existing = store.get_item(item_id)
    if not existing:
        return go()
    values = {'kind': kind, 'title': title.strip(), 'details': details.strip(), 'date': date_value or None,
              'time': time.strip() or None, 'location': location.strip() or None, 'uncertainty': uncertainty.strip()}
    error = None
    if kind not in LABELS or not values['title'] or any(len(values[k] or '') > limit for k, limit in
            (('title', 200), ('details', 1200), ('time', 100), ('location', 300), ('uncertainty', 500))):
        error = 'Check the category, title and field lengths.'
    elif date_value:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_value):
                raise ValueError()
            date.fromisoformat(date_value)
        except ValueError:
            error = 'Enter a valid date.'
    if error:
        return page(request, 'edit', item={**existing, **values}, labels=LABELS, error=error)
    store.update_item(item_id, values)
    return go('/preschool#saved')


@router.post('/item/{item_id}/dismiss')
def dismiss(item_id: int, dismissed: int = Form(1)):
    store.dismiss(item_id, dismissed == 1)
    return RedirectResponse('/preschool?dismissed=1#saved' if dismissed != 1 else '/preschool#saved', status_code=303)


@router.post('/shared/group')
def save_shared_group(group: str = Form('')):
    group = group.strip()
    if group and not re.fullmatch(r"[A-Za-z0-9._%+'@-]{2,320}", group):
        return RedirectResponse('/preschool/shared?error=group', status_code=303)
    with store.account('shared'):
        store.set_setting('recipient_group', group)
        for key in ('scan_query', 'scan_page', 'scan_complete'):
            store.set_setting(key, '')
        store.set_setting('scan_next', '0')
    return RedirectResponse('/preschool/shared', status_code=303)


@router.post('/group')
def save_class_group(group: str = Form('')):
    group = group.strip()
    if group and not re.fullmatch(r"[A-Za-z0-9._%+'@-]{2,320}", group):
        return RedirectResponse('/preschool/manage?error=group', status_code=303)
    with store.account('shared'):
        store.set_setting('recipient_group', group)
        for account in ('personal', 'shared'):
            with store.account(account):
                for key in ('scan_query', 'scan_page', 'scan_complete'):
                    store.set_setting(key, '')
                store.set_setting('scan_next', '0')
    return RedirectResponse('/preschool/manage#inbox', status_code=303)


@router.post('/shared/backfill')
def start_shared_backfill():
    with store.account('shared'):
        return _start_shared_backfill()


def _start_shared_backfill():
    if not all(_settings_for_both('auto_enabled')):
        store.set_setting('backfill_enabled','0')
        store.set_setting('backfill_status','Paused while automatic AI is off')
        return RedirectResponse('/preschool/shared',status_code=303)
    try:
        mailbox()
        now = time.time()
        cutoff = now - 90 * 86400
        group = store.setting('recipient_group', '')
        with store.account('shared'):
            store.set_setting('scan_query', scanner.search_query(group, f'after:{int(cutoff)}'))
            store.set_setting('scan_page', '')
            store.set_setting('scan_started', now)
            store.set_setting('scan_next', '0')
            store.set_setting('backfill_from', cutoff)
            store.set_setting('backfill_enabled', '1')
            store.set_setting('backfill_status', 'Searching the past 90 days')
    except (ValueError, bridge.BridgeError):
        with store.account('shared'):
            store.set_setting('backfill_status', 'Could not start; shared mailbox verification failed')
        return RedirectResponse('/preschool/shared', status_code=303)
    def run():
        with store.account('shared'):
            scanner.run(force=True)
    threading.Thread(target=run, name='preschool-backfill', daemon=True).start()
    return RedirectResponse('/preschool/shared', status_code=303)


@router.post('/backfill')
def start_backfill():
    if not all(_settings_for_both('auto_enabled')):
        for account in ('personal','shared'):
            with store.account(account):
                store.set_setting('backfill_enabled','0')
                store.set_setting('backfill_status','Paused while automatic AI is off')
    return RedirectResponse('/preschool/manage',status_code=303)
    now = time.time()
    cutoff = now - 90 * 86400
    started = []
    for account in ('personal', 'shared'):
        with store.account(account):
            try:
                mailbox()
            except (ValueError, bridge.BridgeError):
                store.set_setting('backfill_status', 'Mailbox verification failed; not started')
                continue
            prior_status = store.setting('backfill_status', 'Not started')
            if store.setting('backfill_enabled', '0') == '1' or prior_status.startswith('Complete'):
                continue
            if account == 'shared':
                group = store.setting('recipient_group', '')
            else:
                with store.account('shared'):
                    group = store.setting('recipient_group', '')
            store.set_setting('scan_query', scanner.search_query(group, f'after:{int(cutoff)}'))
            store.set_setting('scan_page', '')
            store.set_setting('scan_started', now)
            store.set_setting('scan_next', '0')
            store.set_setting('backfill_from', cutoff)
            store.set_setting('backfill_enabled', '1')
            store.set_setting('backfill_status', 'Searching the past 90 days')
            started.append(account)
    if started:
        def run():
            for account in started:
                with store.account(account):
                    scanner.run(force=True)
        threading.Thread(target=run, name='preschool-backfill', daemon=True).start()
    return RedirectResponse('/preschool?backfill=started#inbox', status_code=303)


def shared_route(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with store.account('shared'):
            return fn(*args, **kwargs)
    return wrapper


def shared_home_redirect(request: Request):
    return RedirectResponse('/preschool', status_code=303)


@router.get('/criteria', response_class=HTMLResponse)
def criteria_page(request: Request):
    return page(request, 'criteria', criteria=criteria, version=criteria.VERSION,
                automatic_enabled=all(_settings_for_both('auto_enabled')))


for path, fn, methods in (
    ('/shared', shared_home_redirect, ['GET']), ('/shared/scan', scan, ['POST']), ('/shared/auto', auto_mode, ['POST']),
    ('/shared/candidate/{mid}', candidate_page, ['GET']), ('/shared/sender', add_sender, ['POST']),
    ('/shared/sender/remove', remove_sender, ['POST']), ('/shared/discover', discover, ['GET']),
    ('/shared/preview', preview, ['GET']),
    ('/shared/item/{item_id}/edit', edit, ['GET']), ('/shared/item/{item_id}/edit', save_edit, ['POST']),
    ('/shared/item/{item_id}/dismiss', dismiss, ['POST']),
):
    router.add_api_route(path, shared_route(fn), methods=methods)


TOOL = {'slug': 'preschool', 'name': 'Preschool',
         'description': 'Class news, dates, parent responsibilities and resources', 'router': router}
