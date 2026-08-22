# Windows packaging

Normally you don't need any of this - push a version tag (see the main
README's "Releasing a new version" section) and GitHub Actions builds
`VW3ALanguageUpdater-Setup-<version>.exe` for you automatically.

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

pyinstaller --noconfirm --windowed --name VW3ALanguageUpdater --add-data "README.md;." vw3a_lang_updater.py
```

That produces `dist\VW3ALanguageUpdater\VW3ALanguageUpdater.exe` - you
can run it directly to sanity-check it before building the installer.

Then compile the installer with [Inno Setup](https://jrsoftware.org/isinfo.php)
(free): install it, then either open `packaging\windows\installer.iss`
in the Inno Setup IDE and click Compile, or from a shell with Inno
Setup's `ISCC.exe` on PATH:

```powershell
ISCC.exe packaging\windows\installer.iss
```

The finished installer lands in `installer_output\`.
