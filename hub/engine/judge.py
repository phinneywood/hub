"""Yes/no judgement over one message, by Jev on the same zero-retention route preschool uses.

Jev sees a subject and a body with links and email addresses removed, and returns a probability only.
Each question is fixed here; callers pick one by name.
"""
import math
import re

import httpx

from preschool import jev

from .. import luna

MAX_BODY = 6000
ADDRESS = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
QUESTIONS = {
    'needs_owner': {
        'type': 'noul',
        'instructions': 'Does this email need its recipient to personally do something?',
        'criteria': {
            'true': 'A person or organisation asks the recipient to reply, decide, sign, pay, book, bring or prepare '
                    'something, or tells them of a deadline, a changed or cancelled appointment, a bill that is due, '
                    'or a problem with an account, order or delivery that they must handle.',
            'false': 'Marketing, newsletters, digests, receipts and confirmations that need no step, social or app '
                     'notifications, and routine automated notices. A generic "shop now" or "learn more" is not a request.',
        },
    },
    'expects_reply': {
        'type': 'noul',
        'instructions': 'Is the writer of this email waiting for the person they wrote to to answer?',
        'criteria': {
            'true': 'The writer asks a question, asks for a decision, a time, a document or a confirmation, or '
                    'otherwise leaves something open that only the other person can settle.',
            'false': 'A thank-you, an acknowledgement, an answer that closes the matter, something sent only for '
                     'information, or a message to a company address that sends automatic replies.',
        },
    },
}


class JudgeError(Exception):
    pass


def _post(url, request, token):
    response = httpx.post(url, json=request, timeout=jev.TIMEOUT, follow_redirects=False,
                          headers={'Authorization': 'Bearer ' + token, 'X-Title': 'Hub'})
    if response.status_code != 200:
        raise JudgeError('Jev is unavailable.')
    return response.json()


def scrub(text):
    """Message text as a model may see it: no links and no email addresses, such as those in a quoted reply."""
    return ADDRESS.sub('[address]', jev.URL_RE.sub('[link]', str(text or '')))


def ask(question, subject, text, transport=None):
    """Probability 0-1 that the answer to one of QUESTIONS is yes for this message."""
    token = luna.key()
    if not token:
        raise JudgeError('Jev is unavailable.')
    body = scrub(text)[:MAX_BODY].strip()
    if not body:
        raise JudgeError('Nothing to judge.')
    request = {'model': jev.MODEL, 'provider': jev.PROVIDER, 'questions': {question: QUESTIONS[question]},
               'state': {'subject': ADDRESS.sub('[address]', str(subject or ''))[:300], 'body': body}}
    try:
        data = (transport or _post)(jev.URL, request, token)
        answer = data['answers'][question]
        value = answer['noul'] if answer.get('type') == 'noul' else None
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                or data.get('provider') != 'TypeSafe' or not str(data.get('model', '')).startswith(jev.MODEL)):
            raise ValueError
        return float(value)
    except JudgeError:
        raise
    except Exception:  # noqa: BLE001 - provider replies and errors must not travel further
        raise JudgeError('Jev is unavailable.') from None


def needs_owner(subject, text, transport=None):
    """Probability 0-1 that the message needs the owner to act."""
    return ask('needs_owner', subject, text, transport)
