"""python -m archive index | serve | curate | apply | status (see __init__.py)."""
import argparse
import json
import sys

from . import __doc__ as about


def main(argv=None):
    parser = argparse.ArgumentParser(prog='archive', description=about.splitlines()[0])
    parser.add_argument('do', choices=('index', 'serve', 'curate', 'apply', 'status'))
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


if __name__ == '__main__':
    sys.exit(main())
