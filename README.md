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
