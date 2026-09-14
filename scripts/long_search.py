"""Resume a desktop-created NFL candidate library without opening the GUI."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from candidate_library import metadata,run_search
from build_snapshots import load_snapshot

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library');parser.add_argument('--snapshot',help='Required for a new library')
    parser.add_argument('--hours',type=float,default=1)
    parser.add_argument('--candidates',type=int,default=20000,help='Total saved candidate target (1–100000); existing candidates count toward it')
    args=parser.parse_args()
    if not 0 < args.hours <= 12:parser.error('Hours must be greater than zero and at most 12')
    if not 1 <= args.candidates <= 100000:parser.error('Candidates must be between 1 and 100000')
    snapshot=load_snapshot(args.snapshot) if args.snapshot else metadata(args.library)['snapshot']
    try:run_search(args.library,snapshot,seconds=args.hours*3600,candidate_limit=args.candidates,progress=lambda text:print(text,flush=True))
    except KeyboardInterrupt:print('Stopped. Completed batches remain saved; resume with the same command.')

if __name__=='__main__':main()
