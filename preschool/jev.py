"""Bounded Jev relevance judgments over the OpenRouter Decisions API."""
import math
import re

import httpx

from .support import luna
from . import criteria, school

URL = 'https://openrouter.ai/api/alpha/decisions'
MODEL = 'typesafe/jev-1.14'
PROVIDER = {'only': ['TypeSafe'], 'allow_fallbacks': False, 'zdr': True, 'data_collection': 'deny'}
TIMEOUT = 25
MAX_TEXT = 18000
URL_RE = re.compile(r'https?://[^\s<>"\']+', re.I)


class JevError(Exception):
    pass


# Built per request from the private school settings; see questions().
_QUESTIONS = {
    'preschool_relevant': {
        'type': 'noul',
        'instructions': 'Is this current email materially useful to a family participating in the {SCHOOL} ({SHORT})?',
        'criteria': {
            'true': 'The current authored content gives a {SHORT} family something concrete to know or do for this class: class/teacher updates; schedules, closures or events; parent duties; what to bring or prepare; tasks, forms and deadlines; {SHORT}-specific resources; or class/co-op tuition, fees or contributions, preserving the stated amount or due date when provided. A class-group recipient plus clearly class-specific content qualifies even if {SHORT} is not spelled out. In mixed {ORG_SHORT}/{NEWS} newsletters or blogs, retain only the specific {SHORT} class item.',
            'false': 'The only {SHORT} reference is in old quoted history or a signature; the content is unrelated personal, billing or neighborhood mail; or a promotion/automated notice has no concrete {SHORT} class-useful item. A group recipient alone is not enough.'
        }
    },
    'useful_information': {
        'type': 'noul',
        'instructions': 'Does the current email contain concrete information a family needs to know or do for this {SHORT} class, including what is coming up, what to prepare, or what is due?',
        'criteria': {
            'true': 'A family could use a current class fact, event, date, schedule detail, classroom update, practical reminder, preparation reminder, required action, deadline, form, {SHORT}-specific resource, or class/co-op bill due date and stated amount. To-do reminders matter even when there is no calendar event.',
            'false': 'There is no concrete current {SHORT} class-useful information; billing is unrelated to this class; the only useful detail is old quoted history; or a mixed newsletter/blog has no specific preschool-class item.'
        }
    }
}


def questions():
    return school.fill(_QUESTIONS)


def __getattr__(name):
    if name == 'QUESTIONS':
        return questions()
    raise AttributeError(name)


def class_group_match(recipients, class_group):
    recipients = recipients if isinstance(recipients, dict) else {}
    headers = ' '.join(str(recipients.get(k) or '')[:1500] for k in ('to', 'cc', 'list-id'))
    # Match a whole address/list token, never a substring of a different list.
    return bool(class_group and re.search(r'(?<![\w.%+@-])' + re.escape(class_group) + r'(?![\w.%+@-])', headers, re.I))


def request_state(message, class_group, supporting_sources=()):
    text = message.get('text') or ''
    if not isinstance(text, str) or (not text.strip() and not supporting_sources):
        raise JevError('Message text is empty or exceeds the decision limit.')
    text=URL_RE.sub('[linked resource]',text) if text.strip() else '[No readable email body; evaluate the bounded supporting sources and class-group context.]'
    subject = str(message.get('subject') or '')
    if len(subject) > 500:
        raise JevError('Message subject exceeds the decision limit.')
    sources=[]; used=len(subject)+len(text)
    if not isinstance(supporting_sources,(list,tuple)) or len(supporting_sources)>8:
        raise JevError('Supporting-source bounds.')
    for source in supporting_sources:
        if not isinstance(source,dict) or not isinstance(source.get('text'),str):
            raise JevError('Supporting-source format.')
        label=str(source.get('label') or 'Supporting source')[:200]
        value=URL_RE.sub('[linked resource]',source['text'])
        used+=len(label)+len(value)
        if used>MAX_TEXT:
            raise JevError('Combined email and source text exceeds the decision limit.')
        sources.append({'kind':str(source.get('kind') or 'source')[:40],'label':label,'text':value})
    if used>MAX_TEXT:
        raise JevError('Combined email and source text exceeds the decision limit.')
    group_match = class_group_match(message.get('recipients'), class_group)
    # Minimize personal data: Jev gets neither mailbox/sender addresses nor recipient lists.
    return {'criteria_version': criteria.VERSION, 'subject': subject, 'body': text,
            'supporting_sources': sources,'addressed_to_configured_class_group': group_match,
            'message_has_attachments': bool(message.get('attachments'))}


def validate(data):
    try:
        if not isinstance(data, dict) or not isinstance(data.get('answers'), dict):
            raise ValueError()
        answers = data['answers']
        names = questions()
        if set(answers) != set(names):
            raise ValueError()
        scores = {}
        for name in names:
            value = answers[name]
            probability = value.get('noul') if isinstance(value, dict) and value.get('type') == 'noul' else None
            if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError()
            scores[name] = float(probability)
        if data.get('provider') != 'TypeSafe' or not str(data.get('model', '')).startswith(MODEL):
            raise ValueError()
        return scores
    except (AttributeError, TypeError, ValueError):
        raise JevError('Jev returned an unusable decision.') from None


def decide(message, class_group=None, transport=None, supporting_sources=()):
    token = luna.key()
    if not token:
        raise JevError('Jev is unavailable.')
    state = request_state(message, class_group, supporting_sources)
    request = {'model': MODEL, 'provider': PROVIDER, 'state': state, 'questions': questions()}
    if transport:
        try:
            response = transport(URL, request, token)
            return validate(response)
        except JevError:
            raise
        except Exception:
            raise JevError('Jev is unavailable.') from None
    try:
        response = httpx.post(URL, json=request, timeout=TIMEOUT, follow_redirects=False,
                              headers={'Authorization': 'Bearer ' + token, 'X-Title': 'Hub Preschool Relevance'})
        if response.status_code != 200:
            raise JevError('Jev is unavailable.')
        return validate(response.json())
    except JevError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise JevError('Jev is unavailable.') from None
