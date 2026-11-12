# Home control (design, 2026-11-11)

Status: steps 1 and 2 (read, then lights, power and music) built on a branch; the rest proposed. Owner decisions on 2026-11-11:

- Hub talks to Home Assistant directly for devices and playback, and to Music Assistant's own API
  for playlists. Only Apple Music edits that no API offers would go to a Claude Code turn driving the
  signed-in Chrome, like Kitchen.
- Hub may control lights, switches and plugs, speakers and music, the thermostat, and locks, the
  garage and the alarm.
- Playlists may be fully edited: create, add, remove, reorder, rename, delete.
- Hub reaches Home Assistant with administrator rights through the existing HA broker, which holds
  the owner's admin token. Hub holds no Home Assistant token of its own (owner, 2026-11-11).

## How a request flows

```
 owner in Ask or on the Home page
        │
        ▼
 Hub ── home actions (catalog) ──▶ HA broker ──▶ Home Assistant ──▶ lights, plugs, thermostat,
  │        allowlist, limits, undo   (holds the admin token)       locks, speakers
  │
  ├── music actions (catalog) ──────▶ Music Assistant API ──▶ search, queue, Music Assistant
  │                                    (its own key)           playlists, add to Apple playlists
  │
  └── "playlist" hand-off (later) ──▶ Claude Code + Chrome ──▶ Apple Music edits its API can't do
```

## Devices: Hub to Home Assistant

- **Through the HA broker.** The broker (home-ops `ha-maintenance/ha_broker.py`, run as a login
  LaunchAgent) already holds the owner's administrator token in Keychain and serves
  owner-only requests over a Unix socket, logging each one. Hub sends its Home Assistant requests
  there (`{"op": "request", ...}` and `{"op": "ws", ...}`), so there is one admin token, one holder
  and one log, as the connections plan wants. When the connections gateway arrives, Hub moves behind
  it without other changes. Hub finds the socket from a setting, never a path written into Git.
  Home Assistant's logbook shows the owner's name for changes Hub makes; Hub's own change log says
  they came from Hub. If the broker isn't running, Home control says so and changes nothing.
- **Hub enforces what may be touched.** The broker passes on any Home Assistant request, so the
  allowlist in Hub's code is the fence: a fixed set of (domain, action) pairs with checked arguments,
  and no generic "call any service" path. Device names and states reach a model only as data.

  | Class | Domains | Actions | How it runs |
  | --- | --- | --- | --- |
  | Lights and power | `light`, `switch`, `fan`, `scene` | on, off, brightness, colour, activate scene | Right away on the owner's request; Undo puts back the previous state |
  | Speakers and music | `media_player` (Music Assistant and Sonos) | search, play, pause, next, volume, join and unjoin rooms | Right away; playback that starts from idle starts at about 15% |
  | Thermostat | `climate` | set temperature within fixed bounds, set mode | Right away, within bounds; Undo puts back the previous setting |
  | Security | `lock`, `cover` (garage only), `alarm_control_panel` | lock and unlock, open and close, arm and disarm | Only a tap on an approval card from a signed-in device. A typed message never acts alone |

  Not included: cameras, presence and location, automations, scripts, Home Assistant settings,
  restarts, and any service not in the table. Device names and states are data, never instructions.
- **Catalog entries.** A `Home` source in `hub/engine/catalog.py` with lookups (`home_status`: rooms,
  devices in the allowlisted domains and their states; `music_now`: what plays where) and actions per
  class. Each action has a preview, an undo where one exists, and a trust level on the Engine page.
- **Ask can act.** Ask's first doing tools are `home_control` (lights, plugs, fans, scenes) and
  `play_music` (play, pause, skip, volume, group). The model names a device, room or speaker from
  `home_status` and `music_now`; Hub resolves it to an entity it keeps and checks the action twice
  (`control.py`, then `broker.WRITES`). Who asked comes from the signed-in request, never from the model.
  The owner can set the "Change lights, plugs, scenes and speakers" action to Suggest only on the Engine
  page, and then Ask changes nothing. For security devices Ask will show an approval card instead
  (step 5). Device names and states reach Ask's model only over its zero-retention route.
- **No Home page of its own.** Home Assistant already has dashboards, so `/house` opens Home Assistant
  (its address is in the private settings file, `household.json` → `home.assistant_url`, with
  `home.music_url` for Music Assistant's player). Hub keeps only `/house/changes`: what it changed,
  with Undo.
- **Today.** A Home card with the lights and music in one line and one-tap buttons ("All lights off",
  "Pause"), a banner with Undo after a tap, and a "Needs you" line for the alarm going off or a jammed
  lock at any hour, and an unlocked door or open garage at night.
- **Record.** Every change is logged in Hub (who asked, from where, what changed, the state before),
  so Undo, for a day, can put it back.
- **Limits.** At most 30 device changes a minute; a change whose answer never came back is reported as
  not confirmed, never as done. Playback that starts from silence starts at 15%.

## Music: Music Assistant's own API

Playing goes through Home Assistant (`music_assistant.search`, `music_assistant.play_media`,
`media_player.*`). Playlists go straight to Music Assistant's own API on the Green (port 8095), with
its own login key, because Home Assistant's Music Assistant actions have no playlist calls.

What that API can do with playlists (checked in the Music Assistant 2.10.9 source):

| | Music Assistant playlist | Apple Music playlist |
| --- | --- | --- |
| Create | yes | no |
| Add tracks | yes | yes |
| Remove, reorder | yes | no (Apple's API doesn't allow it) |
| Rename, delete | yes | no |
| Plays on the speakers | yes | yes |
| Shows in the Music app on phones | no | yes |

So Hub does most of it by API:

- **Making a playlist** ("make a calm dinner playlist") creates a Music Assistant playlist from search
  results, plays it at once if asked, and allows full editing. It lives on the Green and can be played
  from Hub, Ask or Music Assistant, but not from the Music app on a phone.
- **Adding to an Apple Music playlist** ("add this song to Road Trip") works by API too, and shows up
  everywhere the owner's library does.
- **Everything else in Apple Music itself** (create, remove, reorder, rename, delete there) has no API.
  That part waits for the Chrome hand-off below, and is only built if the owner wants playlists to
  appear on phones.

## Apple Music edits: Claude Code with Chrome (later, optional)

Apple Music on the web can do the edits its API can't, in the owner's signed-in Chrome.

- **A second hand-off** in `catalog.HANDOFFS`, "playlist", built like the Kitchen one: Ask passes the
  owner's message word for word to a playlist assistant, a headless Claude Code turn with Claude in
  Chrome on music.apple.com.
- **Where it runs.** Inside the kitchen service, as a second brain with its own instructions, memory
  folder and tool list. Both brains drive the same Chrome, and one queue in one service keeps them
  from fighting over it.
- **What it may do.** Full playlist editing, as the owner chose: create, add, remove, reorder, rename
  and delete. Two guards:
  - Deleting a whole playlist shows a confirmation card and waits for a tap, because it can't be
    undone.
  - It never changes account, subscription, payment, family sharing or privacy settings, never types
    passwords or codes, and never buys anything.
- **Playing it.** The turn returns the playlist's tracks as data. Hub can then play them at once
  through Music Assistant's `play_media` with that list. A playlist made on the web also shows up in
  Music Assistant after its next library sync.
- **The rule this extends.** Like Kitchen, chat text for playlists goes to Claude Code on the owner's
  Claude plan, not a zero-retention route. AGENTS.md and the engine doc must record that exception
  before it is built.

## Order of work

1. **Read.** Built: the Home source, `home_status` and `music_now`, the Today card and "Needs you".
2. **Lights, power and music.** Built: `home_control` and `play_music` in Ask, Today buttons, Undo and
   the changes page. A proper result card with Undo inside the chat needs the Ask page rebuilt (Node);
   until then Ask's answer links to Home changes.
3. **Security devices** with approval cards (Lock from "Needs you").
4. **Automations.** Ask drafts one in plain words; nothing is created until the owner taps Create.
   Hub marks the ones it makes and only edits or deletes those; one that touches locks, the garage or
   the alarm is flagged on the card.
5. **Playlists by API.** Music Assistant playlists (create and full editing) and adding to Apple Music
   playlists, plus a "Needs you" reminder a month before the Apple Music sign-in expires.
6. **Thermostat**, once its Home Assistant setup is finished and reads are checked.
7. **Apple Music edits through Chrome**, only if the owner wants them.

Tests use made-up devices and a fake Home Assistant. Nothing is switched on or off to check a build.
The first live check of each step is the owner trying it.

## Owner setup

The HA broker already has access, so steps 1 to 4 and 6 need nothing more.

1. **For the links:** add the Home Assistant and Music Assistant addresses to `household.json` in Hub's
   private data folder, under `"home": {"assistant_url": "…", "music_url": "…"}`.
2. **In Music Assistant** (for step 5): create an API key in its settings and save it in 1Password's
   `Hub` vault as item `Music Assistant`, field `credential`.
3. **In Chrome on the Mac mini** (only for step 7): sign in to music.apple.com once with the Apple
   Account that holds the library.
