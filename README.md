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
