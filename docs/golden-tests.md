# Golden tests

Status: framework and seed scenarios built (2026-11-13). No live run yet; the first one is arranged with the owner.
Run by hand for now; whether a live run gates Deploy is revisited after a week (owner, 2026-11-13).

## The idea

A golden test is a request a real person would send, written down with what must be true afterwards.
"Softly play some music in the living room while I read a bedtime story" must play a real album or playlist
(never the sentence itself), only on the living-room speaker, quietly before it starts, and the reply must
say what was picked.

Unit tests check one function with made-up inputs. Golden tests check the whole path a request takes: the
model's choice of tools, Hub's validation, what would change, and the words that come back. They catch the
failures that only show up end to end: a model passing the owner's whole sentence as a search, "as well"
starting a second stream, "remember …" becoming a playlist.

Every new user-facing capability ships with golden scenarios: at least one request it should handle and one
it should refuse or leave alone.

## How it works in Hub

```
 scenario (repo, fictional words)        ─┐
   turns:  "play it in the {kitchen} as well"│
   checks: Joined(living_room_speaker, kitchen_speaker), ChangesOnly(...)
   script: what a well-behaved model does   │
                                            ▼
 runner ── agent.build() + agent.settings() ── the same agent, tools, limits and recall as /ask
   │            │
   │            ├─ offline: scripted model follows `script`   (pytest, CI; made-up household)
   │            └─ live:    the real model on the pinned zero-retention route (model.build())
   ▼
 dry-run layer ── Hub's real checks run; the change is recorded, never sent
   │   Home Assistant  broker._exchange   (after broker._checked)
   │   Music Assistant music._send        (after music._call's allowlist)
   │   Kitchen         client.call        (anything but a read)
   ▼
 trace: messages, replies, tool calls and answers, intended changes  ──▶ checks ──▶ pass / why not
```

- **Scenarios** live in `hub/golden/scenarios.py`. Wording is fictional; `{living_room}`, `{kitchen_speaker}`
  and so on are aliases. Offline they are the made-up names; live, the private `household.json` can map
  them to the house's own names under `"golden": {"names": {"living_room": "…"}}`. No real name is ever
  in the repository.
- **Checks** (`hub/golden/checks.py`) read the trace: `Called`, `NotCalled`, `NoChanges`, `ReadOnly`,
  `ChangesOnly`, `Changed`, `Proposed`, `ConcretePick`, `ShortSearch`, `QuietFirst`, `SaysPick`, `Joined`, `HandedOff`,
  `ReplySays`, `ReplyNot`, `PlainReply`. Each returns one plain sentence when it fails.
- **The dry-run layer** (`hub/golden/dryrun.py`) stands in the three doors through which Ask can change
  anything. Reads go through. A change that passes Hub's checks is recorded and answered as if it worked;
  one that fails them is refused as it would be live. Later reads see the recorded changes laid over the
  real state, so a second turn knows what the first one started. Undo records, and proposals that wait
  for the owner's tap (a note to save, a playlist to make), go to a scratch database, so a dry run leaves
  nothing on the Home changes, Playlists or Notes pages; proposals are recorded as `to: 'proposal'`.
  A test fails if the catalog gains a control or hand-off the layer doesn't cover.
- **Givens.** A scenario may start with recipes of its own (`given`, made-up, checked like any other). They
  live in the dry run's scratch database, so the owner's real recipes are neither used nor touched.
- **Live writes** (owner, 2026-11-13) are for light and music scenarios only (`harmless`), only from the
  command line with `--live-writes`, and only after the owner types "yes" at the prompt. Their Home
  Assistant changes go out after the same checks and are recorded in Hub's change log; right after the
  scenario's checks the runner undoes each one, and a change it couldn't put back fails the scenario.
  Music Assistant edits, Kitchen, notes, recipes and playlists stay held back even then.

## Two modes

| | Offline | Live |
| --- | --- | --- |
| Model | scripted, from each scenario's `script` | the real one, on Ask's zero-retention route |
| Data | a made-up household (`tests/golden_world.py`) | the owner's real Hub data, read only |
| Writes | dry run | dry run (lights and music live on request, undone after) |
| When | every `pytest` run and CI | on demand: Settings → Golden tests, or the command line |
| Cost | none | at most $0.50 a run and $5 a month (owner, 2026-11-13); recorded as "golden" in the engine's runs |
| Results | the test report | `golden/runs/*.json` in Hub's private data folder; a summary on Insights |

Offline, the checks test Hub's side: what a tool does with a given call, what it would change, what it
answers. Each seed failure also has a "known bad" script in `tests/test_golden.py` that replays what went
wrong, to prove the checks catch it. Live, the same checks test the model's choices too.

A live run stops before a turn that could take it past the cap (it reserves the costliest turn so far), and
after 80 model calls in any case. Skipped scenarios are listed as skipped.

### Running it

- **Offline:** `pytest tests/test_golden.py`.
- **From Settings:** Golden tests → Run. It starts `python -m hub.golden live` in its own process, so the
  dry-run layer never stands in front of the owner's own chats.
- **From the command line** (Termius on the Mac mini, with the live Hub's environment):

  ```sh
  HUB_DATA_DIR=<the live Hub's data folder> python -m hub.golden live
  ```

  `--only <id> …` runs some scenarios, `--cap 0.20` lowers the cap, `list` shows them all.

## The seed scenarios

| Scenario | Guards against |
| --- | --- |
| `music-softly-in-one-room` | the whole sentence sent as a search (the music free-text bug); loud start; other rooms |
| `music-as-well-joins` | "as well" starting a second stream instead of joining speakers |
| `remember-is-a-note-not-a-playlist` | a "remember …" sentence turned into a playlist instead of a note for the owner's tap |
| `lights-room-off`, `lights-dim-one-lamp` | lights in other rooms, the wrong service |
| `what-needs-me-today`, `calendar-free-tomorrow` | a read that changes something |
| `grocery-goes-to-kitchen` | a grocery ask not passed to Kitchen word for word, or passed twice |
| `gmail-filing-is-not-here` | claiming to file mail Ask can't file |
| `recipe-is-proposed-not-run`, `recipe-runs-by-name` | a recipe that runs or saves without a tap, or one that touches other speakers |

Both already expect the ask-zones behaviour: "remember …" proposes a note for the owner's tap, and Hub
itself turns a second play for "as well" into a join (`tests/test_golden.py` replays the old model mistake
and checks Hub's guard). New capabilities add their own.

## Writing a scenario

1. Write the request the way a person types it, with aliases for anything in the house.
2. List what must be true: the tool and its key arguments, what may change (and nothing else), what the
   reply must and mustn't say. Prefer outcome checks (what would change) over wording checks.
3. Write the `script`: the steps a well-behaved model takes, ending with `Say(...)`.
4. If it comes from a real failure, set `seed` and add a known-bad replay to `tests/test_golden.py`.
5. Run `pytest tests/test_golden.py`.

## Applying it to another project

The pattern needs four things, whatever the project:

1. **One entry point** that runs a request the way production does (here `agent.build()` plus
   `agent.settings()`, shared with the `/ask` route so the two can't drift apart).
2. **A dry-run layer at the doors**, not around the model: the few functions where a change leaves the
   program. It runs the real validation, records the change, and answers as if it worked.
3. **Scenarios as data** with fictional wording, aliases for private names, checks on outcomes, and a
   scripted model for offline runs.
4. **Two modes**: offline in the normal suite, live on demand with a hard cost cap, private results and a
   record of what it cost.

For Kitchen that would be: its message entry point, a dry run at the cart and order doors, and scenarios
like "what can I make with what's in the fridge?" or "add the usual milk".
