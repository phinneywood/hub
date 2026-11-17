"""Access control for requests arriving through `tailscale serve`.

The app listens on loopback only. Tailscale Serve adds Tailscale-User-Login to
proxied requests; requests without it come from this Mac itself.
"""
from fastapi import HTTPException, Request

from . import config, people


def require_owner(request: Request):
    login = request.headers.get("tailscale-user-login")
    if login is None:
        return
    if login.lower() not in config.ALLOWED_LOGINS:
        raise HTTPException(403, "Not allowed")


NOT_YOURS = "This page shows another person’s Google account. Hub shows each person only their own mail and calendar."


class NotYours(Exception):
    """A page that shows a Google account the asking login may not see. hub/main.py answers a page with a plain one
    saying whose it is, and a change with a 403."""


def account_owner(account):
    """For pages that show one person's Google account: only a login that may see it gets in (hub/people.py).
    Everyone else, a program on this Mac included, is turned away."""
    def check(request: Request):
        if not people.sees(request.headers.get("tailscale-user-login"), account):
            raise NotYours()
    return check
