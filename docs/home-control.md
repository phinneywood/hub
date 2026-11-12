# Home control (design, 2026-11-11)

Status: step 1 (read) built on a branch, awaiting review; the rest proposed. Owner decisions on 2026-11-11:

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
- **Ask can act.** Ask gets its first doing tools: `home_control` and `play_music`. Arguments are an
  entity from `home_status` and an allowlisted action; anything else is refused before Home Assistant
  hears of it. Ask shows a result card ("Kitchen lights off · Undo"). For security devices it shows
  an approval card instead, and nothing happens until the owner taps it (the same signed-in-device
  rule as Deploy and Place order). Device names and states reach Ask's model only over its
  zero-retention route, as today.
- **A Home page** (`/house`, shown as "Home"): rooms with their lights and plugs as toggles, the
  thermostat, now playing per speaker with play, pause and volume, and the security devices with their
  state and tap-to-approve buttons. A card on Today summarises it, and flags an unlocked door or an
  open garage at night.
- **Record.** Every change is logged in Hub (who asked, from where, what changed, the state before),
  so Undo and the Home page can show recent changes.
- **Limits.** At most 30 device actions per minute from Ask; a failed or unknown result is reported
  as unknown, never as done.

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

1. **Read.** The Home source, `home_status` and `music_now` lookups, and a read-only Home page and
   Today card.
2. **Lights, power and music.** Actions with Undo, Ask's `home_control` and `play_music` tools and
   result cards, and toggles on the Home page.
3. **Playlists by API.** Music Assistant playlists (create and full editing) and adding to Apple
   Music playlists.
4. **Thermostat**, once its Home Assistant setup is finished and reads are checked.
5. **Security devices** with approval cards.
6. **Apple Music edits through Chrome**, only if the owner wants them.

Tests use made-up devices and a fake Home Assistant. Nothing is switched on or off to check a build.
The first live check of each step is the owner trying it.

## Owner setup

Nothing for steps 1, 2, 4 and 5: the HA broker already has access.

1. **In Music Assistant** (for step 3): create an API key in its settings and save it in 1Password's
   `Hub` vault as item `Music Assistant`, field `credential`.
2. **In Chrome on the Mac mini** (only for step 6): sign in to music.apple.com once with the Apple
   Account that holds the library.
