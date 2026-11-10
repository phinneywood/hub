"""Access control for requests arriving through `tailscale serve`.

The app listens on loopback only. Tailscale Serve adds Tailscale-User-Login to
proxied requests; requests without it come from this Mac itself.
"""
from fastapi import HTTPException, Request

from . import config


def require_owner(request: Request):
    login = request.headers.get("tailscale-user-login")
    if login is None:
        return
    if login.lower() not in config.ALLOWED_LOGINS:
        raise HTTPException(403, "Not allowed")
