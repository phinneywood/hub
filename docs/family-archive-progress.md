# Family archive: progress note (delete at merge)

Branch `family-archive`. The working note for this branch; what stays is in `docs/app.md` (“Archive”), the
archive program's own modules (`deploy/archive`) and `deploy/install-archive`.

## Built

- **The archive program** (`deploy/archive`): `index` (photos with osxphotos; documents with PDFKit, Vision and
  textutil, fetching iCloud-only documents; the ChatGPT export, the group chat and attachments), `serve` (fixed
  read-only routes on a Unix socket, ids only), `curate` and `apply` (the photo curator, moved in from its own
  branch), `status`, and `photos-access` (run once at the Mac). Sharp photos: exported by Photos, 2 GB cache.
- **Hub** (`hub/app/archive.py`, `/app/api/archive...`): the archive's client, a search read without a model, live
  mail search, calendar history kept in Hub's database, the kids and the switches.
- **Ask** (`search`, `read` in `hub/app/ask/tools.py`): nothing about photos reaches the model or decides a second
  trip; mail and calendar results do; documents' and ChatGPT history's matching passages, masked, unless switched off
  (owner decision, 2026-11-16). A request that read any of it changes nothing in the house.
- **The app**: the Archive screen (`web/app/src/archive.tsx`), the full-screen viewer, the month's best (folded in
  from Photos), Ask's photo and result cards; the demo shows a made-up archive through the real service.

## Checked

- The whole suite, and the browser check at phone (evening and day) and laptop sizes.
- A run by hand on the real library, documents and export, and searches through the real service: counts, times and
  kinds of error only, reported in the project thread. Nothing about the real data is in this repository.
- Not yet: the archive as installed (its LaunchAgents and Full Disk Access), the yes to working Photos, and a sharp
  photo from iCloud. They need the owner at the Mac.

## Waiting on the owner

1. Merging (a squash merge, so the curator branch's own history stays out of main), then deleting the remote
   `photo-curator` branch.
2. Installing: `PYTHON=... deploy/install-archive`, then at the Mac Full Disk Access for `HubArchive` and
   `launchctl kickstart` of `hub-archive-access` (macOS asks whether the archive may work Photos), then the first
   index and the curator's catch-up (its start date is the owner's, kept out of the repository).
3. A backup entry for `~/Library/Application Support/HubArchive` (only `curator/` holds anything that can't be made
   again; the index, previews and sharp photos are rebuilt by the next run), if wanted.

## Where it stopped (2026-11-17, paused by the owner)

The branch is at a clean tree. Checked on what is committed: the whole suite in a venv built from the reviewed
wheelhouse (1,766 passed, 1 skipped: it needs Quartz, which only the archive's own Python has). Not run on these
commits: the browser check and the installed-package check.

**Done since the last note**

- Ask's `archive` tool: the month's best, on this day, what Ask may read, who counts as one of the kids. Hub
  answers each itself and the model's record is one fixed line. A dry run holds the switches and the kids back.
- The Archive has no tab or button of its own: it opens from Ask's cards and from System.
- The owner's own history of asking keeps each answer as they saw it (`app_ask.shown`), beside what later requests
  are told; Hub's sentence and the model's later words each get a line.
- Who the owner is comes from `hub/people.py` alone (its two commits are cherry-picked here; main holds the same
  content). While nobody is named yet, everyone sees Shared Library photos only and the landing says so.
- The landing says what is wrong in fixed sentences, and fits itself to the login.
- Photos is asked for nothing until `photos-access` has the owner's yes on record: the viewer shows the Mac's own
  picture and the curator's marks wait. The viewer asks for a full-size picture only after 600 ms on screen.
- On a phone: the shorter search placeholder, on this day's years in a row, toasts that wrap.
- Already there before: a document or conversation found on the loose retry is read by its loose passages.

**Left to do**

1. Merge `origin/main` (not started; main moved on by nine commits and overlaps this branch in about 26 files, ten
   with conflicts; rebuild the app after it rather than merging the built files by hand). Then use main's own
   pieces and words for "not yours" in the archive's mail and calendar, and check that nothing new on main reads
   `app_ask` rows into a model.
2. The search reader (`archive.understand`): open-ended and closed spans ("since 2024", "before 2020", "between
   2019 and 2021"), a single day with or without a year, decades, "3 months ago", weekends, "last christmas" as the
   newest year that has matches, more filler words ("say", "told", "mention"), "screenshots" as a kind. A test case
   and its sentence for each.
3. Nothing found: quote the search as typed, show the "some of them" line only when the loose retry found
   something, no empty sections, one quiet line of help. The same in Ask's answer.
4. Read every sentence the archive can show once more for plain words (the landing's were rewritten; `tools.py` and
   `archive.tsx` were not gone through).
5. `/status` in the archive's service gives the people and kids counts unscoped even with `shared=1`.
6. A test that after a photos turn `brain.earlier` holds the fixed line and none of Hub's photo sentence, while the
   person's own history shows it (the archive tool has this test; a photo search does not yet).
7. Docs: "Archive" and Ask's list of tools in `docs/app.md` still describe the rail tab and the search button; the
   four owner decisions with their date in `AGENTS.md`, `docs/engine.md` and the module docstrings; the Versions
   entry; the README table.
8. On the real archive: an incremental index and the service run by hand, with counts, timings and sizes only;
   search and picture timings at real scale; the screen measured by a script at three widths; Ask on real data
   (this needs the owner's word first: the repository's rule is no tests on real mail). A process run by hand for
   this needs one allowed login, or it measures Shared Library photos only.
9. Finish: the installed-package check, the browser check, and the steps for the owner at the Mac. Have him open
   one photo in the viewer while he is there, in case the service's first export asks separately.

**Left outside this checkout** (nothing deleted; deleting any of it is the owner's call)

- The index of the real library and its previews are in the archive's own folder and nowhere else (about 128 MB and
  2.3 GB; folder 0700, files 0600). No second index was found in temporary or scratch folders or the home folder.
- An earlier session's scratch folder (temporary, 0700): two copies of this branch's diff as patch files, its
  probe scripts and logs of its runs by hand (counts and timings), three Python environments (about 370 MB), the
  wheelhouse it built from, and screenshots of the made-up demo. Files that came from real runs are now 0600.
- The photo curator session's scratch folder (temporary, 0700): a trial month of previews from the real library
  (about 12 MB, now owner-only), two screenshots and two Python environments.
- A demo server from 2026-11-16 still runs from this checkout on a loopback port, on made-up data.
- A link in the temporary folder to one of those Python environments, used to run the suite from a short path.
