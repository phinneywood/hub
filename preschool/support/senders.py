import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,255}$")

def valid_sender(value):
    return isinstance(value, str) and bool(EMAIL_RE.fullmatch(value))
