# Home control (design, 2026-11-11)

Status: steps 1 and 2 (read, then lights, power and music) built; step 5 (playlists) built on a branch; the rest proposed. Owner decisions on 2026-11-11:

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
  A name that is both a room and a device means the room (owner, 2026-11-13): "turn off the living room"
  turns off every light there, even one that is itself called "Living room", and "play in the living room"
  uses that room's speaker. A device is reached by a name no room has, or by its entity id.
  The owner can set the "Change lights, plugs, scenes and speakers" action to Suggest only on the Engine
  page, and then Ask changes nothing.
- **"As well" means in sync** (2026-11-13). "Play it in the kitchen as well", "there too" or "in both" joins
  that speaker to the group already playing, never a second stream. Hub checks the owner's typed message
  itself (the model never supplies it), so a `play_media` call for such a message becomes a join; with two
  different rooms playing, Ask asks which one. A joining speaker that was silent starts at the playing
  room's volume. Each joined speaker is its own row in the change record: Undo takes it out of the group
  and puts its volume back, and leaves the first room playing.
- **Speaker zones** (2026-11-13). Speakers in one space (an open-plan room) can be a zone. A zone comes from
  a note the owner saved with a tap (see "Remember by tap" in docs/engine.md), which keeps the speakers beside
  the note, or from the private `household.json` → `home.speaker_zones` (a list of zones, each a list of
  speaker entity ids or names). Playing in one room of a zone plays in all of them, joined and at the same
  volume, in one change with one Undo, unless the owner says "only here". Zones never bring in a speaker
  outside the zone: a speaker grouped with others outside it still plays alone or asks first, as before.
  `music_now` tells the model each speaker's zone mates. Removing the note removes its zone. For security devices Ask will show an approval card instead
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

**How Hub connects** (built in step 5, `hub/tools/house/music.py`):

- `POST <music_url>/api` with `Authorization: Bearer <key>` and the body
  `{"message_id", "command", "args"}`; the answer is the bare result. `music_url` is the private
  `household.json` setting the links already use, so the key travels over the Tailscale HTTPS route to
  the Green, never over plain HTTP if the owner sets the https address.
- **The key** belongs to a separate, non-admin Music Assistant user made for Hub (owner, 2026-11-12), so
  it can't change Music Assistant's settings, providers or users. Hub reads it from 1Password
  (`op://Hub/Music Assistant/credential`) with the same read-only service account as the model key
  (`model_key.secret`). It is never logged, shown, or put in a URL, plist or file. Music Assistant's
  long-lived keys last 365 days and don't renew.
- **Only these commands leave Hub** (`music.READS`, `music.WRITES`): `music/playlists/library_items`,
  `music/playlists/playlist_tracks`, `music/search`, `music/recently_played_items`, `auth/tokens`,
  `music/playlists/create_playlist`, `music/playlists/add_playlist_tracks`,
  `music/playlists/remove_playlist_tracks` (positions count from 1) and `music/library/remove_item`.
- **A non-admin key has no rename or reorder** (`music/playlists/update` and `music/playlists/remove`
  are admin-only, and no move command exists). Hub renames by copying into a playlist with the new name
  and deleting the old one only once the copy is whole, and moves a song by removing the tail of the
  list and adding it back in the new order.
- **Adds and removes run in the background** in Music Assistant, and a refusal (for example "not
  editable") only shows inside that background task. Hub reads the playlist back for up to 8 seconds and
  records the edit as not confirmed unless the song count matches.
- **Errors over `/api`** are plain HTTP statuses: 401/403 (key refused) and 400 (refused) mean nothing
  changed; other failures and timeouts are recorded as unknown.

**Reminders on Today** (from `music_items`, no new schedule):

- The Apple Music sign-in lasts about 180 days, and Music Assistant doesn't report when it ends (its
  setup keeps the sign-in time privately and no command returns it). Hub counts from the sign-in date in
  `household.json` → `home.apple_music_signed_in` (`YYYY-MM-DD`) and shows a line from 30 days before.
- Hub's own key: from `auth/tokens`, the key named `Hub`, asked at most twice a day. The field names of
  that answer weren't checked against a live server; if they differ, the line just never shows.

**Checked in the Music Assistant 2.10.9 source but not yet live:** the shape of `auth/tokens`, and what
a failed background task looks like (Hub doesn't depend on it).

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
5. **Playlists by API.** Built: the `music_library` lookup, the `edit_playlist` control
   (create, add, remove, move, rename; add only for Apple Music), the Playlists page with Undo and the
   delete confirmation, making a playlist only on the owner's tap there (2026-11-13: Ask had made one named
   with the owner's sentence; now create, rename and delete need the owner's message to ask for a playlist,
   checked in code, and a name that reads like a sentence is refused), playing a library playlist by its exact name, and "Needs you" lines a month
   before the Apple Music sign-in or Hub's key run out. Trust: "Edit playlists" on the Engine page.
6. **Thermostat**, once its Home Assistant setup is finished and reads are checked.
7. **Apple Music edits through Chrome**, only if the owner wants them.

Tests use made-up devices and a fake Home Assistant. Nothing is switched on or off to check a build.
The first live check of each step is the owner trying it.

## Owner setup

The HA broker already has access, so steps 1 to 4 and 6 need nothing more.

1. **For the links:** add the Home Assistant and Music Assistant addresses to `household.json` in Hub's
   private data folder, under `"home": {"assistant_url": "…", "music_url": "…"}`.
2. **In Music Assistant** (for step 5): add a non-admin user for Hub, sign in as it, create a long-lived
   key named `Hub`, and save it in 1Password's `Hub` vault as item `Music Assistant`, field `credential`.
   Add `"apple_music_signed_in": "YYYY-MM-DD"` (the last Apple Music sign-in) to the same `home` section.
3. **In Chrome on the Mac mini** (only for step 7): sign in to music.apple.com once with the Apple
   Account that holds the library.
