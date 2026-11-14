# The door: Home Assistant and Music Assistant inside Hub

Status: built (2026-11-14), not switched on. Code: `hub/door.py`, the Home and Music screens in
`hub/tools/house/pages.py`. Switching it on is the owner's: it changes Home Assistant's configuration and
Tailscale Serve (steps below).

## What the owner gets

Hub's main bar becomes **Ask · Today · Home · Music**. Home is the whole Home Assistant and Music is the whole Music
Assistant, each under Hub's bar, with no login of their own, on the phone and on a desktop. Insights moves to All
tools. Hub's own quick controls (the mini-player, the now-playing card, Home changes with Undo) stay as they are.

## How it works

```
 phone or laptop (on the tailnet)
   │  https://<machine>.<tailnet>.ts.net:<hub port>/house/assistant      Hub's page: its bar and one frame
   │  https://<machine>.<tailnet>.ts.net:<door port>/…                   what the frame loads
   ▼
 Tailscale Serve ── adds Tailscale-User-Login ──▶ the door (hub/door.py, 127.0.0.1:8781, inside Hub's process)
                                                    │  checks the login on every request and WebSocket
                                                    │  drops X-Forwarded-* and Tailscale's headers
                                                    ▼
                                                  Home Assistant (home network)
                                                    │  trusted_networks: this Mac's address is signed in as the owner
                                                    ▼
                                                  Music Assistant add-on (Home Assistant signs its users in: "ingress")
```

- **A separate address, not a path.** Home Assistant can't be served under `/something/`; its pages use addresses
  from the root. So it keeps a port of its own on the same machine name. Same name, other port is the same site to a
  browser, so storage and cookies work inside the frame, on iPhone too. Inside the installed Hub app a frame stays in
  the app; a link to the other port would open a browser sheet.
- **The door holds no credential.** It is a pass-through. What signs the owner in is Home Assistant's own
  `trusted_networks` provider, told to trust exactly this Mac's address and to sign it in as one user without
  asking. A sign-in made that way only works from that address and is refused over Home Assistant's cloud link.
- **So the door is the gate, and is strict.** It lets through only the logins listed for it (being allowed into Hub
  isn't enough), and never a request with no login, which is what a program on this Mac would send. It refuses
  anything another website makes the browser send, WebSockets from another origin, and a sign-in started for any
  site but itself. It listens on loopback only; Tailscale Serve is the one way in.
- **Music Assistant comes through Home Assistant.** Home Assistant already signs its users into add-ons, so Music is
  the add-on's panel at `/app/<add-on slug>`, through the same door. No Music Assistant login, token or key ever
  reaches a browser. Hub's own calls to Music Assistant (search, playlists) keep using Hub's key, server to server.
- **Framing.** Home Assistant forbids being framed by another address. The door takes that header off and says
  instead that only pages on this machine's own name may frame it. On Hub's side only the Home and Music screens
  may hold a frame, and only of the door's address; every other page still refuses frames.

## What this trusts, plainly

Any program running on this Mac can reach Home Assistant from this Mac's address, and with `trusted_networks` that
is enough to be signed in as the owner. Such a program can already ask the broker to do anything in Home Assistant,
so little is added, but it is a real widening and the owner's decision. Other machines on the home network gain
nothing: only this Mac's one address is trusted. Keep that address reserved on the router; if it changes, Home
Assistant shows its password page again (and the `door.sign_in` live check fails).

## Settings

Private `household.json` in Hub's data folder (placeholders here; nothing real is in Git):

```json
"home": {
  "ha_url": "https://<machine>.<tailnet>.ts.net:<door port>",
  "music_api": "http://<music assistant's address on the home network>:8095"
},
"door": {
  "upstream": "http://<home assistant's address on the home network>:8123",
  "logins": ["<the owner's Tailscale login>"],
  "music_path": "/app/<music assistant add-on slug>"
}
```

- `door.upstream` and `door.logins` switch the door on. Without both it refuses everything and nothing listens.
- `home.ha_url` is the address the frame loads: the Serve port that points at the door. It must be `https`.
- `door.music_path` adds the Music screen. The slug is in the address bar when Music Assistant is open inside Home
  Assistant.
- `home.music_api` is where Hub itself calls Music Assistant. Only needed once the separate Music Assistant port is
  closed; until then `home.music_url` serves for both.
- `HUB_DOOR_PORT` (environment, default 8781) is the loopback port the door listens on.

## Switching it on (owner; in this order)

1. **Reserve this Mac's address** on the router, so it never changes.
2. **Home Assistant**, in `configuration.yaml` (keep the `homeassistant` provider as the fallback; a mistake here
   can lock the login page, so keep a terminal or the Samba add-on at hand to undo it). `trusted_networks` must be
   first. This Mac's address must not be listed in `http: trusted_proxies`.

   ```yaml
   homeassistant:
     auth_providers:
       - type: trusted_networks
         trusted_networks:
           - <this Mac's address>/32
         trusted_users:
           <this Mac's address>/32: <the owner's user id, from Settings → People → Users>
         allow_bypass_login: true
       - type: homeassistant
   ```

   Check the configuration, then restart Home Assistant.
3. **Hub's private settings**: add the `door` section and `music_api` above. Deploy the Hub version that has the door.
4. **Tailscale Serve**: point Home Assistant's port at the door instead of straight at Home Assistant
   (`<door port>` is the port Home Assistant has on the tailnet today):

   ```sh
   tailscale serve --https=<door port> off
   tailscale serve --bg --https=<door port> http://127.0.0.1:8781
   ```

5. **Look**: open Hub, tap Home. Home Assistant should appear with no login. Tap Music. Then Settings → Checks → Run:
   `door.gate` and `door.sign_in` should pass.
6. Later, once Music works: close the separate Music Assistant port (`tailscale serve --https=<its port> off`).

To undo: point the Serve port back at Home Assistant, remove the `door` section, and take the `trusted_networks`
block out of `configuration.yaml`.

## Checked on a made-up Home Assistant, not yet on the real one

`tests/test_door.py` covers who passes, what is refused, what Home Assistant gets to see, WebSockets both ways, and
Hub's screens. The door was also run on loopback against a stand-in server: pages, a 300 KB upload, a never-ending
stream that ends when the browser leaves, a WebSocket echo. To confirm on the real system after step 5:

- Home Assistant opens with no click on the installed version (its source says it does).
- Music Assistant's panel loads and its in-browser player plays, through the two layers.
- A camera view streams, and the Home screen survives the phone sleeping and waking.
- The installed Hub app on the phone stays in the app.

If Music Assistant's panel misbehaves inside Home Assistant, the fallback is a second door straight to Music
Assistant (it works under a door, and takes a one-time sign-in token in the address). Not built.
