# DFS Optimizer

A Windows desktop lineup optimizer for DraftKings NFL, MLB, NBA, NHL, and WNBA slates.

## Download the Windows app

1. Open the repository's [latest release](../../releases/latest).
2. Download `DFS-Optimizer.exe` and `DFS-Optimizer.exe.sha256`.
3. Optionally verify the download from PowerShell:

   ```powershell
   Get-FileHash .\DFS-Optimizer.exe -Algorithm SHA256
   ```

   The displayed hash should match the value in `DFS-Optimizer.exe.sha256`.

4. Double-click `DFS-Optimizer.exe`.

The release executable is self-contained; Python does not need to be installed. The app is currently unsigned, so Windows may identify it as being from an unknown publisher. Only use binaries downloaded from this repository's Releases page.

## Run from source

Python 3.12 is recommended.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

On Windows, `Launch_DFS.bat` also starts the app after its dependencies have been installed.

## Results & Learning

1. Save the lineups you plan to enter, then choose **Export Saved CSV** on the Showdown or Classic tab.
2. After the contest, download the DraftKings contest standings or contest-history CSV.
3. Open **Results & Learning** and choose **Import DraftKings Results**.

The app matches result rosters to exact lineups it previously exported. The report includes net return, ROI, cash rate, finish percentile, projection error, and guarded breakdowns by salary use, ownership, construction, and context adjustment. Small samples are clearly labeled and are not used to automatically change optimizer strategy.

Export history and imported results stay on the computer. They are stored in the app's local `history` folder and are not uploaded to this repository.

## Portfolio & Exposure

The optimizer can shape the complete set of generated lineups, not just each lineup in isolation:

- set minimum and maximum player exposure, including separate Showdown Captain limits;
- require a minimum number of unique players between lineups;
- cap the percentage of lineups containing a team or game;
- create selected-player groups that require at least one player or prevent players from appearing together;
- balance ownership concentration and duplication risk across the portfolio; and
- review a portfolio compliance summary before exporting to DraftKings.

The app generates a larger candidate pool and selects a compliant portfolio from it. If aggressive settings cannot all be satisfied, it returns the feasible lineups it found and clearly reports any relaxed uniqueness or minimum-exposure shortfall.

## NFL SIM Edge

NFL Classic builds can use the **NFL SIM Edge** option. The app first removes inactive and low-depth players from the automatic field pool while preserving manual locks. It then:

- creates only complete, near-cap field lineups with realistic QB-stack, bring-back, and FLEX construction;
- simulates correlated game, team, passing, rushing, and player outcomes;
- ranks every candidate against the same representative field in each scenario; and
- selects a 150-lineup portfolio that covers different top-one-percent outcomes while respecting exposure rules.

The Classic results table shows a slate-relative SIM Edge grade. Its tooltip includes top-one-percent rate, representative win rate, cash rate, simulated ceiling, and duplication risk. These metrics are decision aids based on the loaded projections and assumptions, not guarantees of contest results.

## NFL Game-Day Check and Vegas lines

NFL salary files automatically receive current player availability, injury status, practice participation, roster status, news notes, and depth-chart roles from Sleeper. The status strip reports how many salary-file players matched, when the check ran, and whether anything changed. **Game-Day Check** refreshes the data on demand, and a stale check is refreshed before lineup generation.

- Confirmed out, inactive, injured-reserve, suspended, and practice-squad players are automatically removed unless the user locked them.
- A locked unavailable player is never silently removed; lineup generation stops and names the conflict.
- Questionable and doubtful players remain available, with their uncertainty reflected in the NFL role adjustment.
- The Status and Role columns expose starter/backup depth, practice participation, the source, and freshness details.

The **Vegas ITT** column displays each team's implied point total rather than an opaque score. Open **Live Data Settings** to save a personal [The Odds API](https://the-odds-api.com/) key. The app requests consensus US spreads and totals, matches them to the DraftKings games, and uses a small capped projection adjustment. The key stays in the current Windows user's local settings and is not placed in exports, logs, or the repository. If no key is configured—or no NFL lines have been posted—the status strip explains that instead of displaying a misleading zero.

NFL Showdown generation uses those same live inputs without applying the Classic low-depth pool filter. Strategic Showdown builds softly favor favorite-heavy 4-2 and plausible 5-1 constructions, passing correlations in higher-total or trailing-team scripts, and RB/K/DST combinations in lower-total favorite scripts. When a confirmed starter is unavailable, the next active depth-chart player receives a small, reversible opportunity adjustment; manual locks and exposure limits remain hard rules.

## Create a release

The `Windows Release` GitHub Actions workflow supports two modes:

- **Manual build:** Open **Actions → Windows Release → Run workflow**. When it finishes, download the Windows artifact from the workflow run.
- **Published release:** Push a version tag such as `v1.0.0`. The workflow runs the tests, builds the executable, calculates its SHA-256 checksum, and publishes both files to GitHub Releases.

Example release commands:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Create release tags from a reviewed commit on `main` so the published executable matches the supported source version.

## Tests

```powershell
python -m unittest discover -v
```
