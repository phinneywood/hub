"""Fifteen-minute deterministic discovery and tightly bounded new-mail processing."""
import re
import threading
import time
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from zoneinfo import ZoneInfo

from .support import bridge, luna, senders, store as hub_store
from . import criteria, extract, jev, mail, processor, quotas, school, school_calendar, store

INTERVAL = 15 * 60
MAX_PAGES = 8
SEARCH_VERSION = '2'
ID_RE = re.compile(r'^[a-fA-F0-9]{8,32}$')
TOKEN_RE = re.compile(r'^[A-Za-z0-9_=-]{1,2048}$')
DAILY_EMAIL_LIMIT = 3
DAILY_JEV_LIMIT = 50
MAX_JEV_PER_SCAN = 3
AUTO_RELEVANT = 0.90
AUTO_USEFUL = 0.75
FILTER_IRRELEVANT = 0.05
LOCK = threading.Lock()
_started = False


def match_pattern():
    """The configured school acronym or name phrase; matches nothing when unconfigured."""
    parts = []
    short = school.configured('short_name')
    if short:
        parts.append(r'\b' + re.escape(short) + r'\b')
    phrase = school.configured('search_name')
    if phrase:
        parts.append(r'\s+'.join(re.escape(word) for word in phrase.lower().split()))
    return re.compile('|'.join(parts) or r'(?!)', re.I)


def __getattr__(name):
    if name == 'MATCH':
        return match_pattern()
    raise AttributeError(name)


def global_daily_count(prefix, day):
    kinds = {'auto_count_': 'extraction', 'jev_count_': 'decision'}
    if prefix not in kinds:
        raise ValueError('Unknown standing quota counter.')
    return quotas.current(kinds[prefix], day)


def set_global_daily_count(prefix, day, value):
    with store.account('shared'):
        store.set_setting('global_' + prefix + day, value)


def discover(now=None):
    now = time.time() if now is None else now
    account = mail.get('/gmail/profile', ttl=0).get('mailbox', '').lower()
    if not senders.valid_sender(account) or not store.mailbox_matches(account):
        raise ValueError('Mailbox does not match the preschool catalog.')
    if store.setting('search_version') != SEARCH_VERSION:
        # New discovery terms need a fresh bounded window; this is not the paused
        # 90-day backfill and does not reset or modify Gmail.
        for key, value in (('scan_query',''),('scan_page',''),('scan_complete','')):
            store.set_setting(key,value)
        store.set_setting('search_version',SEARCH_VERSION)
    query = store.setting('scan_query')
    page = store.setting('scan_page', '')
    if not query:
        last = store.setting('scan_complete')
        try:
            since = max(0, int(float(last) - 2 * 86400)) if last else None
        except ValueError:
            since = None
        group = class_group()
        query = search_query(group, f'after:{since}' if since else 'newer_than:28d')
        store.set_setting('scan_query', query)
        store.set_setting('scan_started', now)
        page = ''
    for _ in range(MAX_PAGES):
        response = mail.get('/gmail/search', ttl=0, query=query, limit=25, page_token=page)
        found = []
        for m in response.get('messages', []):
            mid = str(m.get('id') or '')
            parsed = getaddresses([m.get('from') or ''])
            if not ID_RE.fullmatch(mid) or len(parsed) != 1 or not senders.valid_sender(parsed[0][1]):
                continue
            sender = parsed[0][1].lower()
            subject = str(m.get('subject') or '')[:500]
            snippet = str(m.get('snippet') or '')[:1000]
            found.append({'id': mid, 'sender': sender, 'subject': subject, 'sent': str(m.get('date') or '')[:200],
                          'snippet': snippet, 'classification': 'likely' if match_pattern().search(subject) else 'review'})
        store.add_candidates(account, found)
        page = response.get('next_page_token') or ''
        if not page:
            # Use the beginning of this search, with a two-day overlap on the next pass.
            store.set_setting('scan_complete', store.setting('scan_started', str(now)))
            store.set_setting('scan_query', '')
            store.set_setting('scan_page', '')
            return True
        if not isinstance(page, str) or not TOKEN_RE.fullmatch(page):
            raise ValueError('Search returned an invalid page token.')
        store.set_setting('scan_page', page)
    return False


def class_group():
    if store.current_account() == 'shared':
        return store.setting('recipient_group', '')
    with store.account('shared'):
        return store.setting('recipient_group', '')


def search_query(group, date_filter):
    # Tuition is a narrow discovery hint for possible class bills; Jev still requires
    # clear school/class context before anything can be retained.
    options = [term for term in (school.configured('short_name'), school.configured('search_name')) if term]
    options = [term if re.fullmatch(r'[A-Za-z0-9]+', term) else '"' + term.replace('"', '') + '"' for term in options]
    options.append('tuition')
    if group:
        if not re.fullmatch(r"[A-Za-z0-9._%+'@-]{2,320}", group):
            raise ValueError('Invalid recipient group.')
        options.extend(('to:' + group, 'cc:' + group))
        if '@' in group:
            options.append('list:' + group)
    if not isinstance(date_filter, str) or not re.fullmatch(r'(?:newer_than:\d{1,3}d|after:\d{1,12})', date_filter):
        raise ValueError('Invalid search time window.')
    if date_filter.startswith('newer_than:') and int(date_filter.split(':',1)[1][:-1]) > 90:
        raise ValueError('Search window exceeds 90 days.')
    return '{' + ' '.join(options) + '} ' + date_filter


def auto_process(now):
    """Jev classifies bounded new-mail candidates; Luna sees only high-confidence useful mail."""
    if store.setting('auto_enabled', '0') != '1' or not luna.configured():
        return False
    try:
        cutoff = float(store.setting('auto_started', str(now)))
    except ValueError:
        cutoff = now
    day = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).date().isoformat()
    used = global_daily_count('auto_count_', day)
    jev_used = global_daily_count('jev_count_', day)
    account = mail.get('/gmail/profile', ttl=0).get('mailbox', '').lower()
    if not senders.valid_sender(account) or not store.mailbox_matches(account):
        return False
    try:
        backfill_enabled = store.setting('backfill_enabled', '0') == '1'
        backfill_from = float(store.setting('backfill_from', '0')) if backfill_enabled else 0
    except ValueError:
        backfill_enabled, backfill_from = False, 0
    judged = 0
    pending = store.candidates('ready') + store.candidates('new')
    pending.sort(key=lambda c: (c['sent'], c['discovered']), reverse=True)
    for candidate in pending:
        if candidate['sender'] == account or hub_store.ai_blocked(candidate['sender']):
            continue
        try:
            sent = parsedate_to_datetime(candidate['sent'])
            if not sent.tzinfo:
                continue
            sent_at = sent.timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
        is_backfill = backfill_enabled and sent_at >= backfill_from
        if sent_at < cutoff and not is_backfill:
            continue
        message = mail.get('/gmail/message', message_id=candidate['id'])
        if not processor.LOCK.acquire(blocking=False):
            return False
        claimed = False
        try:
            claimed = store.claim_candidate(candidate['id'])
            if not claimed:
                continue
            if message.get('id') != candidate['id'] or processor.address(message.get('from')) != candidate['sender']:
                store.needs_review(candidate['id'],'validation_failed')
                return False
            if message.get('truncated'):
                store.needs_review(candidate['id'],'source_truncated')
                return False
            prepared = processor.prepare_message(account,candidate['id'],candidate['sender'],message)
            if prepared['evidence'].review:
                reason=prepared['evidence'].review[0]
                if reason in ('attachment_too_large','combined_source_text_too_large','combined_luna_text_too_large','link_source_truncated','source_too_large'):
                    reason='source_too_large'
                elif reason in ('not_public_or_unavailable','fetch_failed','too_many_redirects','unsupported_content_type'):
                    reason='source_unavailable'
                elif reason in ('interactive_link','invalid_link','non_public_link','unsupported_google_link','unsupported_link'):
                    reason='off_allowlist'
                elif reason=='attachment_read_failed':
                    reason='attachment_read_failed'
                store.needs_review(candidate['id'],reason)
                return False
            if not prepared['source_text'].strip() and not prepared['supporting']:
                store.needs_review(candidate['id'],'validation_failed')
                return False
            if candidate['state'] == 'ready':
                if candidate.get('criteria_version') != criteria.VERSION:
                    store.needs_review(candidate['id'], 'jev_uncertain')
                    continue
                scores = {'preschool_relevant': candidate['relevance_score'] or 0,
                          'useful_information': candidate['usefulness_score'] or 0}
                if scores['preschool_relevant'] < AUTO_RELEVANT or scores['useful_information'] < AUTO_USEFUL:
                    store.needs_review(candidate['id'], 'jev_uncertain')
                    continue
            else:
                if jev_used >= DAILY_JEV_LIMIT or judged >= MAX_JEV_PER_SCAN:
                    store.mark_candidate(candidate['id'], 'new')
                    return False
                jev_used = quotas.reserve('decision', day)
                group = class_group()
                scores = jev.decide(prepared['jev_state'], group, supporting_sources=prepared['supporting'])
                judged += 1
                relevant = scores['preschool_relevant']
                useful = scores['useful_information']
                if relevant < FILTER_IRRELEVANT:
                    store.save_decision(candidate['id'], scores, 'filtered')
                    if judged >= MAX_JEV_PER_SCAN:
                        return False
                    continue
                if relevant < AUTO_RELEVANT or useful < AUTO_USEFUL:
                    store.save_decision(candidate['id'], scores, 'needs_review')
                    if judged >= MAX_JEV_PER_SCAN:
                        return False
                    continue
            if used >= DAILY_EMAIL_LIMIT:
                store.save_decision(candidate['id'], scores, 'ready')
                continue
            store.save_decision(candidate['id'], scores, 'processing')
            # Reserve Luna's daily allowance before the first extraction call.
            quotas.reserve('extraction', day)
            processor.read_message(account, candidate['id'], candidate['sender'],prepared=prepared)
            return True
        except jev.JevError:
            if claimed:store.needs_review(candidate['id'],'jev_unavailable')
            return False
        except bridge.BridgeError:
            if claimed:store.needs_review(candidate['id'],'gmail_read_failed')
            return False
        except luna.LunaError:
            if claimed:store.needs_review(candidate['id'],'luna_failed')
            return False
        except extract.ExtractionError:
            if claimed:store.needs_review(candidate['id'],'extraction_invalid')
            return False
        except ValueError:
            if claimed:store.needs_review(candidate['id'],'validation_failed')
            return False
        except Exception:
            if claimed:store.needs_review(candidate['id'],'internal_error')
            return False
        finally:
            processor.LOCK.release()
        if judged >= MAX_JEV_PER_SCAN:
            return False
    return False


def run(now=None, force=False):
    now = time.time() if now is None else now
    if not LOCK.acquire(blocking=False):
        return False
    try:
        if not force:
            try:
                next_due = float(store.setting('scan_next', '0'))
            except ValueError:
                next_due = 0
            if now < next_due:
                return False
        store.set_setting('scan_next', now + INTERVAL)
        try:
            complete = discover(now)
            store.set_setting('scan_status', 'Up to date' if complete else 'More results will be checked next time')
            store.set_setting('scan_last_attempt', now)
        except (bridge.BridgeError, ValueError, TypeError):
            store.set_setting('scan_status', 'Mail check unavailable; will retry')
            store.set_setting('scan_last_attempt', now)
        try:
            auto_process(now)
        except (bridge.BridgeError, ValueError, TypeError):
            pass
        if store.setting('backfill_enabled', '0') == '1' and not store.setting('scan_query') and not store.setting('scan_page'):
            try:
                from email.utils import parsedate_to_datetime
                from datetime import datetime
                cutoff = float(store.setting('backfill_from', '0'))
                remaining = 0
                for candidate in store.candidates('new') + store.candidates('ready'):
                    try:
                        sent = parsedate_to_datetime(candidate['sent'])
                        remaining += bool(sent.tzinfo and sent.timestamp() >= cutoff)
                    except (TypeError, ValueError, OverflowError):
                        remaining += 1
                if remaining == 0:
                    store.set_setting('backfill_enabled', '0')
                    store.set_setting('backfill_status', 'Complete; review uncertain messages below')
                else:
                    store.set_setting('backfill_status', f'{remaining} messages remain in the approved 90-day batch')
            except (TypeError, ValueError):
                store.set_setting('backfill_status', 'Backfill needs review')
        try:
            last = float(store.setting('school_sync', '0'))
        except ValueError:
            last = 0
        if store.current_account() == 'personal' and (force or now - last >= 86400):
            try:
                school_calendar.refresh(now)
                store.set_setting('school_status', 'Up to date')
            except ValueError:
                store.set_setting('school_status', 'School calendar unavailable; showing last successful copy')
        return True
    finally:
        LOCK.release()


def start():
    global _started
    if _started:
        return
    _started = True
    for name in ('personal', 'shared'):
        with store.account(name):
            store.reopen_stale_decisions(criteria.VERSION)
            store.recover_candidates()
            if store.setting('auto_started') is None:
                store.set_setting('auto_started', time.time())
            if store.setting('auto_enabled') is None:
                store.set_setting('auto_enabled', '0')
    def loop():
        while True:
            try:
                for name in ('personal', 'shared'):
                    with store.account(name):
                        run()
            except Exception:
                # No email or provider text goes into logs.
                pass
            threading.Event().wait(60)
    threading.Thread(target=loop, name='preschool-scanner', daemon=True).start()
