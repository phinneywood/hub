#!/usr/bin/env python3
"""Refuse personal information and secrets before they reach Git.

Two layers:
- Generic patterns that need no private data: real-looking email addresses, home paths, phone
  numbers, private hostnames and common secret formats. CI runs these over every tracked file.
- The owner's private word list (names, places, school), read from outside the repository. The
  commit hooks in githooks/ add it and fail closed when it is missing.

Usage:
  privacy_scan.py --staged            added lines and paths in the index (pre-commit hook)
  privacy_scan.py --message FILE      a commit message (commit-msg hook)
  privacy_scan.py --all               every tracked file (CI; add --no-denylist there)

Word list: one case-insensitive term per line; '#' starts a comment; a line starting with '!' is
an allowed phrase that is removed before matching (for example a public account name that
contains a private term). Findings never print the private term itself.

A line may opt out of the generic patterns with the marker `privacy-scan: allow`, for synthetic
test values only. It never exempts private word-list terms.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_DENYLIST = Path.home() / "Library" / "Application Support" / "Hub" / "pii-denylist.txt"
MARKER = "privacy-scan: allow"

EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
SAFE_DOMAIN = re.compile(r"(^|\.)(example|invalid|test|localhost)(\.[a-z]+)*$|^example\.(com|net|org)$", re.I)
SERVICE_ADDRESSES = {"calendar-notification@google.com", "noreply@anthropic.com", "noreply@github.com"}
SERVICE_DOMAINS = re.compile(r"(^|\.)users\.noreply\.github\.com$", re.I)
PATTERNS = [
    ("home path", re.compile(r"/Users/(?!\.\.\.|Shared\b)[A-Za-z0-9_-]+")),
    ("phone number", re.compile(r"(?<!\d)\(?\d{3}\)?[-. ](?!555[-. ]01\d\d)\d{3}[-. ]\d{4}(?!\d)")),
    ("private hostname", re.compile(r"\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b", re.I)),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("API key", re.compile(r"\bsk-(?:or-v1-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google key or token", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b|\bya29\.[0-9A-Za-z_-]{20,}")),
    ("bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
]


def load_denylist(path):
    terms, allowed = [], []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        (allowed if line.startswith("!") else terms).append(line.lstrip("!").lower())
    return terms, allowed


def scan_line(text, terms=(), allowed=()):
    """Return the finding categories for one line of text."""
    found = []
    if terms:
        lowered = text.lower()
        for phrase in allowed:
            lowered = lowered.replace(phrase, " ")
        if any(term in lowered for term in terms):
            found.append("private word-list term")
    if MARKER in text:
        return found
    for match in EMAIL.finditer(text):
        if not (SAFE_DOMAIN.search(match.group(1)) or SERVICE_DOMAINS.search(match.group(1))
                or match.group(0).lower() in SERVICE_ADDRESSES):
            found.append("email address")
            break
    found.extend(name for name, pattern in PATTERNS if pattern.search(text))
    return found


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def staged_lines():
    path, number = None, 0
    for line in git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path:
                yield path, 0, path  # the path itself
        elif line.startswith("@@"):
            number = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            yield path, number, line[1:]
            number += 1


def tracked_lines():
    for path in git("ls-files", "-z").split("\0"):
        if not path:
            continue
        yield path, 0, path
        try:
            data = Path(path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in data[:8192]:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            yield path, number, line


def message_lines(path):
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.startswith("#"):
            yield "commit message", number, line


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--message")
    parser.add_argument("--denylist", default=os.environ.get("HUB_PII_DENYLIST", str(DEFAULT_DENYLIST)))
    parser.add_argument("--no-denylist", action="store_true", help="generic patterns only (CI)")
    args = parser.parse_args(argv)

    terms, allowed = [], []
    if not args.no_denylist:
        try:
            terms, allowed = load_denylist(args.denylist)
        except FileNotFoundError:
            print(f"privacy scan: private word list not found at {args.denylist}.\n"
                  "Create it (owner-only) or set HUB_PII_DENYLIST. Refusing to continue.", file=sys.stderr)
            return 2

    lines = staged_lines() if args.staged else tracked_lines() if args.all else message_lines(args.message)
    findings = [(path, number, kind) for path, number, text in lines for kind in scan_line(text, terms, allowed)]
    for path, number, kind in findings:
        where = f"{path} (file name)" if number == 0 else f"{path}:{number}"
        print(f"privacy scan: {where}: {kind}", file=sys.stderr)
    if findings:
        print(f"privacy scan: {len(findings)} finding(s). Move real values to private data, or use "
              "fictional ones (Alex, Sam, *.example).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
