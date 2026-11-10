"""Each source sorts what is new, first thing in the morning job.

Sorting comes before anything Hub does by itself and before the overnight review, so both work from
today's picture. A source's `sort` keeps its own limits and records its own runs. One that fails is
skipped and logged by name; the reason is not kept.
"""
from .. import store
from . import catalog


def run():
    """Run every source's `sort`. Returns the slugs that finished; never raises."""
    done = []
    for source in catalog.SOURCES:
        if not source.sort:
            continue
        try:
            source.sort()
            done.append(source.slug)
        except Exception:  # noqa: BLE001 - one source must not stop the others or the review
            store.log('engine_sort_failed', source.slug)
    return done
