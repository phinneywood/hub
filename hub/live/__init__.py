"""Live checks: small tests of the real running system, kept with their history. See docs/live-checks.md.

`checks.py` says what is checked, `runner.py` runs them and keeps every result in `live-checks.db` in the
private data folder, `routes.py` is the Checks page, and `python -m hub.live` is the command line.
"""
