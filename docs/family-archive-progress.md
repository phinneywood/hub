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
