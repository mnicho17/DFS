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

## Slate Readiness says Review or Blocked

- Open the report and follow the **Next step** beside each item.
- **Blocked** identifies a hard preparation problem such as missing roster eligibility, poor projection coverage, or a locked unavailable player.
- **Review** identifies uncertainty or an optional gap such as stale news, questionable players, deep backups, incomplete ownership, no posted Vegas lines, or no generated portfolio yet.
- Generate lineups and reopen the report to add salary and contest-preset fit checks.
- The score is a preflight summary, not a prediction of lineup results, and the audit never changes the slate.

## Generation is slow

- After the build completes, choose **Settings > Copy Last Build Report**. It identifies whether Generate, SIM, or Select consumed the most time and includes the candidate counts and active rules needed to reproduce the issue.
- Start with fewer requested lineups while testing settings.
- Remove impossible minimum exposures or conflicting player groups.
- Relax very high uniqueness or very low team/game caps.
- For NFL Classic, Strategic, Balanced, Contrarian, and Chalk use the compact starter/rotation pool. Randomized with SIM Edge off intentionally uses the broader pool and can take longer.
- NFL SIM Edge does additional field and outcome simulation, so it normally takes longer than a projection-only build.
- Use **Settings > Build History…** to compare the same slate and settings across app releases.
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

## A standings file did not count as a complete field

- The file must contain at least 25 entry rows and about 95% of the advertised contest field.
- A personal contest-history file can update your own outcomes without representing the opponent field.
- Put **Single Entry**, **3-Max**, **20-Max**, or **150-Max** in the contest name or CSV filename if you want the field associated with that SIM preset.
- Learned preset tuning also needs three complete fields, 1,000 entries, and at least 70% player metadata coverage. Until then, the selected baseline preset remains active.

## A large standings import looks slow

- Complete contests can contain hundreds of thousands of entry rows and may take about a minute to inspect and summarize.
- Results & Learning remains usable enough to show progress while the import runs. Choose **Cancel Import** to stop after the current batch; a partially read file is not saved.
- The app saves compact field summaries instead of a local record for every opponent entry.

## Matching salaries were not attached

- Select the DraftKings salary CSV from the exact historical slate represented by the latest imported complete NFL field.
- The app requires at least 70% of field players to match before replacing the field summary with salary and construction details.
- A rejected or cancelled attachment leaves the existing field summary unchanged.

## Real Field vs latest NFL SIM is missing

- Import a complete NFL Classic field associated with a named preset.
- Generate and export an NFL Classic SIM build using that same preset.
- Reopen or refresh Results & Learning. The comparison is report-only until the complete-field learning guardrails are satisfied.

## Where local data is stored

Choose **Results & Learning**, then **Open Local History Folder**. Exports, imported results, slate snapshots, and build diagnostics remain on the computer.
