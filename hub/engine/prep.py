"""Each source gets its own pages ready, once a day with the morning job.

Read-only housekeeping with no model: a source's `prepare` saves what its page and its “Needs you”
items would otherwise have to fetch while the owner waits. One that fails is skipped and logged
by name; the reason is not kept.
"""
from .. import store
from . import catalog


def run():
    """Run every source's `prepare`. Returns the slugs that finished; never raises."""
    done = []
    for source in catalog.SOURCES:
        if not source.prepare:
            continue
        try:
            source.prepare()
            done.append(source.slug)
        except Exception:  # noqa: BLE001 - one source must not stop the others or the review
            store.log('engine_prepare_failed', source.slug)
    return done
