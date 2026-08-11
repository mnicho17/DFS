# DFS Optimizer troubleshooting

## Windows warns about an unknown publisher

The app is not code-signed. Download it only from this repository's Releases page. You can compare the executable's SHA-256 value with the supplied `.sha256` file before opening it.

## The player file will not load

- Download a fresh salary CSV directly from DraftKings.
- Confirm the selected sport matches the slate.
- Close the file in Excel before loading it again.
- Do not rename or remove DraftKings columns.

## Vegas lines are blank

- Open **Live Data Settings** and confirm a personal The Odds API key is saved.
- Choose **Game-Day Check** again.
- Read the live-data status message. It distinguishes a missing or rejected key from a slate where no lines are posted.
- Confirm the DraftKings game names match the teams returned by the odds provider.

## An NFL player looks active when news says otherwise

Choose **Game-Day Check** immediately before generating and again near lock. The app uses structured availability, practice, roster, and depth-chart data, but late-breaking news can lead the data source. A manually locked unavailable player stops generation instead of being silently removed.

## Generation is slow

- Start with fewer requested lineups while testing settings.
- Remove impossible minimum exposures or conflicting player groups.
- Relax very high uniqueness or very low team/game caps.
- NFL SIM Edge does additional field and outcome simulation, so it normally takes longer than a projection-only build.
- Use **Cancel** to keep completed lineups from the current build.

## Fewer lineups were returned than requested

Open **Portfolio Summary** and read the generation warning. The available player pool, locks, fades, exposures, uniqueness, team/game caps, and groups may not allow the requested count. The app returns the feasible portfolio it found rather than hiding the shortfall.

## DraftKings rejects the export

- Confirm the export came from the same slate as the contest.
- Confirm all required roster slots are filled.
- Do not add analysis columns to the DraftKings upload file.
- If the salary file changed, reload it and generate a fresh export.

## Historical results do not match

Results & Learning matches exact rosters previously exported by the app. A row may remain unmatched if the submitted lineup was edited after export, the result file omits its lineup, or its player names/IDs cannot be parsed.

## Where local data is stored

Choose **Results & Learning**, then **Open Local History Folder**. Exports, imported results, and slate snapshots remain on the computer.
