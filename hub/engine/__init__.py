"""The automation engine behind Hub.

Five fixed parts, so a new capability is one entry in `catalog`, not a new design:

  sources       what Hub may read, and what of it may be shown to a model
  judgement     a model on the zero-retention route, with no tools: it only returns text
  you           the “Needs you” list: open, do, or Not today
  actions       what Hub may do, each with a preview page, an undo and a trust level
  record        every run, its model calls and cost, with hard daily and monthly limits

Standing instructions are the owner's plain-words guidance. They steer judgement only;
they cannot grant an action or a source that is not in the catalog.
"""
