# Windows packaging

Normally you don't need any of this - push a version tag (see the main
README's "Releasing a new version" section) and GitHub Actions builds
`AltiKit-Setup-<version>.exe` for you automatically.

This doc is for building it locally instead (e.g. to test a change
before tagging a release). Run everything below **on Windows**
(PowerShell), from the repo root, with Python 3.11+ installed:

```powershell
python -m venv build_venv
build_venv\Scripts\Activate.ps1
pip install -r requirements.txt pyinstaller

# PLAYWRIGHT_BROWSERS_PATH=0 makes `playwright install` put the browser
# inside the playwright package itself instead of the OS cache dir, so
# PyInstaller's bundled playwright hook picks it up automatically and
# the resulting .exe doesn't need internet access just to find Chromium.
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
playwright install chromium

# Trim Chromium files with long, deeply-nested names (locale .pak files,
# ad-privacy/media-preload/isolated-web-app data, hyphenation dictionaries)
# that are irrelevant to headless automation - if your checkout path is
# long enough, they can exceed Windows' 260-char MAX_PATH and make Inno
# Setup fail to compile. Harmless to remove since this app only drives
# Chromium headlessly and never renders any of the features they back.
$pwDir = python -c "import playwright, os; print(os.path.dirname(playwright.__file__))"

Get-ChildItem -Path $pwDir -Recurse -Directory -Filter "locales" | ForEach-Object {
    Get-ChildItem -Path $_.FullName -Filter "*.pak" | Where-Object { $_.Name -ne "en-US.pak" } | Remove-Item -Force
}

$foldersToRemove = "PrivacySandboxAttestationsPreloaded", "MEIPreload", "IwaKeyDistribution", "hyphen-data"
foreach ($name in $foldersToRemove) {
    Get-ChildItem -Path $pwDir -Recurse -Directory -Filter $name | ForEach-Object {
        Remove-Item -Path $_.FullName -Recurse -Force
    }
}

# Each bundled browser's "resources\" dir (distinct from the top-level
# resources.pak file) holds optional feature-specific payloads (e.g. the
# reading-mode accessibility helper) - irrelevant here, safe to drop whole.
$localBrowsersDir = Join-Path $pwDir "driver\package\.local-browsers"
Get-ChildItem -Path $localBrowsersDir -Recurse -Directory -Filter "resources" | ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force
}

# Optional: write a version the app can report/compare against for its
# "Check for Updates" feature. Without this file, the app just reports
# itself as "dev" and skips the comparison. CI always writes a real one
# from the pushed tag.
"v0.0.0-local" | Out-File -Encoding ascii VERSION.txt

pyinstaller --noconfirm --windowed --name AltiKit --icon packaging\icon\icon.ico --add-data "README.md;." --add-data "HELP.md;." --add-data "VERSION.txt;." --add-data "packaging\icon\icon-256.png;." altikit.py
```

That produces `dist\AltiKit\AltiKit.exe` - you can run it directly to
sanity-check it before building the installer.

Then compile the installer with [Inno Setup](https://jrsoftware.org/isinfo.php)
(free): install it, then either open `packaging\windows\installer.iss`
in the Inno Setup IDE and click Compile, or from a shell with Inno
Setup's `ISCC.exe` on PATH:

```powershell
ISCC.exe packaging\windows\installer.iss
```

The finished installer lands in `installer_output\`.
