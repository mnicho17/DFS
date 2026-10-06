"""Explicit offline preparation from a validated saved build snapshot."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_snapshots import validate_snapshot
from showdown_library import prepare, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Saved input-snapshot.json (required for preparation)')
    parser.add_argument('--output', type=Path, required=True, help='New or resumable SQLite library')
    parser.add_argument('--hours', type=float, default=1)
    parser.add_argument('--max-candidates', type=int, default=5_000_000)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--locked-captains',action='store_true',help='Prepare only Captains locked in the snapshot')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(args.output), indent=2)); return
    if args.snapshot is None:
        parser.error('--snapshot is required for preparation')
    snapshot = validate_snapshot(json.loads(args.snapshot.read_text(encoding='utf-8')))
    recipe = snapshot['inputs']['recipe']
    if recipe['contest_kind'] != 'showdown':
        parser.error('Only NFL Showdown preparation is supported')
    from portfolio_rules import player_key
    scope=None
    if args.locked_captains:
        scope=[player_key(p) for p in snapshot['inputs']['players'] if p.get('LockCpt')]
        if not scope:parser.error('The snapshot has no Captain locks')
    last = [0]
    def progress(value):
        if value['checked']-last[0] >= 100000 or value['complete']:
            print(json.dumps(value), flush=True); last[0] = value['checked']
    result = prepare(args.output, snapshot['inputs']['players'],
                     salary_cap=recipe.get('salary_cap', 50000), seconds=args.hours*3600,
                     max_candidates=args.max_candidates, progress=progress,captain_keys=scope)
    print(json.dumps(result, indent=2))
    if not result['complete']:
        print('Coverage is partial. Run the same command again to resume; no full-coverage claim is made.')


if __name__ == '__main__':
    main()
