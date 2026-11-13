"""Golden tests from the command line.

  python -m hub.golden list                       the scenarios
  python -m hub.golden live [--only ID ...] [--cap 0.50] [--live-writes]

Offline runs (scripted model, made-up household) are part of the test suite: pytest tests/test_golden.py.

A live run reads the live Hub's data, so HUB_DATA_DIR must point at it. Writes are a dry run. --live-writes
lets the light and music scenarios (marked harmless) change the house for real, only after the owner types "yes"
at the prompt, and undoes each one right after its checks.
"""
import argparse
import os
import sys

from . import runner, scenarios


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m hub.golden', description='Golden tests for Ask Hub.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    live = sub.add_parser('live')
    live.add_argument('--only', nargs='*', default=())
    live.add_argument('--cap', type=float, default=runner.RUN_COST_CAP)
    live.add_argument('--live-writes', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'list':
        for s in scenarios.SCENARIOS:
            print(f'{s.id}{"  [harmless]" if s.harmless else ""}\n    {s.about}')
        return 0
    if not os.environ.get('HUB_DATA_DIR'):
        print('Set HUB_DATA_DIR to the live Hub’s data folder, or start the run from Settings → Golden tests.')
        return 2
    unknown = set(args.only) - {s.id for s in scenarios.SCENARIOS}
    if unknown:
        print('No such scenario: ' + ', '.join(sorted(unknown)))
        return 2
    if args.live_writes:
        harmless = [s.id for s in scenarios.SCENARIOS if s.harmless and (not args.only or s.id in args.only)]
        if not sys.stdin.isatty():
            print('--live-writes needs the owner at a terminal.')
            return 2
        print('These light and music scenarios will really change the house, and each is undone right after its checks: '
              + (', '.join(harmless) or 'none') + '. Everything else is a dry run.')
        if input('Type yes to go on: ').strip().lower() != 'yes':
            print('Nothing ran.')
            return 1
    try:
        result = runner.live(args.only, cap=args.cap, writes=args.live_writes)
    except runner.Stopped as error:
        print(error)
        return 1
    for row in result['scenarios']:
        print(f'{row["status"]:8} {row["id"]}' + ''.join(f'\n    {f["check"]}: {f["why"]}' for f in row['failures']))
    print(f'Cost ${result["cost"]:.4f} of a ${result["cap"]:.2f} cap, {result["calls"]} model calls.'
          + (f' {result["stopped"]}' if result['stopped'] else ''))
    return 0 if all(r['status'] == 'pass' for r in result['scenarios']) else 1


if __name__ == '__main__':
    sys.exit(main())
