"""Text as the index keeps it: cleaned, and cut into pieces of about 1,000 characters where a paragraph, a sentence
or at least a word ends, so a search finds the page or the message a thing was said in, not just the file."""
import re

SIZE = 1000                 # characters a piece aims for
MOST = 400_000              # characters kept from one document, page by page; the rest is left unread
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​﻿]')
SENTENCE = re.compile(r'(?<=[.!?…])\s+')


def clean(text):
    """Text without control characters, with runs of spaces made one and at most one blank line in a row."""
    text = CONTROL.sub(' ', str(text or '')).replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[ \t\xa0]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _sentences(paragraph, size):
    """A long paragraph as pieces no longer than `size`, broken after sentences, else between words."""
    out, piece = [], ''
    for sentence in SENTENCE.split(paragraph):
        while len(sentence) > size:                  # one very long sentence: break it between words
            cut = sentence.rfind(' ', 0, size)
            cut = cut if cut > size // 2 else size
            if piece:
                out.append(piece)
                piece = ''
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if piece and len(piece) + 1 + len(sentence) > size:
            out.append(piece)
            piece = sentence
        else:
            piece = f'{piece} {sentence}'.strip()
    if piece:
        out.append(piece)
    return out


def chunks(text, size=SIZE):
    """Cleaned text in pieces of about `size` characters, each whole paragraphs where they fit."""
    out, piece = [], ''
    for paragraph in clean(text).split('\n\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > size:
            if piece:
                out.append(piece)
                piece = ''
            out.extend(_sentences(paragraph.replace('\n', ' '), size))
            continue
        if piece and len(piece) + 2 + len(paragraph) > size:
            out.append(piece)
            piece = paragraph
        else:
            piece = f'{piece}\n\n{paragraph}' if piece else paragraph
    if piece:
        out.append(piece)
    return out


def words(*values):
    """The words of several fields as one line for the index: lists are flattened, empty ones left out."""
    out = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            out.extend(str(v) for v in value if v)
        elif value:
            out.append(str(value))
    return ' · '.join(dict.fromkeys(' '.join(v.split()) for v in out if v and v.strip()))
