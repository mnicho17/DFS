"""Prepare a new server slate with the same code used by the desktop app."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nfl_pipeline import prepare_nfl_slate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_path')
    parser.add_argument('--output', required=True, help='A new output directory; existing directories are never overwritten')
    parser.add_argument('--mode', choices=['classic', 'showdown'], default='classic')
    parser.add_argument('--ownership-sims', type=int, default=1000)
    parser.add_argument('--template-sim', action='store_true', help='Match the desktop Showdown template option')
    parser.add_argument('--parquet', action='store_true', help='Also write server Parquet files; requires pandas and pyarrow')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error('Choose a new output directory to preserve prior snapshots')
    if args.parquet:
        import pandas as pd
        import pyarrow
    value = prepare_nfl_slate(args.csv_path, mode=args.mode,
                              ownership_sims=args.ownership_sims, template_sim=args.template_sim)
    output.mkdir(parents=True, exist_ok=False)
    for key, name in [('players', 'enriched_players'), ('role_pool', 'role_pool'),
                      ('summary', 'enrichment_summary'), ('preparation', 'preparation')]:
        (output / (name + '.json')).write_text(json.dumps(value[key], indent=2, allow_nan=False), encoding='utf-8')
        if args.parquet and key in {'players', 'role_pool'}:
            rows = [{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in p.items()} for p in value[key]]
            pd.DataFrame(rows).to_parquet(output / (name + '.parquet'), index=False)
    print(json.dumps(value['summary'], indent=2))
    print(f"Prepared {len(value['role_pool'])} players with ownership under {output}")


if __name__ == '__main__':
    main()
