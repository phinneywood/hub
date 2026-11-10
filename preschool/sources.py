"""Bounded read-only extraction of email attachments and allowlisted public links."""
import csv
import io
import ipaddress
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .support import bridge
from . import ical, school

# The school and neighborhood hosts come from the owner's private settings
# (preschool.school.source_hosts). Without valid configured hosts nothing is fetched,
# not even Google Docs/Sheets/Drive: see allow_hosts().
GOOGLE_HOSTS = school.GOOGLE_HOSTS
GOOGLE_REDIRECT_SUFFIX = '.googleusercontent.com'
MAX_LINKS = 5
MAX_BYTES = 1_000_000
MAX_SOURCE_TEXT = 12_000
MAX_ATTACHMENT_TEXT = 20_000
SKIP_WORDS = re.compile(r'unsubscribe|manage preferences|view (?:this )?email|facebook|instagram|linkedin|twitter', re.I)
GENERIC_LINK_LABELS = re.compile(r'^(?:click here|here|read more|learn more|more info|this link|open|view)$',re.I)
TRACKING_KEYS = {'gclid', 'fbclid', 'mc_cid', 'mc_eid'}
PUBLIC_QUERY_KEYS = {'tribe_event_display','pagename','shortcode','eventdisplay','ical','post_type','page_id'}


def allow_hosts():
    """Hosts that may be read. Fails closed: no valid school hosts, no fetching at all."""
    hosts = school.source_hosts()
    return hosts | GOOGLE_HOSTS if hosts else frozenset()


def resource_words():
    short = school.configured('short_name').lower()
    return re.compile(r'calendar|photo|album|registration|register|form|parent|class|preschool|' +
                      (re.escape(short) + '|' if short else '') +
                      ''.join(re.escape(word) + '|' for word in school.resource_words()) +
                      r'sign.?up|schedule|program|resource', re.I)


def __getattr__(name):
    live = {'ALLOW_HOSTS': allow_hosts, 'UPGRADE_HTTP_HOSTS': school.source_hosts, 'RESOURCE_WORDS': resource_words}
    if name in live:
        return live[name]()
    raise AttributeError(name)


@dataclass
class Evidence:
    attachments: list
    links: list
    review: list

    @property
    def text(self):
        parts=[]
        for i, item in enumerate(self.attachments,1):
            if item.get('text'):
                parts.append(f"Attachment {i} ({item['kind']}):\n{item['text']}")
        for i, item in enumerate(self.links,1):
            if item.get('text'):
                parts.append(f"Public linked source {i} ({item['label'][:200]}):\n{item['text']}")
        return '\n\n'.join(parts)


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','head','svg','iframe','object','form','nav','footer','aside'}: self.skip += 1
        if tag in {'p','br','div','li','tr','h1','h2','h3'} and not self.skip: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script','style','head','svg','iframe','object','form','nav','footer','aside'} and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip:self.parts.append(data)


def normalize_url(raw):
    if not isinstance(raw,str) or not 1<=len(raw)<=2000 or any(ord(c)<32 or c.isspace() for c in raw):
        return None,'invalid_link'
    try:p=urlsplit(raw.strip())
    except ValueError:return None,'invalid_link'
    host=(p.hostname or '').lower().rstrip('.')
    scheme=p.scheme.lower()
    school_hosts=school.source_hosts()
    allowed=school_hosts|GOOGLE_HOSTS if school_hosts else frozenset()
    if p.port not in (None,443) and not (scheme=='http' and p.port==80):return None,'non_public_link'
    if scheme=='http' and host in school_hosts:scheme='https'
    if scheme!='https' or p.username or p.password:return None,'non_public_link'
    try:
        ip=ipaddress.ip_address(host)
        if not ip.is_global:return None,'non_public_link'
    except ValueError:pass
    if host not in allowed:return None,'off_allowlist'
    path=p.path or '/'
    if re.search(r'login|logout|signin|sign-in|unsubscribe|checkout|donate|payment|rsvp|submit|register|signup|sign-up',path,re.I):
        return None,'interactive_link'
    if host in school_hosts:
        pairs=parse_qsl(p.query,keep_blank_values=True)
        if pairs and not all(k.lower().startswith('utm_') or k.lower() in TRACKING_KEYS|PUBLIC_QUERY_KEYS for k,_ in pairs):
            return None,'query_requires_review'
        query=urlencode([(k,v) for k,v in pairs if not k.lower().startswith('utm_') and k.lower() not in TRACKING_KEYS])
        return urlunsplit(('https',host,path,query,'')),'public_page'
    if host=='docs.google.com':
        m=re.fullmatch(r'/document/d/([A-Za-z0-9_-]{10,200})/(?:edit|view|export)/?',path)
        if m:
            if set(dict(parse_qsl(p.query,keep_blank_values=True)))-{'usp'}:return None,'query_requires_review'
            return f'https://docs.google.com/document/d/{m.group(1)}/export?format=txt','google_doc'
        m=re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]{10,200})/(?:edit|view|export)/?',path)
        if m:
            params=dict(parse_qsl(p.query,keep_blank_values=True));gid=params.get('gid','0')
            if set(params)-{'gid','usp'}:return None,'query_requires_review'
            if not re.fullmatch(r'\d{1,12}',gid):return None,'query_requires_review'
            return f'https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}','google_sheet'
        return None,'unsupported_google_link'
    if host=='drive.google.com':
        m=re.fullmatch(r'/file/d/([A-Za-z0-9_-]{10,200})/(?:view|preview)/?',path)
        if m:
            params=dict(parse_qsl(p.query,keep_blank_values=True))
            if set(params)-{'usp'}:return None,'query_requires_review'
            return f'https://drive.google.com/uc?export=download&id={m.group(1)}','google_drive_file'
    return None,'unsupported_link'


def _allowed_redirect(url):
    try:p=urlsplit(url)
    except ValueError:return False
    host=(p.hostname or '').lower().rstrip('.')
    hosts=allow_hosts()
    return p.scheme=='https' and (host in hosts or (bool(hosts) and host.endswith(GOOGLE_REDIRECT_SUFFIX))) and not p.username and not p.password and p.port in (None,443)


def _read_response(client,url,limit=MAX_BYTES):
    for _ in range(4):
        if not _allowed_redirect(url):return None,'off_allowlist'
        try:
            with client.stream('GET',url,headers={'Accept':'text/html,text/plain,text/csv,application/pdf','User-Agent':'Hub-Preschool-Reader/1.0'},follow_redirects=False) as response:
                if response.status_code in (301,302,303,307,308):
                    location=response.headers.get('location','')
                    url=urljoin(url,location)
                    continue
                if response.status_code!=200:return None,'not_public_or_unavailable'
                content_type=response.headers.get('content-type','').split(';',1)[0].lower()
                if content_type not in ('text/html','text/plain','text/csv','application/csv','application/octet-stream','application/pdf','text/calendar','application/ics'):
                    return None,'unsupported_content_type'
                content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>limit:return None,'source_too_large'
                raw=bytes(content)
                if content_type=='application/octet-stream':
                    if raw.startswith(b'%PDF-'):content_type='application/pdf'
                    else:return None,'unsupported_content_type'
                return (raw,content_type),None
        except (httpx.HTTPError,ValueError):return None,'fetch_failed'
    return None,'too_many_redirects'


def _text(content,content_type):
    if content_type in ('text/calendar','application/ics'):
        try:
            parsed=ical.parse(content.decode('utf-8-sig','replace'))
        except (ValueError,UnicodeError):
            return '',True
        value='\n'.join(' | '.join(x for x in (event['title'],event['date'],event['time'],event['location'],event['uncertainty']) if x) for event in parsed)
        return value[:MAX_SOURCE_TEXT],len(value)>MAX_SOURCE_TEXT
    if content_type=='application/pdf':
        try:parsed=bridge.extract_public_document('application/pdf',content)
        except bridge.BridgeError:return '',True
        return ' '.join((parsed.get('text') or '').split())[:MAX_SOURCE_TEXT],bool(parsed.get('truncated')) or len(parsed.get('text') or '')>MAX_SOURCE_TEXT
    try:value=content.decode('utf-8-sig','replace')
    except (AttributeError,UnicodeError):return ''
    if content_type=='text/html':
        parser=VisibleHTML();parser.feed(value);value=''.join(parser.parts)
    elif content_type in ('text/csv','application/csv','application/octet-stream'):
        try:
            rows=list(csv.reader(io.StringIO(value)))[:500]
            value='\n'.join(' | '.join(cell[:500] for cell in row[:20]) for row in rows)
        except csv.Error:return ''
    value=' '.join(value.split())
    return value[:MAX_SOURCE_TEXT],len(value)>MAX_SOURCE_TEXT


def collect(message, load_attachment, client=None, current_text=None):
    """Return bounded source evidence; never fetch a link outside the configured public allowlist."""
    attachments=[]; links=[]; review=[]
    manifests=message.get('attachments') or []
    if not isinstance(manifests,list):
        manifests=[];review.append('unreadable_attachment')
    if len(manifests)>3:review.append('too_many_attachments')
    for attachment in manifests[:3]:
        if not isinstance(attachment,dict):
            review.append('unreadable_attachment');continue
        mime=(attachment.get('mime_type') or '').lower()
        if mime not in {'text/calendar','application/ics','application/pdf','text/plain'}:
            if not (mime.startswith('image/') and attachment.get('inline')):
                review.append('unsupported_attachment')
            continue
        if not attachment.get('readable') or not attachment.get('attachment_id'):
            review.append('unreadable_attachment');continue
        try:
            content=load_attachment(attachment)
            if not isinstance(content,str) or not content.strip():raise ValueError()
            if len(content)>MAX_ATTACHMENT_TEXT:review.append('attachment_too_large');continue
            if mime in {'text/calendar','application/ics'}:
                events=ical.parse(content)
                summary='\n'.join(' | '.join(x for x in (event['title'],event['date'],event['time'],event['location'],event['uncertainty']) if x) for event in events)
                attachments.append({'kind':'calendar invitation','text':summary,'calendar_events':events})
            else:
                attachments.append({'kind':'PDF' if mime=='application/pdf' else 'plain text','text':content,'calendar_events':[]})
        except (ValueError,bridge.BridgeError):
            review.append('attachment_read_failed')
    raw_links=message.get('links') or []
    if message.get('links_truncated'):
        review.append('too_many_resource_links')
    if not isinstance(raw_links,list):
        raw_links=[];review.append('too_many_resource_links')
    current=' '.join((current_text or message.get('text') or '').split()).casefold()
    words=resource_words()
    def relevant_link(link):
        label=' '.join(str(link.get('label') or '').split())
        url=str(link.get('url') or '')
        if words.search(label+' '+url):return True
        if not label or not GENERIC_LINK_LABELS.fullmatch(label):return False
        start=0
        while True:
            index=current.find(label.casefold(),start)
            if index<0:return False
            context=current[max(0,index-160):min(len(current),index+len(label)+160)]
            if words.search(context):return True
            start=index+len(label)
    useful_links=[x for x in raw_links if isinstance(x,dict) and not SKIP_WORDS.search(str(x.get('label') or '')) and
                  relevant_link(x) and
                  (' '.join(str(x.get('label') or '').split()).casefold() in current or str(x.get('url') or '').casefold() in current)]
    if len(useful_links)>MAX_LINKS:review.append('too_many_resource_links')
    own_client=client is None
    client=client or httpx.Client(timeout=15,follow_redirects=False)
    try:
        for link in useful_links[:MAX_LINKS]:
            url,kind=normalize_url(link.get('url'))
            if not url:
                review.append(kind);continue
            result,error=_read_response(client,url)
            if error:
                review.append(error);continue
            content,content_type=result
            text,truncated=_text(content,content_type)
            if not text:
                review.append('empty_link_source');continue
            if truncated:
                review.append('link_source_truncated');continue
            links.append({'label':str(link.get('label') or 'Linked source')[:200], 'url':url,
                          'kind':kind,'text':text})
    finally:
        if own_client:client.close()
    return Evidence(attachments,links,list(dict.fromkeys(review)))
