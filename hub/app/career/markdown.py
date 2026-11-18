"""The little Markdown the desk is written in, read into blocks the app draws itself (it never sets HTML).

A file may start with front matter between two `---` lines, one `key: value` a line. The body is headings,
paragraphs, lists (with `- [ ]` and `- [x]` for steps), quotes, code fences and rules. Inline: **bold**, *italic*,
`code` and [links](https://...) to web pages only.
"""
import re

LINK = re.compile(r'\[([^\]\n]{1,300})\]\((https?://[^\s)]{1,800})\)')
INLINE = re.compile(r'(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s][^*\n]*\*|\[[^\]\n]{1,300}\]\(https?://[^\s)]{1,800}\)|<https?://[^\s>]{1,800}>)')
HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
ITEM = re.compile(r'^(\s*)([-*+]|\d{1,3}[.)])\s+(\[( |x|X)\]\s+)?(.*)$')
FENCE = re.compile(r'^\s*(```|~~~)')
RULE = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')
KEY = re.compile(r'^([A-Za-z][A-Za-z0-9_ -]{0,40}):\s*(.*)$')


def front(text):
    """(the front matter as {key: value}, the rest of the text)."""
    text = str(text or '').replace('\r\n', '\n').lstrip('﻿')
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---', 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].split('\n'):
        found = KEY.match(line.strip())
        if found:
            value = found.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            meta[found.group(1).strip().lower().replace(' ', '_')] = value
    return meta, text[end + 4:].lstrip('\n')


def with_front(meta, body):
    """A file's text again from its front matter and body: what `front` read, written back."""
    lines = [f'{key}: {value}' for key, value in meta.items() if value not in (None, '')]
    return ('---\n' + '\n'.join(lines) + '\n---\n\n' if lines else '') + body.lstrip('\n')


def inline(text):
    """A line of text as spans: {'t': 'text' | 'b' | 'i' | 'code' | 'link', 'text', 'href'?}."""
    spans, at = [], 0
    text = str(text)
    for found in INLINE.finditer(text):
        if found.start() > at:
            spans.append({'t': 'text', 'text': text[at:found.start()]})
        piece = found.group(0)
        if piece.startswith('**'):
            spans.append({'t': 'b', 'text': piece[2:-2]})
        elif piece.startswith('`'):
            spans.append({'t': 'code', 'text': piece[1:-1]})
        elif piece.startswith('<'):
            spans.append({'t': 'link', 'text': piece[1:-1], 'href': piece[1:-1]})
        elif piece.startswith('['):
            link = LINK.match(piece)
            spans.append({'t': 'link', 'text': link.group(1), 'href': link.group(2)})
        else:
            spans.append({'t': 'i', 'text': piece[1:-1]})
        at = found.end()
    if at < len(text):
        spans.append({'t': 'text', 'text': text[at:]})
    return spans


def plain(text):
    """Text without its Markdown marks: what a sentence says, for a summary or for the model."""
    text = LINK.sub(lambda m: m.group(1), str(text))
    text = re.sub(r'<(https?://[^\s>]+)>', r'\1', text)
    text = re.sub(r'(\*\*|__|`)', '', text)
    text = re.sub(r'(?<![\w*])\*(?!\s)([^*\n]+)\*', r'\1', text)
    return ' '.join(text.split())


def blocks(body):
    """The body as blocks: {'t': 'h', 'level', 'text'}, {'t': 'p', 'text'}, {'t': 'ul' | 'ol', 'items': [{'text',
    'done'}]}, {'t': 'quote', 'text'}, {'t': 'code', 'text'}, {'t': 'hr'}. Text is a list of spans."""
    out, para, lines = [], [], str(body or '').replace('\r\n', '\n').split('\n')

    def close():
        if para:
            out.append({'t': 'p', 'text': inline(' '.join(para))})
            para.clear()

    n = 0
    while n < len(lines):
        line = lines[n]
        if FENCE.match(line):
            close()
            mark, code = FENCE.match(line).group(1), []
            n += 1
            while n < len(lines) and not lines[n].strip().startswith(mark):
                code.append(lines[n])
                n += 1
            out.append({'t': 'code', 'text': '\n'.join(code)})
        elif not line.strip():
            close()
        elif HEADING.match(line):
            close()
            found = HEADING.match(line)
            out.append({'t': 'h', 'level': min(len(found.group(1)), 3), 'text': inline(found.group(2))})
        elif RULE.match(line):
            close()
            out.append({'t': 'hr'})
        elif line.lstrip().startswith('>'):
            close()
            quote = []
            while n < len(lines) and lines[n].lstrip().startswith('>'):
                quote.append(lines[n].lstrip()[1:].strip())
                n += 1
            out.append({'t': 'quote', 'text': inline(' '.join(q for q in quote if q))})
            continue
        elif ITEM.match(line):
            close()
            kind = 'ol' if ITEM.match(line).group(2)[0].isdigit() else 'ul'
            items = []
            while n < len(lines) and (ITEM.match(lines[n]) or (items and lines[n].startswith(('  ', '\t')) and lines[n].strip())):
                found = ITEM.match(lines[n])
                if found:
                    box = found.group(4)
                    items.append({'text': found.group(5).strip(), 'done': None if box is None else box.lower() == 'x'})
                else:                       # a wrapped line of the item above
                    items[-1]['text'] += ' ' + lines[n].strip()
                n += 1
            out.append({'t': kind, 'items': [{'text': inline(i['text']), 'done': i['done']} for i in items]})
            continue
        else:
            para.append(line.strip())
        n += 1
    close()
    return out


def links(body):
    """Every web link in the text, in order and once each: [(text, href)]."""
    seen, out = set(), []
    for found in LINK.finditer(str(body or '')):
        if found.group(2) not in seen:
            seen.add(found.group(2))
            out.append((found.group(1), found.group(2)))
    return out
