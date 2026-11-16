"""Hub Archive: the household's photos, documents and ChatGPT history, indexed on this Mac and searchable from Hub.

One program, run from its own copy of Python with osxphotos (deploy/install-archive). It is the only thing on the Mac
given Full Disk Access: Hub never opens the Photos library, the documents or the export. It reads them and never
changes them; what it derives (the index, previews, the curator's choices) stays in one owner-only folder.

  index     read the sources and bring the index up to date (index.py; nightly, and by hand)
  serve     answer Hub over a Unix socket in that folder, on fixed read-only routes (serve.py)
  curate    choose each month's best photos (curator.py; on the 1st)
  apply     act on the marks made in Hub: albums and prints (curator.py)
  status    print what the index holds, as counts

Nothing it reads or derives goes to a model or leaves this Mac.
"""
