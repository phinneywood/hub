"""ChatGPT history, from the export kept in Documents: each conversation's title, when it was had, and what the owner
and ChatGPT said along the branch it ended on; the group chat; and the text of documents attached to them.

The export's own files: conversations-000.json and on (conversations.json in older exports), group_chats.json, and
the attachments as <file id>.dat, named in library_files.json and conversation_asset_file_names.json. Only what was
said is kept, voice conversations included: not ChatGPT's thinking or the summaries of it. Attached PDFs, Word
documents, text and Markdown are read into the conversation they belong to; pictures are found by name.

None of it goes to a model unless the owner switches that on in Hub; this program never sends it anywhere.
"""
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path

from . import macos
from .text import MOST, chunks, clean, words

SAID = ('user', 'assistant')
CONTENT = ('text', 'multimodal_text')
READER = 1                  # raised when reading improves, so every conversation is read again once
READ = {'.pdf': 'pdf', '.docx': 'docx', '.doc': 'doc', '.txt': 'text', '.md': 'text', '.markdown': 'text'}


def root():
    return Path(os.environ.get('HUB_ARCHIVE_CHATGPT') or Path.home() / 'Documents' / 'chatgpt-archive' / 'export')


def _load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def conversations(folder):
    """Every conversation in the export, from however many files it was split into."""
    found = []
    for path in sorted(glob.glob(str(folder / 'conversations-*.json'))) or [str(folder / 'conversations.json')]:
        value = _load(path, [])
        if isinstance(value, list):
            found.extend(c for c in value if isinstance(c, dict))
    return found


def branch(conversation):
    """The messages along the branch the conversation ended on, first to last: from its current message up through
    each one's parent. An export without a current message ends on its newest."""
    mapping = conversation.get('mapping') or {}
    node = conversation.get('current_node')
    if node not in mapping:
        dated = [(n.get('message') or {}).get('create_time') or 0 for n in mapping.values()]
        node = list(mapping)[dated.index(max(dated))] if mapping else None
    path, seen = [], set()
    while node in mapping and node not in seen:
        seen.add(node)
        path.append(mapping[node])
        node = mapping[node].get('parent')
    return list(reversed(path))


def said(node):
    """(role, when, text, attachments) for a message the owner or ChatGPT said, else None."""
    message = node.get('message') or {}
    role = (message.get('author') or {}).get('role')
    content = message.get('content') or {}
    meta = message.get('metadata') or {}
    if role not in SAID or content.get('content_type') not in CONTENT or meta.get('is_visually_hidden_from_conversation'):
        return None
    pieces = []
    for part in content.get('parts') or []:
        if isinstance(part, str):
            pieces.append(part)
        elif isinstance(part, dict) and part.get('content_type') == 'audio_transcription' and part.get('text'):
            pieces.append(str(part['text']))
    attached = [a for a in meta.get('attachments') or [] if isinstance(a, dict)]
    text = clean('\n\n'.join(pieces))
    if not text and not attached:
        return None
    return role, message.get('create_time'), text, attached


def _when(value):
    """Seconds since 1970 from the export's numbers or ISO dates."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _day(seconds):
    return dt.datetime.fromtimestamp(seconds).astimezone().date().isoformat() if seconds else None


class Files:
    """The export's attachments: where each is, what it was called, and which conversation it came from."""

    def __init__(self, folder):
        self.folder = folder
        self.names = _load(folder / 'conversation_asset_file_names.json', {})
        self.library = [e for e in _load(folder / 'library_files.json', []) if isinstance(e, dict)]

    def path(self, ident):
        for name in (f'{ident}.dat', f'{ident.replace("-", "_")}.dat'):
            if (self.folder / name).is_file():
                return self.folder / name
        return None

    def name(self, ident, fallback=''):
        path = self.path(ident)
        return (self.names.get(path.name) if path else None) or fallback or ident

    def of(self, conversation):
        """[(file id, name)] the library says came from this conversation."""
        return [(e['file_id'], e.get('file_name') or e['file_id']) for e in self.library
                if e.get('origination_thread_id') == conversation and e.get('file_id')]


def attachment_text(path, name, mac=macos):
    """The text of one attached document, or '' for anything else (pictures, sound, video, archives)."""
    kind = READ.get(Path(name).suffix.lower())
    if not kind or path is None:
        return ''
    with open(path, 'rb') as file:
        head = file.read(4)
    if kind == 'pdf' and head == b'%PDF':
        return '\n\n'.join(mac.pdf_pages(path))
    if kind in ('docx', 'doc'):
        return mac.textutil(path, fmt=kind)
    if kind == 'text':
        return Path(path).read_bytes()[:4 * 1024 * 1024].decode('utf-8', 'replace')
    return ''


def _signature(*values):
    return hashlib.sha1(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def describe(conversation, files, mac=macos):
    """(item, parts) for one conversation, or None for one with nothing said in it."""
    ident = str(conversation.get('conversation_id') or conversation.get('id') or '')
    if not ident:
        return None
    title = ' '.join(str(conversation.get('title') or '').split()) or 'Untitled conversation'
    messages, attached = [], {}
    for node in branch(conversation):
        found = said(node)
        if found:
            messages.append(found)
            for a in found[3]:
                if a.get('id'):
                    attached[a['id']] = (len(messages), files.name(a['id'], a.get('name', '')))
    for file_id, name in files.of(ident):
        attached.setdefault(file_id, (len(messages), name))
    if not messages:
        return None
    return _item('c:' + ident, title, conversation.get('create_time'), conversation.get('update_time'), messages,
                 attached, files, mac, {'starred': bool(conversation.get('is_starred')),
                                        'archived': bool(conversation.get('is_archived'))})


def _item(ident, title, started, updated, messages, attached, files, mac, meta):
    """The item and parts of one conversation: a part for each piece of what was said, numbered by message."""
    started = _when(started) or next((_when(m[1]) for m in messages if m[1]), None)
    updated = _when(updated) or next((_when(m[1]) for m in reversed(messages) if m[1]), started)
    names = [name for _, name in attached.values()]
    head = words(title, names)
    parts, kept = [{'page': 0, 'head': head, 'body': ''}], 0
    for number, (role, when, text, _) in enumerate(messages, 1):
        for piece in chunks(text):
            if kept + len(piece) > MOST:
                break
            parts.append({'page': number, 'role': role, 'at': _when(when), 'head': head, 'body': piece})
            kept += len(piece)
    read = 0
    for file_id, (number, name) in attached.items():
        try:
            text = attachment_text(files.path(file_id), name, mac)
        except Exception:  # noqa: BLE001 - an attachment that can't be read is still found by its name
            text = ''
        if text:
            read += 1
            for piece in chunks(text):
                if kept + len(piece) > MOST:
                    break
                parts.append({'page': number, 'role': 'file', 'head': words(title, name), 'body': piece})
                kept += len(piece)
    item = {'id': ident, 'source': 'chats', 'kind': 'chat', 'title': title, 'at': started, 'day': _day(started),
            'until': updated, 'meta': {**meta, 'messages': len(messages), 'attachments': names[:50], 'read': read}}
    return item, parts


def group(chat, files, mac=macos):
    """(item, parts) for one group chat: its members' messages and ChatGPT's, and the links shared in it."""
    messages = []
    for m in chat.get('messages') or []:
        if not isinstance(m, dict):
            continue
        links = [words(a.get('title'), a.get('desc')) for a in m.get('attachments') or [] if isinstance(a, dict)]
        text = clean('\n\n'.join([str(m.get('text') or ''), *links]))
        if text:
            messages.append(('assistant' if m.get('role') == 'assistant' else 'user', m.get('created_at'), text, []))
    if not messages or not chat.get('id'):
        return None
    title = ' '.join(str(chat.get('name') or '').split()) or 'Group chat'
    return _item('g:' + str(chat['id']), title, chat.get('created_at'), chat.get('updated_at'), messages, {}, files, mac,
                 {'group': True})


def index(conn, mac=macos, now=None, say=print):
    """Bring the ChatGPT history in the index up to date. Returns (counts, errors)."""
    from . import store
    stamp = now or dt.datetime.now().timestamp()
    folder = root()
    had = {**store.known(conn, 'chats')}
    counts = {'items': 0, 'new': 0, 'changed': 0, 'messages': 0, 'attachments_read': 0, 'groups': 0}
    errors, keep = {}, set()
    if not folder.is_dir():
        if had:
            raise RuntimeError('the export folder is gone')
        return counts, errors
    files = Files(folder)
    groups = _load(folder / 'group_chats.json', {}).get('chats') or []
    library = _signature(files.names, files.library)
    every = [('c', c) for c in conversations(folder)] + [('g', g) for g in groups if isinstance(g, dict)]
    if had and not every:
        raise RuntimeError('the export answered with no conversations')
    for kind, raw in every:
        ident = ('c:' + str(raw.get('conversation_id') or raw.get('id') or '')) if kind == 'c' else 'g:' + str(raw.get('id') or '')
        sig = _signature(READER, raw.get('update_time') or raw.get('updated_at'), len(raw.get('mapping') or raw.get('messages') or ()),
                         raw.get('current_node'), raw.get('title') or raw.get('name'), library)
        if had.get(ident) == sig:
            keep.add(ident)
            counts['items'] += 1
            continue
        try:
            described = describe(raw, files, mac) if kind == 'c' else group(raw, files, mac)
        except Exception as error:  # noqa: BLE001 - one odd conversation never stops the rest
            errors[type(error).__name__] = errors.get(type(error).__name__, 0) + 1
            continue
        if described is None:
            continue
        item, parts = described
        item['sig'], item['seen'] = sig, stamp
        counts['new' if ident not in had else 'changed'] += 1
        counts['messages'] += item['meta']['messages']
        counts['attachments_read'] += item['meta']['read']
        counts['groups'] += kind == 'g'
        store.put(conn, item, parts)
        keep.add(ident)
        counts['items'] += 1
    store.seen(conn, [i for i in keep if i in had], stamp)
    gone = [i for i in had if i not in keep]
    store.forget(conn, gone)
    counts['removed'] = len(gone)
    conn.commit()
    return counts, errors
