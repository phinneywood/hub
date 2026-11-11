# The dev Hub

A second Hub that runs this checkout's code on a private copy of live Hub's data, so the owner can
see a change working on real data before it is merged and deployed. The owner runs it; agents may
read `deploy/dev-hub status` and the log.

## How it is put together

| Part | What | Where |
| --- | --- | --- |
| Code | This checkout, auto-reloading on changes to `hub/` and `preschool/` | `.venv`, `uvicorn --reload` |
| Data | A copy of live Hub's data folder, databases through SQLite's backup | `~/Library/Application Support/HubDev/data` |
| Settings | Live Hub's service settings, with its own port and data folder, background jobs off and no deployer folder | Rendered by `setup` from live Hub's LaunchAgent |
| Service | A LaunchAgent loaded into the owner's login (it needs the login to read the model key). Its file lives in `HubDev/`, not `LaunchAgents/`, so nothing restarts it after a reboot | `<prefix>.hub-dev`, log `~/Library/Logs/hub-dev.log` |
| Way in | Listens on 127.0.0.1 only. Two Tailscale Serve entries in front, so Hub's login allowlist applies | `https://<machine>.<tailnet>.ts.net:8445/` for a normal browser, `http://<machine>:8446/` for the Claude app's Browser pane |

The Browser pane refuses the `ts.net` https address. It loads the short-name http address. On the
first visit it may show the page without styling; reload once. Plain-http Serve entries match by
machine name, so the bare `100.x` address gets "not found".

## Use

On the Mac, as the owner (Termius works: `launchctl bootstrap` reaches the login session):

```sh
deploy/dev-hub setup     # once: .venv, data copy, service file
deploy/dev-hub start     # service + Tailscale entries, then a status check
deploy/dev-hub status
deploy/dev-hub stop      # the data copy is kept
deploy/dev-hub refresh-data   # while stopped: set the copy aside, copy live data again
```

`status` should show the local and pane addresses answering 200, "Sign-in applies: yes" and
"Model key: found". After a reboot, run `start` again.

## What is real

The dev Hub reads and acts on the same accounts as live Hub. Gmail actions change the real mailbox;
Kitchen's Approve fills the real cart. It never touches live Hub's data folder, cannot ask the
deployer for anything, and runs no background jobs. Its model spend is recorded in the copy, so
live Hub's monthly limit does not see it.

## Trying Kitchen changes

Kitchen is a separate service in its own repository. To try a Kitchen change without touching live
Kitchen, run its dev copy from that checkout (`deploy/dev-kitchen setup`, then `start`): it has its own
copy of Kitchen's data, socket and brain session and sends no phone notifications. While that copy's
folder (`~/Library/Application Support/KitchenDev`) exists, `deploy/dev-hub setup` points the dev Hub
at it; `status` says which Kitchen is in use. After `setup`, run `stop` and `start` to switch. The dev
Kitchen still works in the same signed-in Chrome, so its cart changes and orders are real.

For screenshots and browser checks on made-up data, use `web/views/check/demo_server.py` instead.
