"""Deterministic sharing eligibility, separate from private email relevance.

Headers are delivery evidence, not a guarantee of harmless prose. No sender name,
subject/body keyword, model score, Bcc recipient or quoted/forwarded header can
authorize family publication. Missing/unapproved evidence fails closed.
"""
import hashlib
import json
import re
from email.headerregistry import HeaderRegistry

from .support import senders
from . import store

VERSION = '1'
LIST_ID = re.compile(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)+')
HEADER = HeaderRegistry()


def bindings():
    with store.account('shared'):
        try:
            addresses = json.loads(store.setting('family_group_addresses', '[]'))
            lists = json.loads(store.setting('family_list_ids', '[]'))
        except (ValueError, TypeError):
            return {'addresses': [], 'lists': []}
    if not isinstance(addresses, list) or not isinstance(lists, list) or len(addresses)+len(lists)>20:
        return {'addresses': [], 'lists': []}
    # recipient_group is editable discovery configuration, not sharing consent,
    # even if it happens to contain a complete address.
    if (any(not isinstance(value,str) or not senders.valid_sender(value) for value in addresses) or
            any(not isinstance(value,str) or not LIST_ID.fullmatch(value.casefold()) for value in lists)):
        return {'addresses': [], 'lists': []}
    return {'addresses': sorted({value.casefold() for value in addresses}),
            'lists': sorted({value.casefold() for value in lists})}


def binding_hash(value=None):
    return hashlib.sha256(json.dumps(bindings() if value is None else value,sort_keys=True).encode()).hexdigest()


def classify(message, approved=None):
    approved = bindings() if approved is None else approved
    result = {'scope': 'unverified', 'reason': 'missing_headers', 'policy': VERSION,
              'binding_hash': binding_hash(approved)}
    if (re.match(r'\s*(?:re\s*:\s*)*(?:fw|fwd)\s*:', str(message.get('subject') or ''), re.I) or
            re.search(r'(?im)^\s*[- ]{2,}(?:Forwarded message|Original Message)[- ]{2,}',
                      str(message.get('text') or ''))):
        return {**result, 'reason': 'forwarded_source_requires_review'}
    headers = message.get('recipients')
    if not isinstance(headers,dict):
        return result
    if any(not isinstance(headers.get(key,''),str) or len(headers.get(key,''))>4096 or
           any(ord(char)<32 or ord(char)==127 for char in headers.get(key,'')) for key in ('to','cc','list-id')):
        return {**result, 'reason': 'invalid_headers'}
    # Parse addresses, never search display names or accept a list-name substring.
    addresses=set()
    try:
        for name in ('to','cc'):
            parsed=HEADER(name,headers.get(name,''))
            if parsed.defects or any(not senders.valid_sender(a.addr_spec) for a in parsed.addresses):
                return {**result, 'reason': 'invalid_headers'}
            addresses.update(a.addr_spec.casefold() for a in parsed.addresses)
    except (ValueError,IndexError,TypeError):
        return {**result, 'reason': 'invalid_headers'}
    list_header = headers.get('list-id','').strip().casefold()
    match = re.fullmatch(r'(?:[^<>]{0,200})<([^<>]+)>',list_header)
    list_id = match[1].strip() if match else list_header
    if list_header and not LIST_ID.fullmatch(list_id):
        return {**result, 'reason': 'invalid_headers'}
    if addresses.intersection(approved['addresses']):
        return {**result, 'scope': 'class', 'reason': 'exact_class_recipient'}
    if LIST_ID.fullmatch(list_id) and list_id in approved['lists']:
        return {**result, 'scope': 'class', 'reason': 'exact_approved_list_id'}
    if list_header:
        return {**result, 'reason': 'unapproved_distribution_list'}
    if addresses:
        return {**result, 'scope': 'private', 'reason': 'no_class_audience_evidence'}
    return result


def source_allowed(source, approved_hash=None):
    if source.get('reference_only') is True:
        # Only locally validated, explicitly maintained public timetable references.
        from .publication import public_resource_url
        from .schedule import source_key
        try:
            return (bool(public_resource_url(source.get('url'))) and
                    source.get('view_key') == source_key({'url':source['url'],'quote':source['source_quote']}))
        except (ValueError,KeyError,TypeError):
            return False
    return (source.get('sharing_audience') == 'class' and source.get('audience_policy') == VERSION and
            source.get('audience_binding_hash') == (approved_hash or binding_hash()))
