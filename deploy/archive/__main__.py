"""python -m archive index | serve | curate | apply | status (see __init__.py)."""
import argparse
import json
import sys

from . import __doc__ as about


def main(argv=None):
    parser = argparse.ArgumentParser(prog='archive', description=about.splitlines()[0])
    parser.add_argument('do', choices=('index', 'serve', 'curate', 'apply', 'status', 'photos-access'))
    parser.add_argument('--source', action='append', choices=('photos', 'documents', 'chats'),
                        help='index only this source (more than once for several)')
    parser.add_argument('--month', help='curate: this month, YYYY-MM')
    parser.add_argument('--since', help='curate: every month from this day, YYYY-MM-DD')
    args = parser.parse_args(argv)
    if args.do == 'index':
        from . import index
        index.run(args.source)
    elif args.do == 'serve':
        from . import serve
        serve.serve()
    elif args.do == 'photos-access':
        return photos_access()
    elif args.do in ('curate', 'apply'):
        from . import curator
        more = ['--month', args.month] if args.month else ['--since', args.since] if args.since else []
        return curator.main(['scan' if args.do == 'curate' else 'apply', *more])
    else:
        from . import search, store
        if not store.database().exists():
            print('Not indexed yet.')
            return 1
        conn = store.connect(read_only=True)
        print(json.dumps({'counts': search.counts(conn), 'runs': store.status(conn)}, indent=1))
    return 0


def photos_access():
    """Once, with the owner at the Mac: one harmless question to Photos, so macOS asks now, on the Mac's screen,
    whether the archive may work Photos (sharp pictures in the viewer, and the curator's albums), rather than on a
    night nobody is there to answer."""
    from . import macos
    try:
        print(json.dumps({'photos': 'yes', 'version': macos.hello_photos()}))
        return 0
    except Exception as error:  # noqa: BLE001 - said; the viewer and the curator ask again later
        print(json.dumps({'photos': type(error).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
