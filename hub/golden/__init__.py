"""Golden tests: real requests a person sends Hub, run through the real Ask pipeline, with checks on what
Hub did, what it would have changed, and what it said. See docs/golden-tests.md.

  scenarios.py  the requests, their checks, and a well-behaved script for offline runs (fictional names)
  checks.py     the checks, and the trace they read
  dryrun.py     Hub's real validation runs; the change is recorded and never sent
  runner.py     offline (scripted model, in the test suite) and live (real model, cost-capped) runs
  routes.py     the Golden tests page in Settings: the latest result, and a button for a live dry run
"""
