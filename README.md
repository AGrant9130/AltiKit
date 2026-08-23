# VW3A1111 / VW3A1121 Language File Updater

A small cross-platform GUI tool to check for, download, and install the
latest language files onto a Schneider Electric VW3A1111 or VW3A1121
Graphic Display Terminal.

It automates the manual procedure described in Schneider's own
`Readme_Languages_Update_for_VW3A1111.txt`: delete the `LANG` and
`KPCONF` folders on the keypad and copy over the new ones, capped at
the device's 10-language limit (plus fonts).

## Installing on Windows (no command line needed)

Grab the latest `VW3ALanguageUpdater-Setup-*.exe` from this repo's
[Releases page](../../releases), run it, and follow the installer -
it adds a Start Menu entry and (optionally) a desktop shortcut. No
Python, no terminal. Chromium is bundled in, so "Check && Download
Latest" works immediately without any extra downloads.

No admin rights are required - it installs to your own user profile.

To get a newer version later, click "Check for Updates" (top-right,
next to "?") - it'll offer to open the download page if one's
available. This only works once this repo's releases are public
(GitHub's unauthenticated API can't see private-repo releases); until
then it'll just say it couldn't check. Running a newer installer over
an existing install always upgrades in place - no need to uninstall
first.

## The three tabs

- **Update Keypad** - the language file update flow described below.
- **Export Config** - pick a VFD configuration file saved on the
  keypad and a destination folder, then copy it off. Just a plain file
  copy (browse to wherever the file actually lives on the mounted
  keypad drive) - there's no assumed folder/naming convention for
  these like there is for `LANG`/`KPCONF`.
- **Export Screenshots** - identical to Export Config, for screenshots
  saved on the keypad instead.

## Development setup

For working on the code itself (Linux/macOS/Windows with a terminal):

```bash
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium     # one-time, downloads a headless browser (~150-300MB)
python3 vw3a_lang_updater.py
```

Tested with Python 3.12 / PySide6 6.x on Linux; should run unmodified
on Windows and macOS since it uses only cross-platform APIs (Qt +
`pathlib`/`shutil`/`webbrowser` + Playwright).

**Why Playwright?** Schneider's download page (`se.com`) blocks plain
Python HTTP requests (confirmed 403, even with full browser-matching
headers) but loads fine in a real browser - this points to TLS/JA3-
level fingerprinting rather than anything header-based, which only an
actual browser network stack can get past. Playwright drives a real
headless Chromium, so it does. The `playwright install chromium` step
downloads that browser once; after that it runs invisibly in the
background whenever you click "Check && Download Latest."

## Usage

1. **Get the language package.** Three ways, in order of convenience:
   - **"Check && Download Latest (auto, via browser)"** - launches a
     headless Chromium in the background, reads the current version
     off Schneider's page, and downloads the zip. This is the primary
     path now that it's backed by a real browser engine.
   - **Manual download** - "Open Download Page in Browser" opens the
     real Schneider page in your normal browser; download the zip
     yourself, then "Select Downloaded Zip..." to point the app at it
     (defaults to your `Downloads` folder). Useful as a fallback, or if
     you just prefer doing it by hand.
   - **Manual unzip** - if you'd rather extract the zip yourself with
     your file manager, "Select Extracted Folder..." points the app
     directly at that folder instead, skipping the app's own
     extraction step. It just needs to contain `LANG` and `KPCONF`
     subfolders.
2. **Select languages** — check up to 10 languages from the extracted
   package. English is pre-checked by default (Schneider recommends
   always keeping English installed for support purposes). `Fonts.ums`
   is always included automatically.
3. **Select keypad drive** — plug the keypad into USB. The app scans
   common removable-media mount points on startup (`/run/media`,
   `/media`, `/mnt` on Linux; `/Volumes` on macOS; drive letters on
   Windows) and lists them, flagging any that already look like a
   keypad drive (contain `LANG`/`KPCONF`). Click one to select it, hit
   "Refresh Detected Drives" if you plug the keypad in after opening
   the app, or "Browse Manually..." for anything the scan misses.
4. **Apply Update** — with "back up existing" checked (recommended, on
   by default), it copies your current `LANG`/`KPCONF` to
   `~/.vw3a_lang_updater/backups/<drive-name>/` before deleting anything.
   Once it finishes, an **"Eject Drive"** button appears - click it to
   safely unmount/eject the drive (via `udisksctl` on Linux, `diskutil`
   on macOS, or the Shell COM object on Windows), then reconnect the
   keypad to the drive normally. Check "Automatically eject after
   updating" beforehand if you'd rather skip that manual click.

Note (per Schneider): the keypad's other settings, e.g. wheel
sensitivity, get reset after a language update — you may need to
reconfigure those separately.

## Known limitations / things to verify yourself

- **No official API.** Schneider doesn't publish a version feed for
  this package. The version comes from scraping `Version: Vx.xx` and
  the zip filename off the download page's HTML (via the headless
  browser) - if Schneider redesigns that page, `fetch_and_download_via_browser()`
  in `vw3a_lang_updater.py` will need a small update to match.
- **Drive auto-detection is a convenience, not a guarantee.** It only
  scans conventional mount locations for your OS. If your setup mounts
  removable drives somewhere unusual, use "Browse Manually...". Either
  way, always confirm you've got the right drive before clicking Apply.
- **Region.** The download page is the US regional one. Other se.com
  locales have sometimes lagged behind on version number historically.
  Change `SE_DOWNLOAD_PAGE` near the top of `vw3a_lang_updater.py` if
  you want a different region.
- **Not an official Schneider integration** — it's automating public,
  manual steps.
- If you rename a downloaded zip (or your own extracted folder) so it
  no longer matches Schneider's naming pattern, the app can't parse a
  version number from it anymore and will log a warning and record the
  version as "unknown" — it'll still work, you just lose accurate
  version tracking for that import.
- The auto-fetch's progress bar is indeterminate (a busy indicator, not
  a percentage) since Playwright doesn't expose byte-level download
  progress the way a raw HTTP client would.
- **Eject requires `udisksctl`/`findmnt` on Linux** (both standard on
  desktop distros - part of udisks2 and util-linux respectively; may be
  missing on minimal/server installs), `diskutil` on macOS (built-in),
  or `powershell` on Windows (built-in). If eject fails, you can always
  still eject manually through your OS's file manager.
- **If "Apply Update" itself takes minutes despite the files being
  tiny (KB, not MB):** this isn't the app's own doing - the transfer
  now runs on a background thread so the window won't freeze/"Not
  Responding" anymore, but the log will show a per-step timing
  breakdown (backup / delete / copy each file) so you can see exactly
  where the time goes. On Windows, a very common cause is the drive's
  removal policy being set to "Quick Removal" (Device Manager → Disk
  drives → your device → Policies), which disables write caching and
  forces every small file write to flush synchronously to the device -
  fine for safety, brutal for lots of tiny writes to slow embedded
  flash storage inside the keypad itself. Switching to "Better
  Performance" trades that safety margin for speed (remember to safely
  eject afterward either way). If it's still slow after checking that,
  the timing log will at least tell us which step to dig into further.

## Releasing a new Windows build

Push a version tag and GitHub Actions builds and publishes the
installer automatically (see `.github/workflows/build-windows-installer.yml`):

```bash
git tag v1.0.0
git push origin v1.0.0
```

That creates a GitHub Release named `v1.0.0` with
`VW3ALanguageUpdater-Setup-1.0.0.exe` attached. To build/test a
one-off installer without tagging (or without releasing publicly), run
the workflow manually from the Actions tab ("Run workflow") - it
uploads the installer as a workflow artifact instead. See
`packaging/windows/README.md` to build it locally instead.
