"""Resume a desktop-created NFL candidate library without opening the GUI."""
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from candidate_library import metadata,run_search
from build_snapshots import load_snapshot

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library');parser.add_argument('--snapshot',help='Required for a new library')
    parser.add_argument('--hours',type=float,default=1)
    parser.add_argument('--candidates',type=int,default=20000,help='Total saved candidate target (1–100000); existing candidates count toward it')
    parser.add_argument('--prepare-scenarios',action='store_true',help='Use part of the same time budget to prepare reusable Deep scenarios')
    args=parser.parse_args()
    if not 0 < args.hours <= 12:parser.error('Hours must be greater than zero and at most 12')
    if not 1 <= args.candidates <= 100000:parser.error('Candidates must be between 1 and 100000')
    snapshot=load_snapshot(args.snapshot) if args.snapshot else metadata(args.library)['snapshot']
    from compute_settings import normalize_deep_settings
    budget=args.hours*3600;started=time.monotonic()
    options=normalize_deep_settings(snapshot['inputs']['recipe'].get('deep_compute'))
    reserve=min(budget*.25,options['minutes']*60) if args.prepare_scenarios else 0
    try:
        count=run_search(args.library,snapshot,seconds=budget-reserve,candidate_limit=args.candidates,progress=lambda text:print(text,flush=True))
        if args.prepare_scenarios and count:
            from scenario_preparation import prepare_scenarios
            print(prepare_scenarios(args.library,snapshot,seconds=max(0,budget-(time.monotonic()-started)),progress=lambda text:print(text,flush=True)))
    except KeyboardInterrupt:print('Stopped. Completed batches remain saved; resume with the same command.')

if __name__=='__main__':main()
