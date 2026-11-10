"""Owner-invoked session catch-up. Fixed source reads and local writes; no model client.

There is deliberately no web endpoint, scheduler or automatic activation for this.
The interactive assistant reviews minimized evidence and supplies explicit facts.
"""
from datetime import date
from email.utils import getaddresses

from .support import config,store as hub_store
from . import extract,mail,processor,store


def import_facts(account,message_id,expected_sender,drafts):
    if account not in ('personal','shared') or not drafts or len(drafts)>30:
        raise ValueError('Invalid bounded session import.')
    if not (config.BRIDGE_TOKEN_FILE.parent/'organization-paused').is_file():
        raise ValueError('Keep the bridge maintenance pause active during catch-up.')
    with store.account(account):
        if store.setting('auto_enabled','0')!='0' or store.setting('backfill_enabled','0')!='0':
            raise ValueError('Automatic processing must stay paused during session catch-up.')
        profile=mail.get('/gmail/profile',ttl=0)
        mailbox=profile.get('mailbox','').lower()
        if not store.setting('mailbox','') or mailbox!=store.setting('mailbox',''):
            raise ValueError('The session mailbox identity changed.')
        sender=getaddresses([expected_sender])[0][1].lower()
        if hub_store.ai_blocked(sender):raise ValueError('AI use is blocked for this sender.')
        raw=mail.get('/gmail/message',message_id=message_id)
        if raw.get('truncated'):raise ValueError('A truncated email needs direct inspection.')
        prepared=processor.prepare_message(mailbox,message_id,sender,raw)
        texts=[prepared['source_text']]+[source['text'] for source in prepared['supporting']]
        items=[]
        for draft in drafts:
            if set(draft)-{'source_index','item','link_to_source'}:raise ValueError('Unexpected session import fields.')
            index=draft.get('source_index',0)
            if type(index) is not int or not 0<=index<len(texts):raise ValueError('Unknown source evidence.')
            item=dict(draft['item'])
            if set(item)!=set(extract.FIELDS) or item['kind'] not in extract.KINDS:
                raise ValueError('Invalid source fact fields.')
            for field,limit in (('title',200),('details',1200),('uncertainty',500),('source_quote',500)):
                if not isinstance(item[field],str) or len(item[field])>limit:raise ValueError('Invalid source fact text.')
            if not item['title'].strip() or not item['source_quote'].strip() or extract.normalized(item['source_quote']) not in extract.normalized(texts[index]):
                raise ValueError('The session quote does not match its source.')
            if item['date'] is not None:date.fromisoformat(item['date'])
            for field,limit in (('time',100),('location',300),('url',2000)):
                if item[field] is not None and (not isinstance(item[field],str) or len(item[field])>limit):raise ValueError('Invalid source fact field.')
            if draft.get('link_to_source'):
                link_index=index-1-len(prepared['evidence'].attachments)
                if not 0<=link_index<len(prepared['evidence'].links):raise ValueError('A resource needs a fetched public source.')
                item['url']=prepared['evidence'].links[link_index]['url']
            elif item['url'] is not None:
                raise ValueError('Only an inspected public source may supply a direct resource link.')
            items.append(item)
        status='Reviewed in the owner-authorized interactive session; no separate model API calls.'
        partial=bool(prepared['evidence'].review)
        ids=store.save_session_message(mailbox,{
            'id':message_id,'sender':sender,'subject':str(raw.get('subject') or '')[:500],
            'date':str(raw.get('date') or '')[:200],'has_attachments':bool(raw.get('attachments')),
            'attachment_status':status,'sharing_evidence':prepared['sharing_evidence']},items,partial=partial)
        return {'item_ids':ids,'partial_sources':partial}
