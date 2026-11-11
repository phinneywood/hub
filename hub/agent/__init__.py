"""Ask Hub: a read-only chat over the engine catalog's lookups.

  model.py   the one place the chat's model is built: Luna's pinned zero-retention route, with a guard
  agent.py   the Pydantic AI agent; its tools are exactly the catalog's lookups
  store.py   threads and their history, in Hub's database, with their own retention
  routes.py  the /ask page, its stream and saved threads; budget and record of every run

Phase 1 reads only: there is no tool that changes anything, and no approval card yet.
"""
from .routes import router

TOOL = {"slug": "ask", "name": "Ask", "description": "Ask about today, mail, the calendar and the trip", "router": router}
