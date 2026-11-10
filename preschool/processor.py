"""Bounded, source-linked preschool extraction after the Jev criteria gate."""
import threading
import re
from email.utils import getaddresses, parsedate_to_datetime
from zoneinfo import ZoneInfo

from .support import bridge, senders, store as hub_store
from . import audience, criteria, evidence as evidence_checks, extract, jev, mail, sources, store

LOCK = threading.Lock()
MAX_MODEL_CHUNKS = 8


def address(header):
    parsed = getaddresses([header if isinstance(header, str) else ''])
    return parsed[0][1].lower() if len(parsed) == 1 and senders.valid_sender(parsed[0][1]) else ''


def sent_context(value):
    try:
        d = parsedate_to_datetime(value)
        return d.astimezone(ZoneInfo('America/Los_Angeles')).isoformat() if d.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _read_attachment(mid, attachment):
    mime=(attachment.get('mime_type') or '').lower()
    if mime in ('text/calendar','application/ics'):
        result=mail.get('/gmail/calendar-attachment',message_id=mid,part_id=attachment.get('part_id') or 'root')
    else:
        result=mail.get('/gmail/document-attachment',message_id=mid,part_id=attachment.get('part_id') or 'root')
    if result.get('truncated'):
        raise extract.ExtractionError('An attachment was truncated by the isolated parser.')
    return result.get('text') or ''


def prepare_message(account, mid, sender, message=None):
    """Read and vet all bounded evidence locally before Jev judges relevance."""
    if hub_store.ai_blocked(sender):
        raise ValueError('AI is turned off for this sender.')
    message=message or mail.get('/gmail/message',message_id=mid)
    if message.get('id')!=mid or address(message.get('from',''))!=sender:
        raise ValueError('Message sender did not match.')
    if message.get('truncated'):
        raise extract.ExtractionError('A message was truncated by the Gmail bridge and was not processed.')
    original=message.get('text') or ''
    current=criteria.current_text(original)
    evidence=sources.collect(message,lambda a:_read_attachment(mid,a),current_text=current)
    sent=sent_context(message.get('date',''))
    with store.account('shared'):
        group = store.setting('recipient_group', '')
    context = {'addressed_to_configured_class_group': jev.class_group_match(message.get('recipients'), group)}
    jev_state={'id':mid,'from':message.get('from',''),'subject':message.get('subject',''),
               'date':message.get('date',''),'text':current,'recipients':message.get('recipients',{}),
               'attachments':message.get('attachments',[])}
    supporting=[]
    for attachment in evidence.attachments:
        supporting.append({'kind':attachment['kind'],'label':attachment['kind'],'text':attachment.get('text','')})
    for link in evidence.links:
        host=(link['url'].split('/',3)[2] if '://' in link['url'] else 'public source')
        supporting.append({'kind':'public link','label':f"{link['label']} ({host})",'text':link['text']})
    if any(evidence_checks.CHANGED.search(text) for text in [current, *(source['text'] for source in supporting)]):
        evidence.review.append('changed_source')
    total=len(current)+sum(len(x['text'])+len(x['label']) for x in supporting)
    if total>jev.MAX_TEXT:
        evidence.review.append('combined_source_text_too_large')
    chunks=len(list(extract.chunks(current)))+sum(len(list(extract.chunks(x['text']))) for x in supporting if x['kind']!='calendar invitation')
    if chunks>MAX_MODEL_CHUNKS:
        evidence.review.append('combined_luna_text_too_large')
    return {'account':account,'message':message,'sender':sender,'source_text':current,
            'jev_state':jev_state,'supporting':supporting,'evidence':evidence,'sent':sent,'context':context,
            'sharing_evidence':audience.classify(message)}


def read_message(account, mid, sender, prepared=None):
    """After relevance approval, extract items and commit the source-linked results locally."""
    prepared=prepared or prepare_message(account,mid,sender)
    if hub_store.ai_blocked(sender):
        raise ValueError('AI is turned off for this sender.')
    evidence=prepared['evidence']
    if evidence.review:
        raise extract.ExtractionError('A supporting attachment or link needs review before analysis.')
    message=prepared['message'];sent=prepared['sent'];text=prepared['source_text']
    items=[]
    if text.strip():items.extend(extract.extract(text,sent,context=prepared['context']))
    chunks=MAX_MODEL_CHUNKS-len(list(extract.chunks(text)))
    for attachment in evidence.attachments:
        if attachment['kind']=='calendar invitation':
            items.extend(attachment.get('calendar_events',[]))
            continue
        content=attachment.get('text','')
        needed=len(list(extract.chunks(content)))
        if not content.strip() or needed>chunks:
            raise extract.ExtractionError('Supporting document exceeds the remaining extraction budget.')
        chunks-=needed
        found=extract.extract(content,sent,context=prepared['context'])
        for item in found:
            item['uncertainty']=('From an attachment. '+item['uncertainty'])[:500]
        items.extend(found)
    for linked in evidence.links:
        content=linked.get('text','')
        needed=len(list(extract.chunks(content)))
        if not content.strip() or needed>chunks:
            raise extract.ExtractionError('Linked source exceeds the remaining extraction budget.')
        chunks-=needed
        found=extract.extract(content,sent,context=prepared['context'])
        for item in found:
            item['url']=linked['url']
            item['details']=(item['details']+' From linked source: '+linked['label'])[:1200]
            item['uncertainty']=('From an allowlisted linked source. '+item['uncertainty'])[:500]
        items.extend(found)
    # Links in plain text may be outside the fetched-resource allowlist. Keep them as
    # resources for the human-facing catalog, but never fetch them automatically.
    fetched_urls={entry['url'] for entry in evidence.links}
    for link in prepared['message'].get('links') or []:
        if not isinstance(link,dict) or not isinstance(link.get('url'),str):continue
        raw=link['url']
        try:
            from urllib.parse import urlsplit
            host=(urlsplit(raw).hostname or '').lower()
        except ValueError:
            continue
        if raw in fetched_urls or not raw.lower().startswith(('http://','https://')):continue
        if not sources.resource_words().search(str(link.get('label') or '')+' '+host):
            continue
        items.append({'kind':'resource','title':str(link.get('label') or 'Linked preschool resource')[:200],
                      'details':'A source link is available; its destination was not fetched because it is outside the approved public-source allowlist.',
                      'date':None,'time':None,'location':None,'url':None,
                      'uncertainty':'Linked destination needs review; it was not fetched.',
                      'source_quote':str(link.get('label') or raw)[:500]})
    manifest=message.get('attachments') or []
    status=f'{len(evidence.attachments)} of {len(manifest)} attachments read; {len(evidence.links)} linked sources read' if manifest or message.get('links') else ''
    store.save_message(account,{'id':mid,'sender':sender,'subject':str(message.get('subject') or '')[:500],
        'date':str(message.get('date') or '')[:200],'has_attachments':bool(manifest),
        'attachment_status':status,'sharing_evidence':prepared.get('sharing_evidence')},items)
    return len(items)
