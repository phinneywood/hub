"""Actions the owner lets Hub do without asking.

Each runs only at the trust level “Just do it and tell me”, which the owner chooses per action on
the Engine page; none starts there. It runs with the morning job, before the overnight review, and
leaves a note in “Needs you” saying what it did and how to undo it. No model is involved.
"""
from .. import home, store
from . import catalog, state

KIND = 'auto'


def run():
    """Do every trusted action that has something to do. Returns the notes left; never raises."""
    day = home.today().isoformat()
    notes = []
    for action in catalog.ACTIONS:
        if not action.auto or state.trust(action) != 'auto':
            continue
        try:
            said = action.auto()
        except Exception:  # noqa: BLE001 - reported as a note; the reason is not kept
            state.record_run(day, KIND, 'error')
            notes.append(state.add_note(day, f'auto-{action.slug}', 'warn', f'Hub couldn’t do this by itself: {action.name}',
                                        'Nothing was changed. It will try again tomorrow.', action.page, 'Review'))
            continue
        if said:
            state.record_run(day, KIND, 'ok')
            store.log('engine_auto', f'{action.slug}: {said}')
            notes.append(state.add_note(day, f'auto-{action.slug}', 'info', said,
                                        f'You let Hub do this without asking. To undo: {action.undo}', action.page, 'Review'))
    return notes
