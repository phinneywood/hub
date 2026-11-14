"""Live checks from the command line.

  python -m hub.live list                           the checks and what each may do
  python -m hub.live run [--trigger shell] [--only ID ...] [--skip ID ...] [--no-spend] [--with-undo] [--gate]
  python -m hub.live failing                        what fails now, and since when

A run reads the real system, so HUB_DATA_DIR must point at the live Hub's data folder. `--no-spend` leaves
out the checks that call the model. `--with-undo` allows the one real change (a named light, put straight
back), and only if the owner has switched it on in the private settings. `--skip hub.pages` is for a version
that isn't running yet.

`--gate` is for a deployer: exit 1 when a check that passed the last time it ran fails now, so a new version
that breaks something is stopped, and something that was already down (Kitchen switched off) doesn't block the
fix for it. `--gate-all` exits 1 on any failure.
"""
import argparse
import sys

from . import checks, runner

MARK = {'pass': '✓', 'fail': '✗', 'skip': '–'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m hub.live', description='Live checks of the running Hub.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('failing')
    run = sub.add_parser('run')
    run.add_argument('--trigger', choices=runner.TRIGGERS, default='shell')
    run.add_argument('--only', nargs='*', default=())
    run.add_argument('--skip', nargs='*', default=())
    run.add_argument('--no-spend', action='store_true')
    run.add_argument('--with-undo', action='store_true')
    run.add_argument('--gate', action='store_true')
    run.add_argument('--gate-all', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'list':
        for c in checks.CHECKS:
            print(f'{c.id}  [{c.effect}]\n    {c.about}')
        return 0
    if args.command == 'failing':
        rows = runner.failing()
        for r in rows:
            print(f'✗ {r["id"]}: {r["words"]}\n    failing since {r["since"]}' + (f', last passed {r["last_pass"]}' if r['last_pass'] else ''))
        print('Nothing is failing.' if not rows else f'{len(rows)} failing.')
        return 0
    unknown = [i for i in (*args.only, *args.skip) if not checks.by_id(i)]
    if unknown:
        print('No such check: ' + ', '.join(unknown), file=sys.stderr)
        return 2
    passing = runner.passing()
    try:
        result = runner.run(args.trigger, only=args.only, skip=args.skip,
                            effects=runner.allowed(with_spend=not args.no_spend, with_undo=args.with_undo))
    except runner.Busy as error:
        print(error, file=sys.stderr)
        return 3
    for r in result['results']:
        print(f'{MARK[r["status"]]} {r["id"]}  {r["ms"]} ms\n    {r["words"]}')
    print(f'{result["passed"]} passed, {result["failed"]} failed, {result["skipped"]} skipped.')
    broke = [r['id'] for r in result['results'] if r['status'] == 'fail' and r['id'] in passing]
    if broke:
        print('Passed last time, fails now: ' + ', '.join(broke))
    return 1 if (args.gate and broke) or (args.gate_all and result['failed']) else 0


if __name__ == '__main__':
    sys.exit(main())
