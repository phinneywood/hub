"""Existing fixed privacy-preserving transport; no organizer actions."""
import json
import re
import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"

MODEL = "openai/gpt-6-luna"

PROVIDER = {"only": ["azure/eu"], "allow_fallbacks": False, "zdr": True, "data_collection": "deny",
            "require_parameters": True}

KEYCHAIN_SERVICE = "hub-openrouter"

MAX_OUTPUT_TOKENS = 600

TIMEOUT = 40

class LunaError(Exception):
    pass


def key():
    from hub import model_key
    return model_key.read()

def configured():
    return bool(key())

def clip(text, limit):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text))[:limit].strip()

def complete(request):
    token = key()
    if not token:
        raise LunaError("Luna isn't set up yet")
    try:
        response = httpx.post(URL, json=request, timeout=TIMEOUT, follow_redirects=False,
                              headers={"Authorization": "Bearer " + token, "X-Title": "Hub"})
    except httpx.HTTPError:
        raise LunaError("Luna couldn't be reached")
    if response.status_code != 200:
        try:
            reason = clip(response.json().get("error", {}).get("message", ""), 200)
        except (ValueError, AttributeError):
            reason = ""
        raise LunaError(f"Luna is unavailable right now (status {response.status_code}){': ' + reason if reason else ''}")
    try:
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LunaError("Luna's reply was incomplete")
        content = choice["message"]["content"]
        proposal = json.loads(content)
    except (KeyError, IndexError, ValueError, TypeError):
        raise LunaError("Luna's reply wasn't usable")
    if not isinstance(proposal, dict):
        raise LunaError("Luna's reply wasn't usable")
    return proposal
