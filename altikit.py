#!/usr/bin/env python3
"""
AltiKit - a utility for Schneider Electric VW3A1111 / VW3A1121 Graphic
Display Terminal keypads.

Update Keypad tab: checks Schneider Electric's public download page for
the current language package version, downloads and extracts it, lets
you pick which languages to install (device limit: 10 languages + fonts
= 11 files), and copies them onto a keypad connected as a USB
mass-storage drive.

Export Config / Export Screenshots tabs: copy VFD configuration files
(DRVCONF) or screenshots (PRTSCR) off the keypad to a folder of your
choosing.

IMPORTANT / KNOWN LIMITATIONS (please read):
  * Schneider does not publish an API or version feed for this package.
    "Check for updates" works by scraping the public download page's HTML
    for the "Version: Vx.xx" text and the .zip filename. If Schneider
    changes that page's layout, this check will start failing loudly
    (it is written to fail with an error, not silently guess wrong).
  * This has been verified against the US regional page. Other regional
    se.com pages have historically lagged behind on version number -
    change SE_DOWNLOAD_PAGE below if you want a different region.
  * This is not an official Schneider tool/integration - it automates the
    same manual steps described in Schneider's own Readme
    (Readme_Languages_Update_for_VW3A1111.txt): delete LANG + KPCONF on
    the keypad, copy the new ones over.
  * Always verify the target folder really is the keypad before applying.
    The app checks for existing LANG/KPCONF folders as a sanity check but
    cannot guarantee you selected the right drive.
"""

import hashlib
import json
import os
import re
from html import unescape
import shutil
import subprocess
import sys
import time
import webbrowser
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox, 
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SE_DOWNLOAD_PAGE = "https://www.se.com/us/en/download/document/Languages_Drives_VW3A1111/"
# Approximate "Schneider Electric green" - a commonly-cited hex for their
# brand color, not pulled from an official brand guideline doc. Swap this
# if you have the exact value from Schneider's own brand kit.
SCHNEIDER_GREEN = "#3DCD58"

# When frozen by PyInstaller, __file__ resolves inside the bundle's
# internal extraction dir, not next to the .exe. sys._MEIPASS is
# PyInstaller's own answer to "where are my bundled data files" - for
# onedir builds (what we use) that's the _internal\ folder next to the
# exe; using sys.executable's parent directly is wrong since PyInstaller
# 6.x nests bundled data under _internal\ rather than next to the exe
# itself. In dev/venv runs, sys.frozen isn't set.
if getattr(sys, "frozen", False):
    _APP_ROOT = Path(sys._MEIPASS)
    # icon-256.png is bundled flat (via --add-data ...;.) rather than
    # under packaging/icon/ like in the source tree - see build docs.
    ICON_PATH = _APP_ROOT / "icon-256.png"
    # Also make the bundled Playwright/Chromium (installed at build time
    # with PLAYWRIGHT_BROWSERS_PATH=0, so it lands inside the playwright
    # package itself and gets picked up by PyInstaller automatically)
    # discoverable at runtime - without this, Playwright would look in
    # the OS's global browser cache instead, which won't exist on a
    # fresh install.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
else:
    _APP_ROOT = Path(__file__).resolve().parent
    ICON_PATH = _APP_ROOT / "packaging" / "icon" / "icon-256.png"
README_PATH = _APP_ROOT / "README.md"
# Deliberately separate from README.md: the in-app help is for people
# already running the app (usage info + a link to the repo), not
# install/build instructions they don't need at that point.
HELP_PATH = _APP_ROOT / "HELP.md"

_VERSION_FILE = _APP_ROOT / "VERSION.txt"
APP_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.is_file() else "dev"

# Used for the "Check for Updates" feature - queries GitHub's public,
# unauthenticated releases API. Only works once the repo (or at least its
# releases) is public: private repos 404 for anonymous requests, which is
# handled as a plain "couldn't check" message rather than a crash.
GITHUB_REPO = "AGrant9130/AltiKit"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

APP_DIR = Path.home() / ".altikit"
CACHE_DIR = APP_DIR / "downloads"
STATE_FILE = APP_DIR / "state.json"
MAX_LANGUAGES = 10  # device hard limit, not counting Fonts.ums

# code -> display name, taken from Schneider's own Readme file.
# "(incomplete)" markers per Schneider's readme are preserved as a hint.
# Note: V1.78's actual package also includes "jp"/"nl"/"uk", which aren't
# documented in that readme. jp/nl are unambiguous (Japanese/Dutch) and
# added below; "uk" is deliberately left unmapped (falls back to showing
# the raw code) rather than guessed at - it can't be Ukrainian (that's
# already "ua"), most likely a UK-English variant, but that's unconfirmed.
LANGUAGE_NAMES = {
    "bg": "Bulgarian (incomplete)",
    "br": "Portuguese (Brazil)",
    "cn": "Chinese (Simplified)",
    "cs": "Czech",
    "de": "German",
    "dk": "Danish",
    "el": "Greek (incomplete)",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish (incomplete)",
    "fr": "French",
    "hu": "Hungarian",
    "jp": "Japanese",
    "ko": "Korean",
    "it": "Italian",
    "nl": "Dutch",
    "no": "Norwegian (incomplete)",
    "pl": "Polish",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak (incomplete)",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "tw": "Chinese (Traditional)",
    "ua": "Ukrainian",
    "vi": "Vietnamese",
}

# Most-to-least commonly needed, for sorting the language list in the GUI
# (English/Spanish first per explicit preference, then roughly by
# international prevalence) rather than alphabetically by code.
LANGUAGE_ORDER = [
    "en", "es", "fr", "de", "nl", "it", "br", "cn", "ru", "tw", "jp", "pl",
    "tr", "ko", "vi", "ua", "ro", "el", "cs", "hu", "sv", "dk",
    "th", "bg", "sk", "fi", "no",
]
_LANGUAGE_RANK = {code: i for i, code in enumerate(LANGUAGE_ORDER)}


@dataclass
class RemotePackageInfo:
    version: str
    zip_filename: str
    download_url: str


ZIP_FILENAME_RE = re.compile(
    r"Languages_for_VW3A1111_Advanced_graphic_display_terminal_U"
    r"(\d+(?:\.\d+)*)(?:\s*\(\d+\))?(?:\.zip)?",
    re.IGNORECASE,
)


def parse_version_from_filename(filename: str) -> str:
    """Best-effort version extraction from a manually-downloaded zip's
    filename. Falls back to 'unknown' if the file was renamed and no
    longer matches Schneider's naming pattern (e.g. Chrome appending
    "(1)" to a duplicate download is still handled - the regex just
    needs the "U<version>.zip" portion to be intact)."""
    match = ZIP_FILENAME_RE.search(filename)
    return match.group(1) if match else "unknown"


@dataclass
class LocalState:
    installed_version: str = ""
    zip_path: str = ""
    extracted_dir: str = ""
    selected_languages: list[str] = field(default_factory=lambda: ["en"])
    config_export_dir: str = ""
    screenshot_export_dir: str = ""
    config_import_browse_dir: str = ""
    # ISO timestamp of the last successful (launch-time, automatic)
    # language-pack version check - throttles that check to once a day
    # since it requires launching headless Chromium, unlike the app
    # update check which is a cheap plain HTTP GET. Left blank on
    # failure so the next launch retries rather than waiting out the
    # day on e.g. a transient network error.
    last_lang_check: str = ""

    @classmethod
    def load(cls) -> "LocalState":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return cls(**data)
            except Exception:
                pass
        return cls()

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.__dict__, indent=2))


# --------------------------------------------------------------------------
# Core logic (no GUI dependencies, so it's testable / reusable)
# --------------------------------------------------------------------------


def _parse_version(v: str) -> tuple[int, ...]:
    """Turns "v1.2.3" (or "1.2.3", or with extra non-numeric suffixes
    like "1.2.3-dev") into (1, 2, 3) for comparison. Non-numeric/missing
    parts become 0 rather than raising, since tags aren't guaranteed to
    be strict semver."""
    v = v.strip().lstrip("vV")
    parts = []
    for piece in v.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(current_version: str) -> dict:
    """Queries GitHub's public, unauthenticated releases API for the
    latest release and compares it to current_version. Returns a dict
    with a "status" key ("update_available" / "up_to_date" / "error")
    plus supporting details - never raises, so the GUI can just branch
    on status rather than handle exceptions from a background thread.
    """
    request = Request(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "altikit"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404:
            return {
                "status": "error",
                "message": "Couldn't check for updates - the release page isn't public yet.",
            }
        return {"status": "error", "message": f"Couldn't check for updates (HTTP {e.code})."}
    except (URLError, TimeoutError, OSError) as e:
        return {"status": "error", "message": f"Couldn't check for updates: {e}"}
    except json.JSONDecodeError:
        return {"status": "error", "message": "Couldn't check for updates: unexpected response from GitHub."}

    latest_tag = data.get("tag_name")
    release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
    if not latest_tag:
        return {"status": "error", "message": "Couldn't check for updates: no release found."}

    if _parse_version(latest_tag) > _parse_version(current_version):
        return {
            "status": "update_available",
            "latest_version": latest_tag,
            "url": release_url,
            "assets": data.get("assets", []),
        }
    return {"status": "up_to_date", "latest_version": latest_tag}


def _find_release_asset(assets: list[dict], name_matches) -> str | None:
    """Returns the download URL of the first asset whose filename
    satisfies name_matches(name), or None."""
    for asset in assets:
        name = asset.get("name", "")
        if name_matches(name):
            return asset.get("browser_download_url")
    return None


def _find_windows_installer_asset(assets: list[dict]) -> str | None:
    """Picks the AltiKit-Setup-*.exe asset's download URL out of a
    GitHub release's asset list, if present."""
    return _find_release_asset(assets, lambda name: name.startswith("AltiKit-Setup-") and name.endswith(".exe"))


def _find_windows_checksum_asset(assets: list[dict]) -> str | None:
    """Picks the AltiKit-Setup-*.exe.sha256 asset's download URL, if
    present - CI writes one alongside every installer build."""
    return _find_release_asset(assets, lambda name: name.endswith(".exe.sha256"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_expected_checksum(url: str) -> str:
    """Fetches a sha256sum-style checksum file ("<hex digest>  <filename>")
    and returns just the hex digest."""
    request = Request(url, headers={"User-Agent": "altikit"})
    with urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0] if text else ""
    digest = first_line.split()[0] if first_line.split() else ""
    if len(digest) != 64:
        raise RuntimeError("Checksum file was empty or in an unexpected format.")
    return digest.lower()


def _download_file(url: str, dest_path: Path, log=lambda msg: None) -> None:
    """Streams a URL to disk in chunks (rather than reading it all into
    memory at once - the Windows installer is ~150-300MB with bundled
    Chromium) and logs rough progress if the server reports a size."""
    request = Request(url, headers={"User-Agent": "altikit"})
    with urlopen(request, timeout=30) as response, open(dest_path, "wb") as f:
        total = response.length or 0
        downloaded = 0
        last_reported_pct = -1
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                if pct != last_reported_pct:
                    log(f"Downloading... {pct}%")
                    last_reported_pct = pct
    if not dest_path.is_file() or dest_path.stat().st_size == 0:
        raise RuntimeError("Downloaded file was empty.")


def perform_self_update(update_result: dict, log=lambda msg: None) -> None:
    """Downloads and applies the update described by update_result (the
    dict check_for_update() returns for status="update_available"), then
    launches the updated app as a new process. Raises with a clear
    message on any failure rather than guessing/silently giving up. The
    caller is expected to quit the current process immediately after
    this returns successfully - the running app's files may already be
    locked/replaced by that point.
    """
    if sys.platform == "win32":
        _self_update_windows(update_result, log)
    elif sys.platform.startswith("linux") or sys.platform == "darwin":
        _self_update_unix(log)
    else:
        raise RuntimeError(f"Auto-update isn't supported on platform '{sys.platform}'.")


def _self_update_windows(update_result: dict, log) -> None:
    assets = update_result.get("assets", [])
    asset_url = _find_windows_installer_asset(assets)
    if not asset_url:
        raise RuntimeError(
            "Couldn't find a Windows installer in the latest release's files. "
            f"Check {update_result.get('url', 'the Releases page')} manually."
        )
    checksum_url = _find_windows_checksum_asset(assets)
    if not checksum_url:
        # Fail closed rather than silently skipping verification - an
        # older release built before this feature existed, or a release
        # someone edited by hand, shouldn't get a free pass to run
        # unverified.
        raise RuntimeError(
            "This release has no checksum file to verify the download "
            f"against - refusing to auto-install. Update manually from "
            f"{update_result.get('url', 'the Releases page')} instead."
        )

    import tempfile
    installer_name = asset_url.rsplit("/", 1)[-1]
    installer_path = Path(tempfile.gettempdir()) / installer_name
    log(f"Downloading {installer_name}...")
    _download_file(asset_url, installer_path, log=log)

    log("Verifying checksum...")
    expected_hash = _fetch_expected_checksum(checksum_url)
    actual_hash = _sha256_file(installer_path)
    if actual_hash != expected_hash:
        installer_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Checksum mismatch - the downloaded installer doesn't match "
            "what was published (could be a corrupted download or a "
            "tampered file). Not running it. Try again, or update "
            f"manually from {update_result.get('url', 'the Releases page')}."
        )
    log("Checksum verified.")

    log("Launching installer...")
    # Not silent - shows the normal install wizard, same as running it
    # by hand, so the user can see/control the upgrade rather than have
    # files replaced with no visible feedback. CloseApplications /
    # RestartApplications in installer.iss are the safety net if this
    # process hasn't fully exited by the time Inno Setup needs to
    # replace its files.
    subprocess.Popen([str(installer_path)], close_fds=True)


def _self_update_unix(log) -> None:
    # No packaged installer for Linux/macOS (see README) - this mirrors
    # update.sh: git pull, then re-sync via install.sh. Only works for
    # an actual git checkout, same limitation update.sh already has.
    repo_dir = _APP_ROOT
    if not (repo_dir / ".git").is_dir():
        raise RuntimeError(
            "This isn't a git checkout, so it can't update itself "
            "automatically. Re-download the source from the Releases "
            "page and run install.sh, or use update.sh from a terminal."
        )

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    ).stdout
    if status.strip():
        raise RuntimeError(
            "This checkout has uncommitted local changes - not "
            "auto-updating to avoid clobbering them. Commit or stash "
            "them, then try again (or run update.sh manually)."
        )

    log("Pulling latest changes...")
    try:
        subprocess.run(["git", "pull"], cwd=repo_dir, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git pull failed: {e.stderr.strip() or e.stdout.strip()}") from e

    install_script = repo_dir / "install.sh"
    log("Re-syncing dependencies (this can take a minute)...")
    try:
        subprocess.run([str(install_script)], cwd=repo_dir, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"install.sh failed: {e.stderr.strip() or e.stdout.strip()}") from e

    log("Restarting...")
    subprocess.Popen(
        [str(repo_dir / "launch.sh")], cwd=repo_dir, close_fds=True, start_new_session=True,
    )


SE_HOMEPAGE = "https://www.se.com/us/en/"

# NOTE: fetching / downloading from se.com uses a real headless Chromium
# via Playwright, not the `requests` library. Confirmed by testing: even
# with a full set of browser-matching headers (User-Agent, Accept,
# Sec-Fetch-*, a homepage warm-up request for cookies/Referer), plain
# `requests` calls to www.se.com get a 403 Forbidden that a real browser
# never sees - almost certainly TLS/JA3-level fingerprinting rather than
# anything header-based, which only an actual browser network stack can
# pass. Playwright is a genuine Chromium instance, so it does.


def _scrape_remote_package_info(page, log=lambda msg: None) -> RemotePackageInfo:
    """Loads the Schneider download page in an already-open Playwright
    page and scrapes the current version/filename/download URL out of
    it, without downloading anything. Shared by fetch_and_download_via_
    browser (which downloads after this) and check_remote_language_
    version (which doesn't - used for the launch-time auto-check)."""
    log("Loading Schneider download page in headless browser...")
    # NOTE: wait_until="networkidle" reliably times out on this page -
    # se.com keeps background requests (analytics/chat widgets, etc.)
    # going indefinitely, so the network never actually goes idle.
    # "domcontentloaded" is enough since we only need the static HTML
    # containing the version/filename text, not any late-loading
    # widgets. We then poll for the text itself (with a generous
    # timeout) since it may still render slightly after DOMContentLoaded.
    page.goto(SE_DOWNLOAD_PAGE, wait_until="domcontentloaded", timeout=60000)

    # Rather than guessing the download URL's domain/query params
    # ourselves (Schneider has changed both before - the domain moved
    # from download.schneider-electric.com to download.se.com, and
    # p_enDocType's value has changed too), scrape the actual download
    # link straight out of the page. It's the file name (which embeds
    # the version, e.g. "..._U1.78.zip") that we actually need;
    # everything else about the URL is just whatever Schneider
    # currently uses to serve it.
    link_match = None
    deadline = time.time() + 30
    while time.time() < deadline:
        html = page.content()
        link_match = re.search(
            r'https://download\.se\.com/files\?[^"\'<>\s]+', html
        )
        if link_match:
            break
        page.wait_for_timeout(500)

    if not link_match:
        raise RuntimeError(
            "Could not find the download link on the Schneider "
            "download page - the page layout may have changed. "
            f"Check {SE_DOWNLOAD_PAGE} manually."
        )
    download_url = unescape(link_match.group(0))
    query = parse_qs(urlparse(download_url).query)
    zip_filename = query.get("p_File_Name", [None])[0]
    if not zip_filename:
        raise RuntimeError(
            "Found a download link but it has no p_File_Name "
            "parameter - the page layout may have changed. Check "
            f"{SE_DOWNLOAD_PAGE} manually."
        )
    version = parse_version_from_filename(zip_filename)
    if version == "unknown":
        raise RuntimeError(
            f"Found download link for '{zip_filename}' but "
            "couldn't parse a version number out of it - the "
            "naming convention may have changed. Check "
            f"{SE_DOWNLOAD_PAGE} manually."
        )
    return RemotePackageInfo(version=version, zip_filename=zip_filename, download_url=download_url)


def _launch_headless_chromium(p):
    try:
        return p.chromium.launch(headless=True)
    except Exception as e:
        raise RuntimeError(
            "Couldn't launch the headless Chromium browser. If this "
            "is the first run, you likely need: playwright install "
            f"chromium\n\nOriginal error: {e}"
        ) from e


def fetch_and_download_via_browser(dest_dir: Path, log=lambda msg: None) -> tuple[str, Path]:
    """Loads the Schneider download page and fetches the zip using a
    real headless browser (Playwright/Chromium), returning
    (version, downloaded_zip_path).

    Requires `playwright install chromium` to have been run once - see
    README.md. Raises RuntimeError with a clear message on any failure
    (page layout changed, download timed out, etc.) rather than
    guessing.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright isn't installed. Run: pip install playwright  "
            "then: playwright install chromium"
        ) from e

    dest_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_headless_chromium(p)
        try:
            page = browser.new_page()
            info = _scrape_remote_package_info(page, log)

            dest_path = dest_dir / info.zip_filename
            if dest_path.exists() and dest_path.stat().st_size > 0:
                log(f"Already have {info.zip_filename} cached, skipping download.")
                return info.version, dest_path

            log(f"Found V{info.version} ({info.zip_filename}), downloading...")
            with page.expect_download(timeout=60000) as download_info:
                try:
                    page.goto(info.download_url)
                except Exception:
                    # Navigating to a file that triggers a download always
                    # raises a navigation error in Playwright even on
                    # success - the download itself is captured separately
                    # below, so this is expected and safe to ignore.
                    pass
            download = download_info.value
            download.save_as(str(dest_path))
            log(f"Downloaded to {dest_path}")
            return info.version, dest_path
        finally:
            browser.close()


def check_remote_language_version(log=lambda msg: None) -> RemotePackageInfo:
    """Launches headless Chromium just long enough to scrape the
    current remote language-package version, without downloading
    anything. Used for the throttled launch-time auto-check; callers
    compare the result against LocalState.installed_version themselves
    and can call fetch_and_download_via_browser afterward if the user
    wants to actually download it."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright isn't installed. Run: pip install playwright  "
            "then: playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = _launch_headless_chromium(p)
        try:
            page = browser.new_page()
            return _scrape_remote_package_info(page, log)
        finally:
            browser.close()


def extract_package(zip_path: Path) -> Path:
    """Extract into CACHE_DIR/<version-named-subdir>, returns that dir."""
    extract_dir = CACHE_DIR / zip_path.stem
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        # Guard against a zip entry using "../" (or similar) to write
        # outside extract_dir ("zip slip") before extracting anything.
        resolved_dir = extract_dir.resolve()
        for member in zf.namelist():
            if not (resolved_dir / member).resolve().is_relative_to(resolved_dir):
                raise RuntimeError(f"Refusing to extract unsafe zip entry: {member}")
        zf.extractall(extract_dir)

    # The zip may extract flat or into a single nested folder - normalize
    # so extract_dir directly contains LANG/ and KPCONF/.
    lang_dir = find_subdir(extract_dir, "LANG")
    kpconf_dir = find_subdir(extract_dir, "KPCONF")
    if lang_dir is None or kpconf_dir is None:
        raise RuntimeError(
            "Extracted package doesn't contain the expected LANG and "
            "KPCONF folders. Schneider may have changed the package "
            f"layout - inspect {extract_dir} manually."
        )
    return extract_dir


def find_subdir(root: Path, name: str) -> Path | None:
    if (root / name).is_dir():
        return root / name
    for p in root.rglob(name):
        if p.is_dir():
            return p
    return None


def list_available_languages(extract_dir: Path) -> list[tuple[str, str, Path]]:
    """Returns list of (code, display_name, ums_path) for *_labels.ums
    files, ordered most-to-least commonly needed per LANGUAGE_ORDER
    (falling back to alphabetical for anything not in that list)."""
    lang_dir = find_subdir(extract_dir, "LANG")
    if lang_dir is None:
        return []
    results = []
    for ums in lang_dir.glob("*_labels.ums"):
        code = ums.stem.replace("_labels", "")
        name = LANGUAGE_NAMES.get(code, code)
        results.append((code, name, ums))
    results.sort(key=lambda item: (_LANGUAGE_RANK.get(item[0], len(LANGUAGE_ORDER)), item[0]))
    return results


def looks_like_keypad_drive(path: Path) -> bool:
    """Sanity check that a target folder looks like the keypad's root."""
    try:
        return (path / "LANG").is_dir() or (path / "KPCONF").is_dir()
    except PermissionError:
        return False


def detect_candidate_drives() -> list[tuple[Path, bool]]:
    """Scans typical removable-media mount points for the current OS and
    returns (path, looks_like_keypad) tuples, so the GUI can offer a
    one-click list instead of manual folder navigation (e.g. through
    /run/media/<user>/<label> on Linux).

    This only looks in a handful of conventional locations - if your
    distro/OS mounts removable drives somewhere unusual, "Browse
    Manually..." is still available as a fallback.
    """
    candidates: set[Path] = set()

    if sys.platform.startswith("linux"):
        # /run/media and /media are where udisks2/systemd auto-mount
        # actual removable media on hotplug. Deliberately not scanning
        # /mnt: that's the traditional spot for manually-mounted arbitrary
        # filesystems (dual-boot partitions, network shares, etc.), which
        # isn't removable-media-specific and was showing up as irrelevant
        # "candidate" clutter.
        run_media = Path("/run/media")
        if run_media.is_dir():
            # Always /run/media/<username>/<label> - the username level
            # itself is never a mount point, just a container directory,
            # so only its children are real candidates.
            try:
                for user_dir in run_media.iterdir():
                    if not user_dir.is_dir():
                        continue
                    try:
                        candidates.update(sub for sub in user_dir.iterdir() if sub.is_dir())
                    except PermissionError:
                        pass
            except PermissionError:
                pass
        media = Path("/media")
        if media.is_dir():
            # Some distros mount directly at /media/<label>, others use
            # /media/<username>/<label> - cover both.
            try:
                for entry in media.iterdir():
                    if not entry.is_dir():
                        continue
                    candidates.add(entry)
                    try:
                        candidates.update(sub for sub in entry.iterdir() if sub.is_dir())
                    except PermissionError:
                        pass
            except PermissionError:
                pass
    elif sys.platform == "darwin":
        volumes = Path("/Volumes")
        if volumes.is_dir():
            candidates.update(e for e in volumes.iterdir() if e.is_dir())
    elif sys.platform == "win32":
        import ctypes
        import string
        DRIVE_REMOVABLE = 2
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            # Only list actually-removable drives (what USB mass-storage
            # devices like the keypad report as) rather than every drive
            # letter in use - nobody needs C:\ offered as a "candidate"
            # keypad location.
            if ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE:
                candidates.add(Path(root))

    results = [(p, looks_like_keypad_drive(p)) for p in candidates]
    # Keypad matches first, then alphabetical.
    results.sort(key=lambda t: (not t[1], str(t[0]).lower()))
    return results


def find_connected_keypad_root() -> Path | None:
    """Returns the first auto-detected drive that looks like the keypad,
    or None if none is currently connected. A connected keypad is a
    physical fact, not tied to any particular tab's UI state, so the
    export tabs use this directly rather than needing to be told about
    whatever's selected on the Update Keypad tab."""
    for path, is_keypad in detect_candidate_drives():
        if is_keypad:
            return path
    return None


# --------------------------------------------------------------------------
# Backup / restore
# --------------------------------------------------------------------------

BACKUPS_DIR = APP_DIR / "backups"

# OS-managed metadata that can show up at a removable drive's root -
# not keypad data, and "restoring" it onto a drive is meaningless at
# best (Windows/macOS manage these themselves) - excluded from backups.
# Matched case-insensitively since keypad drives are typically
# FAT32/exFAT, which are case-insensitive.
_BACKUP_EXCLUDE_NAMES = {
    "system volume information", "$recycle.bin", ".trashes", ".fseventsd",
    ".spotlight-v100", ".ds_store", "desktop.ini", "thumbs.db",
}


def _drive_identifier(target_root: Path) -> str:
    """Stable-ish short identifier for a keypad target, used to name
    backup folders and to match a backup back to "the currently selected
    drive" later. target_root.name is empty for a Windows drive root
    (e.g. "E:\\"), so fall back to the drive letter there; Linux/macOS
    mount points already have a meaningful .name."""
    return target_root.name or target_root.drive.rstrip(":\\") or "keypad"


def _sanitize_backup_label(label: str) -> str:
    """Reduces a free-typed label to characters safe in a folder name.
    Never raises - an empty/all-invalid label just yields "", which
    take_backup() treats as "no label" rather than failing outright."""
    label = re.sub(r"[^A-Za-z0-9 _-]", "", label.strip())
    return re.sub(r"\s+", "_", label.strip())[:40]


@dataclass
class BackupInfo:
    path: Path
    created: str  # ISO-ish timestamp string, "" if unknown (see list_backups)
    drive_id: str
    label: str
    items: list[str]  # top-level names (folders or files) captured in this backup


def describe_backup(b: BackupInfo) -> str:
    """Human-readable one-line summary of a backup - shared by the
    Update tab's restore-confirmation dialog and the Backup & Restore
    tab's list, so both describe a backup the same way."""
    created = b.created.replace("T", " ") if b.created else "(unknown time)"
    label_part = f" — {b.label}" if b.label else ""
    return f"{created}  •  Drive {b.drive_id or '?'}  •  {', '.join(b.items)}{label_part}"


def take_backup(target_root: Path, label: str = "", log=lambda msg: None) -> "BackupInfo | None":
    """Snapshots everything at target_root's top level - not just the
    keypad folders this app knows about (LANG/KPCONF/DRVCONF/PRTSCR),
    but anything else that happens to be there too (a firmware folder
    this app doesn't recognize, stray files, etc.), short of the
    OS-metadata entries in _BACKUP_EXCLUDE_NAMES. This is deliberately
    a "back up everything, in case of anything" snapshot rather than a
    curated list, since a curated list can only protect against data
    loss it already anticipated.

    Goes into a new, distinct folder under BACKUPS_DIR - unlike a
    single "last backup" location, this never overwrites a previous
    backup, so a history accumulates for the Backup & Restore tab to
    list and restore from individually. Returns None (and leaves
    nothing behind) if target_root had nothing worth backing up.
    """
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    drive_id = _drive_identifier(target_root)
    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    safe_label = _sanitize_backup_label(label)
    base_name = f"{timestamp}_{drive_id}" + (f"_{safe_label}" if safe_label else "")
    backup_dir = BACKUPS_DIR / base_name
    suffix = 2
    while backup_dir.exists():
        backup_dir = BACKUPS_DIR / f"{base_name}_{suffix}"
        suffix += 1
    backup_dir.mkdir(parents=True)

    items_backed_up = []
    for entry in sorted(target_root.iterdir()):
        if entry.name.lower() in _BACKUP_EXCLUDE_NAMES:
            continue
        log(f"Backing up {entry.name}...")
        dest = backup_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)
        items_backed_up.append(entry.name)
        log(f"Backed up {entry.name}")

    if not items_backed_up:
        shutil.rmtree(backup_dir)
        return None

    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {"created": created, "drive_id": drive_id, "label": label.strip(), "items": items_backed_up}
    (backup_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return BackupInfo(path=backup_dir, created=created, drive_id=drive_id, label=label.strip(), items=items_backed_up)


def list_backups() -> list[BackupInfo]:
    """Returns every backup under BACKUPS_DIR, newest first."""
    if not BACKUPS_DIR.is_dir():
        return []
    results = []
    for entry in BACKUPS_DIR.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        info = None
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
                info = BackupInfo(
                    path=entry, created=meta.get("created", ""),
                    drive_id=meta.get("drive_id", ""), label=meta.get("label", ""),
                    items=meta.get("items", []),
                )
            except Exception:
                info = None
        if info is None:
            # No/corrupt meta.json - fall back to whatever's actually in
            # the backup folder rather than hiding the backup entirely.
            items = [p.name for p in entry.iterdir() if p.name != "meta.json"]
            if items:
                info = BackupInfo(path=entry, created="", drive_id="", label="", items=items)
        if info is not None:
            results.append(info)
    results.sort(key=lambda b: b.created or b.path.name, reverse=True)
    return results


def find_latest_backup_for_drive(drive_id: str, required_items: list[str]) -> "BackupInfo | None":
    """Most recent backup tagged with drive_id that contains every name
    in required_items, or None. Used by the Update tab's one-click
    "Restore Last Backup" to find what to offer without the user having
    to pick from a list."""
    for backup in list_backups():
        if backup.drive_id == drive_id and all(i in backup.items for i in required_items):
            return backup
    return None


def restore_backup(backup: BackupInfo, items: list[str], target_root: Path, log=lambda msg: None) -> None:
    """Copies the given top-level names (folders or files) from a backup
    back onto target_root, fully replacing whatever's currently there
    for each one - mirrors apply_update()'s own delete-then-copy
    semantics, so a restore behaves the same way an update does rather
    than merging file-by-file."""
    for name in items:
        src = backup.path / name
        if not src.exists():
            log(f"WARNING: backup has no {name}, skipping")
            continue
        dst = target_root / name
        if dst.is_dir():
            log(f"Removing existing {name}...")
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        log(f"Restoring {name}...")
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        log(f"Restored {name}")


def apply_update(
    extract_dir: Path,
    selected_codes: list[str],
    target_root: Path,
    make_backup: bool = True,
    backup_label: str = "before update",
    log=lambda msg: None,
    progress=lambda done, total: None,
) -> None:
    """Deletes LANG+KPCONF on target and copies the new ones over,
    filtering LANG down to the selected languages + Fonts.ums. If
    make_backup, snapshots the entire keypad (LANG/KPCONF/DRVCONF/PRTSCR,
    whichever exist) via take_backup() first, tagged with backup_label -
    see take_backup() for why it covers more than just the folders being
    replaced.

    Logs a timing breakdown for each step (backup / delete / copy) since
    on some systems - especially Windows with removable drives set to
    the "Quick Removal" policy, which disables write caching - each
    small file write is flushed synchronously to the device, and a
    handful of tiny files can still take minutes on slow embedded flash
    storage. That's a Windows/USB-driver-level behavior, not something
    this script can speed up directly, but the per-step timing makes it
    obvious whether it's the backup, the delete, or the copy that's
    actually slow.

    `progress(done, total)` is called once up front with the planned
    step count (backups + deletes + file copies, each counting as one
    step regardless of size) and again after each step completes, so a
    GUI can drive a determinate progress bar instead of a busy spinner.
    """
    t_start = time.perf_counter()

    src_lang = find_subdir(extract_dir, "LANG")
    src_kpconf = find_subdir(extract_dir, "KPCONF")
    if src_lang is None or src_kpconf is None:
        raise RuntimeError("Source package is missing LANG or KPCONF folder.")

    if len(selected_codes) > MAX_LANGUAGES:
        raise RuntimeError(
            f"{len(selected_codes)} languages selected, device supports a "
            f"maximum of {MAX_LANGUAGES} (plus fonts)."
        )

    dst_lang = target_root / "LANG"
    dst_kpconf = target_root / "KPCONF"

    delete_targets = [existing for existing in (dst_lang, dst_kpconf) if existing.is_dir()]
    fonts_src = src_lang / "Fonts.ums"
    lang_files = [
        (code, src_lang / f"{code}_labels.ums")
        for code in selected_codes
        if (src_lang / f"{code}_labels.ums").exists()
    ]
    total_steps = (
        (1 if make_backup else 0) + len(delete_targets)
        + (1 if fonts_src.exists() else 0) + len(lang_files) + 1  # +1 for KPCONF folder copy
    )
    done_steps = 0
    progress(done_steps, total_steps)

    def timed_step(label, fn):
        nonlocal done_steps
        t0 = time.perf_counter()
        fn()
        done_steps += 1
        progress(done_steps, total_steps)
        log(f"  ({label} took {time.perf_counter() - t0:.1f}s)")

    if make_backup:
        log("Backing up entire keypad before making changes...")
        timed_step("full backup", lambda: take_backup(target_root, label=backup_label, log=log))

    for existing in delete_targets:
        log(f"Removing existing {existing}...")
        timed_step(f"delete {existing.name}", lambda e=existing: shutil.rmtree(e))
        log(f"Removed existing {existing}")

    dst_lang.mkdir(parents=True, exist_ok=True)
    if fonts_src.exists():
        timed_step("copy Fonts.ums", lambda: shutil.copy2(fonts_src, dst_lang / "Fonts.ums"))
        log("Copied Fonts.ums")
    else:
        log("WARNING: Fonts.ums not found in source package")

    copied_codes = {code for code, _path in lang_files}
    for code in selected_codes:
        fname = f"{code}_labels.ums"
        if code not in copied_codes:
            log(f"WARNING: {fname} not found in source package, skipping")
            continue
        src_file = src_lang / fname
        timed_step(f"copy {fname}", lambda s=src_file, f=fname: shutil.copy2(s, dst_lang / f))
        log(f"Copied {fname}")

    timed_step("copy KPCONF folder", lambda: shutil.copytree(src_kpconf, dst_kpconf))
    log(f"Copied KPCONF folder ({dst_kpconf})")
    log(f"Done in {time.perf_counter() - t_start:.1f}s total. "
        "Safely eject the drive, then plug the keypad back into the drive.")


def eject_drive(target_root: Path, log=lambda msg: None) -> None:
    """Safely unmounts/ejects the drive at target_root so it's safe to
    unplug. Platform-specific since there's no cross-platform stdlib way
    to do this - shells out to the OS's own eject mechanism (udisksctl on
    Linux, diskutil on macOS, the Shell COM object on Windows) rather
    than guessing, and raises RuntimeError with the real error if it's
    missing or fails, per this app's fail-loud philosophy."""
    if sys.platform.startswith("linux"):
        try:
            device = subprocess.run(
                ["findmnt", "-no", "SOURCE", str(target_root)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except FileNotFoundError as e:
            raise RuntimeError(
                "Can't eject: 'findmnt' isn't available (part of "
                "util-linux, should be present on virtually all Linux "
                "systems)."
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Can't eject: {target_root} doesn't look like a mount "
                "point (findmnt found no device for it)."
            ) from e

        try:
            log(f"Unmounting {device}...")
            subprocess.run(["udisksctl", "unmount", "-b", device], capture_output=True, text=True, check=True)
            log(f"Powering off {device} (safe to unplug after this)...")
            subprocess.run(["udisksctl", "power-off", "-b", device], capture_output=True, text=True, check=True)
        except FileNotFoundError as e:
            raise RuntimeError(
                "Can't eject: 'udisksctl' isn't available (part of "
                "udisks2, standard on most desktop Linux distros)."
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Eject failed: {e.stderr.strip() or e.stdout.strip()}") from e
    elif sys.platform == "darwin":
        try:
            log(f"Ejecting {target_root}...")
            subprocess.run(["diskutil", "eject", str(target_root)], capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Eject failed: {e.stderr.strip() or e.stdout.strip()}") from e
    elif sys.platform == "win32":
        drive_letter = target_root.drive  # e.g. "E:"
        if not drive_letter:
            raise RuntimeError(f"Can't eject: {target_root} doesn't look like a drive root.")
        ps_script = (
            "$s = New-Object -ComObject Shell.Application;"
            f"$s.NameSpace(17).ParseName('{drive_letter}\\').InvokeVerb('Eject')"
        )
        log(f"Ejecting {drive_letter}...")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, check=True,
                # A windowed (console-less) app's child process still gets
                # its own new console window by default on Windows unless
                # told not to - this is what was flashing a PowerShell
                # window on every eject.
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            raise RuntimeError("Can't eject: 'powershell' isn't available.") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Eject failed: {e.stderr.strip() or e.stdout.strip()}") from e
    else:
        raise RuntimeError(f"Don't know how to eject drives on platform '{sys.platform}'.")

    log("Ejected. Safe to unplug.")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------


class UpdateCheckThread(QThread):
    finished_ok = Signal(dict)

    def run(self):
        self.finished_ok.emit(check_for_update(APP_VERSION))


class SelfUpdateThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, update_result: dict):
        super().__init__()
        self.update_result = update_result

    def run(self):
        try:
            perform_self_update(self.update_result, log=self.log_msg.emit)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class LanguageCheckThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal(object)  # RemotePackageInfo
    failed = Signal(str)

    def run(self):
        try:
            info = check_remote_language_version(log=self.log_msg.emit)
            self.finished_ok.emit(info)
        except Exception as e:
            self.failed.emit(str(e))


class BrowserFetchThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal(str, object)  # version, zip_path
    failed = Signal(str)

    def run(self):
        try:
            version, zip_path = fetch_and_download_via_browser(
                CACHE_DIR, log=self.log_msg.emit
            )
            self.finished_ok.emit(version, zip_path)
        except Exception as e:
            self.failed.emit(str(e))


class ApplyThread(QThread):
    log_msg = Signal(str)
    progress = Signal(int, int)  # done, total
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, extract_dir, selected_codes, target_root, make_backup, backup_label="before update"):
        super().__init__()
        self.extract_dir = extract_dir
        self.selected_codes = selected_codes
        self.target_root = target_root
        self.make_backup = make_backup
        self.backup_label = backup_label

    def run(self):
        try:
            apply_update(
                self.extract_dir,
                self.selected_codes,
                self.target_root,
                make_backup=self.make_backup,
                backup_label=self.backup_label,
                log=self.log_msg.emit,
                progress=self.progress.emit,
            )
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class EjectThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, target_root):
        super().__init__()
        self.target_root = target_root

    def run(self):
        try:
            eject_drive(self.target_root, log=self.log_msg.emit)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class BackupThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal(object)  # BackupInfo, or None if there was nothing to back up
    failed = Signal(str)

    def __init__(self, target_root: Path, label: str):
        super().__init__()
        self.target_root = target_root
        self.label = label

    def run(self):
        try:
            info = take_backup(self.target_root, self.label, log=self.log_msg.emit)
            self.finished_ok.emit(info)
        except Exception as e:
            self.failed.emit(str(e))


class RestoreThread(QThread):
    log_msg = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, backup: BackupInfo, items: list[str], target_root: Path):
        super().__init__()
        self.backup = backup
        self.items = items
        self.target_root = target_root

    def run(self):
        try:
            restore_backup(self.backup, self.items, self.target_root, log=self.log_msg.emit)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class FileExportTab(QWidget):
    """"Pick one or more source files, pick a destination folder, copy
    them over" tab for exporting VFD configuration files (DRVCONF) and
    screenshots (PRTSCR) saved on the keypad. Auto-lists files found in
    that folder on whichever drive currently looks like the connected
    keypad, with a manual browse fallback for anything unusual (drive
    not auto-detected, file living somewhere else, etc.)."""

    def __init__(self, item_label: str, keypad_subfolder: str, state: "LocalState | None" = None, state_attr: str = ""):
        super().__init__()
        self.item_label = item_label
        self.keypad_subfolder = keypad_subfolder
        self.state = state
        self.state_attr = state_attr
        self.source_files: list[Path] = []
        # Restores the last-used export destination across app restarts,
        # if one was saved and still exists (LocalState.save() is called
        # whenever it changes, in on_select_folder below).
        self.dest_folder: Path | None = None
        if self.state and self.state_attr:
            saved_dir = getattr(self.state, self.state_attr, "")
            if saved_dir and Path(saved_dir).is_dir():
                self.dest_folder = Path(saved_dir)

        layout = QVBoxLayout(self)

        file_group = QGroupBox(f"1. Check the {item_label}(s) to export")
        file_group_layout = QVBoxLayout(file_group)
        self.file_list = QListWidget()
        self.file_list.itemChanged.connect(self.on_file_item_changed)
        file_group_layout.addWidget(self.file_list)

        file_btn_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.on_select_all_toggle)
        self.refresh_btn = QPushButton("Refresh List")
        self.refresh_btn.clicked.connect(self.refresh_file_list)
        self.browse_btn = QPushButton("Browse Manually...")
        self.browse_btn.clicked.connect(self.on_browse_file)
        file_btn_row.addWidget(self.select_all_btn)
        file_btn_row.addWidget(self.refresh_btn)
        file_btn_row.addWidget(self.browse_btn)
        file_group_layout.addLayout(file_btn_row)

        self.file_label = QLabel("No files selected")
        self.file_label.setWordWrap(True)
        file_group_layout.addWidget(self.file_label)
        layout.addWidget(file_group)

        folder_group = QGroupBox("2. Select the folder to export to")
        folder_group_layout = QVBoxLayout(folder_group)
        self.select_folder_btn = QPushButton("Select Export Folder...")
        self.select_folder_btn.clicked.connect(self.on_select_folder)
        folder_group_layout.addWidget(self.select_folder_btn)
        self.folder_label = QLabel(str(self.dest_folder) if self.dest_folder else "No folder selected")
        folder_group_layout.addWidget(self.folder_label)

        self.export_btn = QPushButton(f"Export {item_label.title()}(s)")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.on_export)
        folder_group_layout.addWidget(self.export_btn)
        layout.addWidget(folder_group)

        self.delete_btn = QPushButton(f"Delete Selected {item_label.title()}(s)")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.on_delete)
        # Red for a destructive action, with a clearly muted disabled
        # state so it doesn't read as "still clickable but red" - applied
        # directly on the widget (not the platform-conditional app-level
        # stylesheets elsewhere) since this should look the same everywhere.
        self.delete_btn.setStyleSheet("""
            QPushButton { background-color: #c0392b; color: white; }
            QPushButton:hover:!disabled { background-color: #e74c3c; }
            QPushButton:pressed:!disabled { background-color: #a93226; }
            QPushButton:disabled { background-color: #7f8c8d; color: #dddddd; }
        """)
        layout.addWidget(self.delete_btn)

        layout.addStretch(1)

        self.refresh_file_list()

    def refresh_file_list(self):
        self.file_list.blockSignals(True)
        self.file_list.clear()
        keypad_root = find_connected_keypad_root()
        if keypad_root is None:
            item = QListWidgetItem("No keypad detected - connect it, or use Browse Manually")
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addItem(item)
        else:
            folder = keypad_root / self.keypad_subfolder
            if not folder.is_dir():
                item = QListWidgetItem(f"No {self.keypad_subfolder} folder found on {keypad_root}")
                item.setFlags(Qt.NoItemFlags)
                self.file_list.addItem(item)
            else:
                files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
                if not files:
                    item = QListWidgetItem(f"No files found in {folder}")
                    item.setFlags(Qt.NoItemFlags)
                    self.file_list.addItem(item)
                else:
                    for f in files:
                        item = QListWidgetItem(f.name)
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        item.setCheckState(Qt.Unchecked)
                        item.setData(Qt.UserRole, str(f))
                        self.file_list.addItem(item)
        self.file_list.blockSignals(False)
        self.on_file_item_changed()

    def _checkable_items(self) -> list[QListWidgetItem]:
        return [
            self.file_list.item(i)
            for i in range(self.file_list.count())
            if self.file_list.item(i).flags() & Qt.ItemIsUserCheckable
        ]

    def on_select_all_toggle(self):
        items = self._checkable_items()
        if not items:
            return
        # Toggle: if everything's already checked, this click means
        # "select none" instead.
        all_checked = all(item.checkState() == Qt.Checked for item in items)
        new_state = Qt.Unchecked if all_checked else Qt.Checked
        self.file_list.blockSignals(True)
        for item in items:
            item.setCheckState(new_state)
        self.file_list.blockSignals(False)
        self.on_file_item_changed()

    def _update_select_all_button_text(self):
        items = self._checkable_items()
        all_checked = bool(items) and all(item.checkState() == Qt.Checked for item in items)
        self.select_all_btn.setText("Select None" if all_checked else "Select All")

    def on_file_item_changed(self, _item=None):
        self.source_files = [
            Path(self.file_list.item(i).data(Qt.UserRole))
            for i in range(self.file_list.count())
            if self.file_list.item(i).checkState() == Qt.Checked
        ]
        self._update_file_label()
        self._update_buttons_enabled()
        self._update_select_all_button_text()

    def on_browse_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, f"Select the {self.item_label}(s) to export")
        if not files:
            return
        self.file_list.blockSignals(True)
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked)
        self.file_list.blockSignals(False)
        self._update_select_all_button_text()
        self.source_files = [Path(f) for f in files]
        self._update_file_label()
        self._update_buttons_enabled()

    def _update_file_label(self):
        if not self.source_files:
            self.file_label.setText("No files selected")
        elif len(self.source_files) == 1:
            self.file_label.setText(str(self.source_files[0]))
        else:
            self.file_label.setText(
                f"{len(self.source_files)} files selected:\n"
                + "\n".join(p.name for p in self.source_files)
            )

    def on_select_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Select the export folder")
        if not directory:
            return
        self.dest_folder = Path(directory)
        self.folder_label.setText(str(self.dest_folder))
        if self.state and self.state_attr:
            setattr(self.state, self.state_attr, str(self.dest_folder))
            self.state.save()
        self._update_buttons_enabled()

    def _update_buttons_enabled(self):
        self.export_btn.setEnabled(bool(self.source_files) and self.dest_folder is not None)
        self.delete_btn.setEnabled(bool(self.source_files))

    def on_export(self):
        errors = []
        copied = 0
        for src in self.source_files:
            dest_path = self.dest_folder / src.name
            try:
                shutil.copy2(src, dest_path)
                copied += 1
            except OSError as e:
                errors.append(f"{src.name}: {e}")

        if errors:
            QMessageBox.warning(
                self, "Export finished with errors",
                f"Copied {copied} of {len(self.source_files)} file(s) to {self.dest_folder}.\n\n"
                "Failed:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(
                self, "Export complete",
                f"Copied {copied} file(s) to {self.dest_folder}.",
            )

    def on_delete(self):
        if not self.source_files:
            return
        files_str = "\n".join(p.name for p in self.source_files)
        resp = QMessageBox.warning(
            self, "Confirm delete",
            f"Permanently delete {len(self.source_files)} file(s) from disk?\n\n"
            f"{files_str}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        errors = []
        deleted = 0
        for f in self.source_files:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                errors.append(f"{f.name}: {e}")

        # Rebuilds the list from disk (so deleted files disappear) and
        # resets checkboxes/source_files/button state via its own
        # on_file_item_changed() call at the end.
        self.refresh_file_list()

        if errors:
            QMessageBox.warning(
                self, "Delete finished with errors",
                f"Deleted {deleted} of {deleted + len(errors)} file(s).\n\n"
                "Failed:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(self, "Delete complete", f"Deleted {deleted} file(s).")


class KeypadImportSection(QWidget):
    """The reverse of FileExportTab: pick one or more files from
    anywhere on the PC and copy them onto the connected keypad's
    <keypad_subfolder> directory. Only used for config files - "import"
    doesn't make sense for screenshots, so this isn't reused for the
    Export Screenshots tab the way FileExportTab is."""

    def __init__(self, item_label: str, keypad_subfolder: str, state: "LocalState | None" = None, state_attr: str = ""):
        super().__init__()
        self.item_label = item_label
        self.keypad_subfolder = keypad_subfolder
        self.state = state
        self.state_attr = state_attr
        self.source_files: list[Path] = []
        self.target_folder: Path | None = None

        layout = QVBoxLayout(self)

        files_group = QGroupBox(f"1. Select the {item_label}(s) to import")
        files_group_layout = QVBoxLayout(files_group)
        self.browse_btn = QPushButton(f"Browse for {item_label.title()}(s)...")
        self.browse_btn.clicked.connect(self.on_browse_files)
        files_group_layout.addWidget(self.browse_btn)
        self.files_label = QLabel("No files selected")
        self.files_label.setWordWrap(True)
        files_group_layout.addWidget(self.files_label)
        layout.addWidget(files_group)

        target_group = QGroupBox("2. Target on keypad")
        target_group_layout = QVBoxLayout(target_group)
        target_row = QHBoxLayout()
        self.target_label = QLabel("")
        self.target_label.setWordWrap(True)
        self.refresh_target_btn = QPushButton("Refresh")
        self.refresh_target_btn.clicked.connect(self.refresh_target)
        target_row.addWidget(self.target_label, stretch=1)
        target_row.addWidget(self.refresh_target_btn)
        target_group_layout.addLayout(target_row)

        self.import_btn = QPushButton(f"Import {item_label.title()}(s)")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self.on_import)
        target_group_layout.addWidget(self.import_btn)
        layout.addWidget(target_group)

        layout.addStretch(1)

        self.refresh_target()

    def refresh_target(self):
        keypad_root = find_connected_keypad_root()
        if keypad_root is None:
            self.target_folder = None
            self.target_label.setText("No keypad detected - connect it first")
        else:
            self.target_folder = keypad_root / self.keypad_subfolder
            self.target_label.setText(str(self.target_folder))
        self._update_import_enabled()

    def on_browse_files(self):
        start_dir = ""
        if self.state and self.state_attr:
            start_dir = getattr(self.state, self.state_attr, "")
        files, _ = QFileDialog.getOpenFileNames(
            self, f"Select {self.item_label}(s) to import", start_dir,
        )
        if not files:
            return
        self.source_files = [Path(f) for f in files]
        if len(self.source_files) == 1:
            self.files_label.setText(str(self.source_files[0]))
        else:
            self.files_label.setText(
                f"{len(self.source_files)} files selected:\n"
                + "\n".join(p.name for p in self.source_files)
            )
        # Remembers where you last picked files from, so the dialog
        # opens there again next time instead of some OS default.
        if self.state and self.state_attr:
            setattr(self.state, self.state_attr, str(self.source_files[0].parent))
            self.state.save()
        self._update_import_enabled()

    def _update_import_enabled(self):
        self.import_btn.setEnabled(bool(self.source_files) and self.target_folder is not None)

    def on_import(self):
        # Re-check right before importing rather than trusting a
        # possibly-stale target from whenever the tab was last refreshed -
        # cheap to redo and avoids writing to a folder path that no
        # longer matches a connected keypad.
        self.refresh_target()
        if self.target_folder is None:
            QMessageBox.warning(self, "No keypad detected", "Connect the keypad and try again.")
            return

        self.target_folder.mkdir(parents=True, exist_ok=True)
        errors = []
        copied = 0
        for src in self.source_files:
            dest_path = self.target_folder / src.name
            try:
                shutil.copy2(src, dest_path)
                copied += 1
            except OSError as e:
                errors.append(f"{src.name}: {e}")

        if errors:
            QMessageBox.warning(
                self, "Import finished with errors",
                f"Copied {copied} of {len(self.source_files)} file(s) to {self.target_folder}.\n\n"
                "Failed:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(
                self, "Import complete",
                f"Copied {copied} file(s) to {self.target_folder}.",
            )


class BackupRestoreTab(QWidget):
    """Full backup history for the connected keypad, independent of the
    Update tab's own automatic pre-update backup. Lets you snapshot the
    entire keypad drive on demand, browse every backup ever taken (each
    kept in its own folder - see take_backup()), and restore a chosen
    subset of what's in a chosen backup. This is the "just in case
    something goes badly wrong" net; the Update tab's "Restore Last
    Backup" button covers the common "undo my last update" case without
    needing this tab at all.
    """

    def __init__(self, set_busy):
        super().__init__()
        self.set_busy = set_busy
        self.target_root: Path | None = None
        self.selected_backup: BackupInfo | None = None

        layout = QVBoxLayout(self)

        # --- Take a backup now ---
        backup_group = QGroupBox("Take a full backup now")
        backup_layout = QVBoxLayout(backup_group)

        target_row = QHBoxLayout()
        self.target_label = QLabel("")
        self.target_label.setWordWrap(True)
        self.refresh_target_btn = QPushButton("Refresh")
        self.refresh_target_btn.clicked.connect(self.refresh_target)
        target_row.addWidget(self.target_label, stretch=1)
        target_row.addWidget(self.refresh_target_btn)
        backup_layout.addLayout(target_row)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Label (optional):"))
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("e.g. before shop update")
        label_row.addWidget(self.label_input, stretch=1)
        backup_layout.addLayout(label_row)

        self.backup_now_btn = QPushButton("Back Up Now (entire keypad)")
        self.backup_now_btn.clicked.connect(self.on_backup_now)
        backup_layout.addWidget(self.backup_now_btn)
        layout.addWidget(backup_group)

        # --- Existing backups ---
        list_group = QGroupBox("Existing backups")
        list_layout = QVBoxLayout(list_group)
        self.backup_list = QListWidget()
        self.backup_list.itemSelectionChanged.connect(self.on_backup_selection_changed)
        list_layout.addWidget(self.backup_list)

        list_btn_row = QHBoxLayout()
        self.refresh_backups_btn = QPushButton("Refresh List")
        self.refresh_backups_btn.clicked.connect(self.refresh_backup_list)
        list_btn_row.addWidget(self.refresh_backups_btn)
        self.delete_backup_btn = QPushButton("Delete Selected Backup")
        self.delete_backup_btn.setEnabled(False)
        self.delete_backup_btn.clicked.connect(self.on_delete_backup)
        self.delete_backup_btn.setStyleSheet("""
            QPushButton { background-color: #c0392b; color: white; }
            QPushButton:hover:!disabled { background-color: #e74c3c; }
            QPushButton:pressed:!disabled { background-color: #a93226; }
            QPushButton:disabled { background-color: #7f8c8d; color: #dddddd; }
        """)
        list_btn_row.addWidget(self.delete_backup_btn)
        list_layout.addLayout(list_btn_row)
        layout.addWidget(list_group)

        # --- Restore from selected backup ---
        restore_group = QGroupBox("Restore from selected backup")
        restore_layout = QVBoxLayout(restore_group)
        self.no_selection_label = QLabel("Select a backup above to choose what to restore.")
        restore_layout.addWidget(self.no_selection_label)
        # Populated dynamically per selected backup (on_backup_selection_changed)
        # rather than a fixed set - a backup can contain anything that was
        # actually on the keypad's drive root at the time, not just the
        # folders this app happens to recognize.
        self.item_checks: dict[str, QCheckBox] = {}
        self.checks_layout = QVBoxLayout()
        restore_layout.addLayout(self.checks_layout)
        self.restore_btn = QPushButton("Restore Checked Items to Connected Keypad")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self.on_restore)
        restore_layout.addWidget(self.restore_btn)
        layout.addWidget(restore_group)

        layout.addStretch(1)

        self.refresh_target()
        self.refresh_backup_list()

    # -- helpers ----------------------------------------------------------

    def refresh_target(self):
        self.target_root = find_connected_keypad_root()
        if self.target_root is None:
            self.target_label.setText("No keypad detected - connect it first")
        else:
            self.target_label.setText(str(self.target_root))
        self._update_buttons_enabled()

    def refresh_backup_list(self):
        self.backup_list.blockSignals(True)
        self.backup_list.clear()
        self.backups_by_path: dict[str, BackupInfo] = {}
        backups = list_backups()
        if not backups:
            item = QListWidgetItem("No backups yet")
            item.setFlags(Qt.NoItemFlags)
            self.backup_list.addItem(item)
        else:
            for b in backups:
                item = QListWidgetItem(describe_backup(b))
                item.setData(Qt.UserRole, str(b.path))
                self.backups_by_path[str(b.path)] = b
                self.backup_list.addItem(item)
        self.backup_list.blockSignals(False)
        self.selected_backup = None
        self._rebuild_item_checks()
        self._update_buttons_enabled()

    def on_backup_selection_changed(self):
        selected = self.backup_list.selectedItems()
        path_str = selected[0].data(Qt.UserRole) if selected else None
        self.selected_backup = self.backups_by_path.get(path_str) if path_str else None
        self._rebuild_item_checks()
        self._update_buttons_enabled()

    def _rebuild_item_checks(self):
        while self.checks_layout.count():
            child = self.checks_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.item_checks = {}
        if self.selected_backup is None:
            self.no_selection_label.setVisible(True)
            return
        self.no_selection_label.setVisible(False)
        for name in self.selected_backup.items:
            cb = QCheckBox(name)
            cb.setChecked(True)
            cb.toggled.connect(self._update_buttons_enabled)
            self.checks_layout.addWidget(cb)
            self.item_checks[name] = cb

    def _update_buttons_enabled(self):
        self.backup_now_btn.setEnabled(self.target_root is not None)
        self.delete_backup_btn.setEnabled(self.selected_backup is not None)
        any_checked = any(cb.isChecked() for cb in self.item_checks.values())
        self.restore_btn.setEnabled(
            self.selected_backup is not None and self.target_root is not None and any_checked
        )

    # -- backup -------------------------------------------------------------

    def on_backup_now(self):
        if self.target_root is None:
            return
        self.backup_now_btn.setEnabled(False)
        self.set_busy("backing up the keypad")
        self.backup_thread = BackupThread(self.target_root, self.label_input.text())
        self.backup_thread.log_msg.connect(lambda _msg: None)
        self.backup_thread.finished_ok.connect(self.on_backup_ok)
        self.backup_thread.failed.connect(self.on_backup_failed)
        self.backup_thread.start()

    def on_backup_ok(self, info: "BackupInfo | None"):
        self.backup_now_btn.setEnabled(True)
        self.set_busy(None)
        self.refresh_backup_list()
        if info is None:
            QMessageBox.information(
                self, "Nothing to back up",
                "The connected keypad's drive appears to be empty - nothing was backed up.",
            )
        else:
            self.label_input.clear()
            QMessageBox.information(self, "Backup complete", f"Backed up: {', '.join(info.items)}.")

    def on_backup_failed(self, err: str):
        self.backup_now_btn.setEnabled(True)
        self.set_busy(None)
        QMessageBox.critical(self, "Backup failed", err)

    # -- delete ---------------------------------------------------------

    def on_delete_backup(self):
        if self.selected_backup is None:
            return
        resp = QMessageBox.warning(
            self, "Confirm delete",
            f"Permanently delete this backup?\n\n{describe_backup(self.selected_backup)}\n\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        shutil.rmtree(self.selected_backup.path, ignore_errors=True)
        self.refresh_backup_list()

    # -- restore ----------------------------------------------------------

    def on_restore(self):
        if self.selected_backup is None or self.target_root is None:
            return
        items = [name for name, cb in self.item_checks.items() if cb.isChecked()]
        if not items:
            return
        confirm = QMessageBox.warning(
            self, "Confirm restore",
            f"This will delete the current {', '.join(items)} on\n{self.target_root}\n"
            f"and replace them with this backup:\n\n{describe_backup(self.selected_backup)}"
            "\n\nThis cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.restore_btn.setEnabled(False)
        self.set_busy("restoring a backup to the keypad")
        self.restore_thread = RestoreThread(self.selected_backup, items, self.target_root)
        self.restore_thread.log_msg.connect(lambda _msg: None)
        self.restore_thread.finished_ok.connect(self.on_restore_ok)
        self.restore_thread.failed.connect(self.on_restore_failed)
        self.restore_thread.start()

    def on_restore_ok(self):
        self.set_busy(None)
        self._update_buttons_enabled()
        QMessageBox.information(self, "Restore complete", "The selected items were restored.")

    def on_restore_failed(self, err: str):
        self.set_busy(None)
        self._update_buttons_enabled()
        QMessageBox.critical(self, "Restore failed", err)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AltiKit")
        self.resize(640, 640)

        self.state = LocalState.load()
        self.extract_dir: Path | None = None
        self.target_dir: Path | None = None
        # Set while ApplyThread/EjectThread is actively writing to or
        # ejecting the keypad drive, so self-update (which quits the app,
        # and on Linux/macOS overwrites the running app's own files via
        # git pull) can refuse to run mid-transfer instead of racing it.
        self.keypad_busy_reason: str | None = None

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # "?" and "Check for Updates" live in the tab bar's own corner
        # widget, so they're aligned with the tab row by construction -
        # Qt handles this natively, rather than the fragile manual pixel
        # positioning this app used before switching to tabs (that needed
        # per-platform tuning and still nearly hit the title bar on
        # Windows - see git history if curious).
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 6, 0)
        # Quick-access eject, independent of the Update tab's own "Eject
        # Drive" button (which only appears right after an apply). Lives
        # in the corner so it's reachable from every tab, not just Update
        # Keypad. Enabled only when a drive is selected and nothing else
        # is using it - see update_apply_enabled().
        self.eject_top_btn = QPushButton("⏏")
        self.eject_top_btn.setFixedSize(22, 22)
        self.eject_top_btn.setToolTip("Safely Eject Keypad")
        self.eject_top_btn.setEnabled(False)
        self.eject_top_btn.clicked.connect(self.on_eject_clicked)
        corner_layout.addWidget(self.eject_top_btn)
        self.check_update_btn = QPushButton("Check for Updates")
        self.check_update_btn.clicked.connect(self.on_check_updates_clicked)
        corner_layout.addWidget(self.check_update_btn)
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedSize(22, 22)
        self.help_btn.setToolTip("View README")
        self.help_btn.clicked.connect(self.on_help_clicked)
        corner_layout.addWidget(self.help_btn)
        tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        update_tab = QWidget()
        layout = QVBoxLayout(update_tab)

        # --- Update check group ---
        self.update_box = QGroupBox("1. Get the language package")
        update_box = self.update_box
        update_layout = QVBoxLayout(update_box)

        self.local_version_label = QLabel(self._local_version_text())
        update_layout.addWidget(self.local_version_label)

        # Automatic path - uses a real headless browser (Playwright) since
        # Schneider's site blocks plain HTTP requests to www.se.com.
        self.fetch_btn = QPushButton("Check && Download Latest (auto, via browser)")
        self.fetch_btn.clicked.connect(self.on_fetch_clicked)
        update_layout.addWidget(self.fetch_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0)  # indeterminate - no byte-level progress from Playwright
        update_layout.addWidget(self.progress_bar)

        update_layout.addWidget(QLabel(
            "If that fails (e.g. Playwright/Chromium not installed yet - "
            "see README), do it manually instead:"
        ))

        # Manual path - open the real page in your browser, download the
        # zip yourself, then point the app at it. Also supports pointing
        # straight at an already-unzipped folder if you'd rather extract
        # it yourself.
        manual_row = QHBoxLayout()
        self.open_page_btn = QPushButton("Open Download Page in Browser")
        self.open_page_btn.clicked.connect(self.on_open_page_clicked)
        self.select_zip_btn = QPushButton("Select Downloaded Zip...")
        self.select_zip_btn.clicked.connect(self.on_select_zip_clicked)
        self.select_folder_btn = QPushButton("Select Extracted Folder...")
        self.select_folder_btn.clicked.connect(self.on_select_extracted_folder_clicked)
        manual_row.addWidget(self.open_page_btn)
        manual_row.addWidget(self.select_zip_btn)
        manual_row.addWidget(self.select_folder_btn)
        update_layout.addLayout(manual_row)

        layout.addWidget(update_box)

        # --- Language selection group ---
        lang_box = QGroupBox(f"2. Select languages to install (max {MAX_LANGUAGES}, plus fonts)")
        lang_layout = QVBoxLayout(lang_box)
        self.lang_list = QListWidget()
        self.lang_list.itemChanged.connect(self.on_lang_item_changed)
        lang_layout.addWidget(self.lang_list)
        self.selected_count_label = QLabel("0 selected")
        lang_layout.addWidget(self.selected_count_label)
        layout.addWidget(lang_box)

        # --- Target group ---
        target_box = QGroupBox("3. Select keypad drive")
        target_layout = QVBoxLayout(target_box)

        self.drive_list = QListWidget()
        self.drive_list.setMaximumHeight(100)
        self.drive_list.itemClicked.connect(self.on_drive_item_clicked)
        target_layout.addWidget(self.drive_list)

        drive_btn_row = QHBoxLayout()
        self.refresh_drives_btn = QPushButton("Refresh Detected Drives")
        self.refresh_drives_btn.clicked.connect(self.refresh_detected_drives)
        self.browse_btn = QPushButton("Browse Manually...")
        self.browse_btn.clicked.connect(self.on_browse_clicked)
        drive_btn_row.addWidget(self.refresh_drives_btn)
        drive_btn_row.addWidget(self.browse_btn)
        target_layout.addLayout(drive_btn_row)

        self.target_label = QLabel("No folder selected")
        target_layout.addWidget(self.target_label)

        layout.addWidget(target_box)

        # --- Apply group ---
        apply_box = QGroupBox("4. Apply")
        apply_layout = QVBoxLayout(apply_box)
        self.backup_checkbox = QCheckBox("Back up entire keypad before updating (LANG/KPCONF/DRVCONF/PRTSCR)")
        self.backup_checkbox.setChecked(True)
        apply_layout.addWidget(self.backup_checkbox)
        backup_label_row = QHBoxLayout()
        backup_label_row.addWidget(QLabel("Backup label (optional):"))
        self.backup_label_input = QLineEdit()
        self.backup_label_input.setPlaceholderText("before update")
        backup_label_row.addWidget(self.backup_label_input, stretch=1)
        apply_layout.addLayout(backup_label_row)
        self.auto_eject_checkbox = QCheckBox("Automatically eject after updating")
        apply_layout.addWidget(self.auto_eject_checkbox)
        self.apply_btn = QPushButton("Apply Update to Keypad")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.on_apply_clicked)
        apply_layout.addWidget(self.apply_btn)
        self.apply_progress_bar = QProgressBar()
        self.apply_progress_bar.setVisible(False)
        apply_layout.addWidget(self.apply_progress_bar)
        self.eject_btn = QPushButton("Eject Drive")
        self.eject_btn.setVisible(False)
        self.eject_btn.clicked.connect(self.on_eject_clicked)
        apply_layout.addWidget(self.eject_btn)
        # Undoes exactly what "Apply Update" just changed (LANG/KPCONF
        # only) using the most recent backup for this drive - the Backup
        # & Restore tab covers everything else (DRVCONF/PRTSCR, older
        # backups, picking specific folders).
        self.restore_last_btn = QPushButton("Restore Last Backup (undo update)")
        self.restore_last_btn.setEnabled(False)
        self.restore_last_btn.clicked.connect(self.on_restore_last_clicked)
        apply_layout.addWidget(self.restore_last_btn)
        layout.addWidget(apply_box)

        # --- Log ---
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)

        tabs.addTab(update_tab, "Update Keypad")

        config_tab = QWidget()
        config_layout = QVBoxLayout(config_tab)
        export_group = QGroupBox("Export from Keypad")
        export_group_layout = QVBoxLayout(export_group)
        export_group_layout.addWidget(
            FileExportTab("configuration file", "DRVCONF", self.state, "config_export_dir")
        )
        config_layout.addWidget(export_group)
        import_group = QGroupBox("Import to Keypad")
        import_group_layout = QVBoxLayout(import_group)
        import_group_layout.addWidget(
            KeypadImportSection("configuration file", "DRVCONF", self.state, "config_import_browse_dir")
        )
        config_layout.addWidget(import_group)
        tabs.addTab(config_tab, "Config Files")

        tabs.addTab(
            FileExportTab("screenshot", "PRTSCR", self.state, "screenshot_export_dir"),
            "Export Screenshots",
        )

        tabs.addTab(BackupRestoreTab(self._set_keypad_busy), "Backup && Restore")

        help_tab = QWidget()
        help_layout = QVBoxLayout(help_tab)
        help_layout.addWidget(QLabel(f"Version: {APP_VERSION}"))
        help_view = QTextBrowser()
        help_view.setReadOnly(True)
        help_view.setOpenExternalLinks(True)
        if HELP_PATH.is_file():
            help_view.setMarkdown(HELP_PATH.read_text(encoding="utf-8"))
        else:
            help_view.setPlainText(f"Couldn't find HELP.md next to the script at {HELP_PATH}.")
        help_layout.addWidget(help_view)
        help_index = tabs.addTab(help_tab, "Help")
        # Hidden from the tab bar itself - "?" is the only way in, so
        # there's exactly one way to reach help, not two. Still a real
        # tab underneath (setCurrentWidget below just works), not a
        # popup window.
        tabs.setTabVisible(help_index, False)

        self.tabs = tabs
        self.help_tab = help_tab

        self._populate_languages_from_cache()
        self.refresh_detected_drives()
        self._auto_check_for_updates()

    # -- helpers --------------------------------------------------------

    def log(self, msg: str):
        self.log_view.append(msg)

    def _set_keypad_busy(self, reason: str | None):
        # Passed into BackupRestoreTab so its own backup/restore actions
        # gate self-update the same way Apply/Eject on this tab already do.
        self.keypad_busy_reason = reason
        self.update_apply_enabled()

    def _local_version_text(self) -> str:
        if self.state.installed_version:
            return f"Last downloaded version: V{self.state.installed_version}"
        return "Last downloaded version: none yet"

    def _populate_languages_from_cache(self):
        if self.state.extracted_dir and Path(self.state.extracted_dir).is_dir():
            self.extract_dir = Path(self.state.extracted_dir)
            self._populate_language_list(self.extract_dir)

    def _populate_language_list(self, extract_dir: Path):
        previously_selected = set(self.state.selected_languages)
        self.lang_list.blockSignals(True)
        self.lang_list.clear()
        for code, name, _path in list_available_languages(extract_dir):
            item = QListWidgetItem(f"{name} ({code})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if code in previously_selected else Qt.Unchecked)
            item.setData(Qt.UserRole, code)
            self.lang_list.addItem(item)
        self.lang_list.blockSignals(False)
        self.update_selected_count()

    def selected_codes(self) -> list[str]:
        codes = []
        for i in range(self.lang_list.count()):
            item = self.lang_list.item(i)
            if item.checkState() == Qt.Checked:
                codes.append(item.data(Qt.UserRole))
        return codes

    def update_selected_count(self):
        n = len(self.selected_codes())
        self.selected_count_label.setText(f"{n} selected (max {MAX_LANGUAGES})")
        self.selected_count_label.setStyleSheet(
            "color: red;" if n > MAX_LANGUAGES else ""
        )
        self.update_apply_enabled()

    def update_apply_enabled(self):
        n = len(self.selected_codes())
        not_busy = self.keypad_busy_reason is None
        ok = (
            self.extract_dir is not None
            and self.target_dir is not None
            and 0 < n <= MAX_LANGUAGES
            and not_busy
        )
        self.apply_btn.setEnabled(ok)
        self.restore_last_btn.setEnabled(self.target_dir is not None and not_busy)
        self.eject_top_btn.setEnabled(self.target_dir is not None and not_busy)

    # -- slots ------------------------------------------------------------

    def on_lang_item_changed(self, _item):
        self.update_selected_count()
        self.state.selected_languages = self.selected_codes()
        self.state.save()

    def on_fetch_clicked(self):
        self.fetch_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log("Launching headless browser to fetch the language package...")
        self.fetch_thread = BrowserFetchThread()
        self.fetch_thread.log_msg.connect(self.log)
        self.fetch_thread.finished_ok.connect(self.on_fetch_ok)
        self.fetch_thread.failed.connect(self.on_fetch_failed)
        self.fetch_thread.start()

    def on_fetch_ok(self, version: str, zip_path: Path):
        self.progress_bar.setVisible(False)
        self.fetch_btn.setEnabled(True)
        if version == self.state.installed_version:
            self.log(f"Already have the latest version (V{version}) downloaded.")
            resp = QMessageBox.question(
                self, "Already up to date",
                f"You already have the latest language pack (V{version}) "
                "downloaded and installed.\n\nRe-download/re-extract it anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        self._finalize_new_package(Path(zip_path), version)

    def on_fetch_failed(self, err: str):
        self.progress_bar.setVisible(False)
        self.fetch_btn.setEnabled(True)
        self.log(f"ERROR fetching update: {err}")
        QMessageBox.warning(
            self, "Auto-fetch failed",
            err + "\n\nUse 'Open Download Page in Browser' + 'Select "
            "Downloaded Zip...' below instead."
        )

    def on_help_clicked(self):
        # The "?" button's placement (tab-bar corner) stays put, but it
        # now switches to the Help tab instead of opening a separate
        # dialog/window.
        self.tabs.setCurrentWidget(self.help_tab)

    def on_check_updates_clicked(self):
        if self.keypad_busy_reason:
            QMessageBox.warning(
                self, "Keypad busy",
                f"Can't check for app updates while {self.keypad_busy_reason} - "
                "wait for it to finish first.",
            )
            return
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("Checking...")
        self.update_check_thread = UpdateCheckThread()
        self.update_check_thread.finished_ok.connect(self._handle_app_update_check_result)
        self.update_check_thread.start()

    def _handle_app_update_check_result(self, result: dict, silent: bool = False):
        # silent=True is the launch-time auto-check: still notify and ask
        # when an update IS available (that's the whole point), but don't
        # pop up ambient "you're up to date"/"check failed" noise on every
        # startup the way the explicit "Check for Updates" button does.
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("Check for Updates")
        status = result.get("status")
        if status == "update_available":
            if APP_VERSION == "dev":
                # A dev checkout (no VERSION.txt) always parses as
                # version 0, so this branch fires on every real
                # release - confirming the check itself still works
                # without offering to actually self-update, which
                # would git-pull/restart a working dev checkout.
                if not silent:
                    QMessageBox.information(
                        self, "Update available (dev build)",
                        f"A new version ({result['latest_version']}) is "
                        "available - auto-update is skipped on dev builds.",
                    )
            else:
                resp = QMessageBox.question(
                    self, "Update available",
                    f"A new version ({result['latest_version']}) is available "
                    f"(you have {APP_VERSION}).\n\nDownload and install it now?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if resp == QMessageBox.Yes:
                    self.start_self_update(result)
        elif status == "up_to_date":
            if not silent:
                QMessageBox.information(
                    self, "Up to date",
                    f"You're on the latest version ({result['latest_version']}).",
                )
        else:
            if silent:
                self.log(f"Auto-check for app updates failed: {result.get('message', 'Unknown error')}")
            else:
                QMessageBox.warning(self, "Check failed", result.get("message", "Unknown error"))

    def _auto_check_for_updates(self):
        """Runs both update checks in the background on launch. The app
        update check is a cheap plain HTTP GET, so it runs every time;
        the language-pack check needs a real headless Chromium load (no
        lightweight API for Schneider's site), so it's throttled to once
        a day via LocalState.last_lang_check. Both are silent unless
        something is actually available to install."""
        self.auto_update_check_thread = UpdateCheckThread()
        self.auto_update_check_thread.finished_ok.connect(
            lambda result: self._handle_app_update_check_result(result, silent=True)
        )
        self.auto_update_check_thread.start()

        if self._lang_check_due():
            self.auto_lang_check_thread = LanguageCheckThread()
            self.auto_lang_check_thread.log_msg.connect(self.log)
            self.auto_lang_check_thread.finished_ok.connect(self._on_auto_lang_check_ok)
            self.auto_lang_check_thread.failed.connect(self._on_auto_lang_check_failed)
            self.auto_lang_check_thread.start()

    def _lang_check_due(self) -> bool:
        if not self.state.last_lang_check:
            return True
        try:
            last = datetime.fromisoformat(self.state.last_lang_check)
        except ValueError:
            return True
        return datetime.now() - last >= timedelta(days=1)

    def _on_auto_lang_check_ok(self, info: RemotePackageInfo):
        # Only stamp last_lang_check on success - if it failed (offline,
        # Chromium not installed yet, etc.) leave it blank so the next
        # launch retries instead of waiting out the rest of the day.
        self.state.last_lang_check = datetime.now().isoformat()
        self.state.save()
        if _parse_version(info.version) <= _parse_version(self.state.installed_version):
            self.log(f"Language pack is up to date (V{info.version}).")
            return
        have = f"V{self.state.installed_version}" if self.state.installed_version else "none yet"
        resp = QMessageBox.question(
            self, "Language pack update available",
            f"A new language pack (V{info.version}) is available (you have {have}).\n\n"
            "Download it now?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self.on_fetch_clicked()

    def _on_auto_lang_check_failed(self, err: str):
        self.log(f"Auto-check for language pack updates failed: {err}")

    def start_self_update(self, update_result: dict):
        # Re-checked here (not just in on_check_updates_clicked) since a
        # keypad transfer can start in the time between that click and
        # the user answering the "update available?" prompt.
        if self.keypad_busy_reason:
            QMessageBox.warning(
                self, "Keypad busy",
                f"Can't update AltiKit while {self.keypad_busy_reason} - "
                "wait for it to finish first.",
            )
            return
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("Updating...")
        self.log(f"Updating to {update_result['latest_version']}...")
        self.self_update_thread = SelfUpdateThread(update_result)
        self.self_update_thread.log_msg.connect(self.log)
        self.self_update_thread.finished_ok.connect(self.on_self_update_ok)
        self.self_update_thread.failed.connect(self.on_self_update_failed)
        self.self_update_thread.start()

    def on_self_update_ok(self):
        self.log("Update launched - closing so it can finish...")
        QApplication.quit()

    def on_self_update_failed(self, err: str):
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("Check for Updates")
        self.log(f"ERROR updating: {err}")
        QMessageBox.critical(
            self, "Update failed",
            f"{err}\n\nYou can still update manually - see the Releases "
            f"page: https://github.com/{GITHUB_REPO}/releases",
        )

    def on_open_page_clicked(self):
        webbrowser.open(SE_DOWNLOAD_PAGE)
        self.log(
            f"Opened {SE_DOWNLOAD_PAGE} in your browser - download the "
            "zip, then click 'Select Downloaded Zip...' below."
        )

    def on_select_zip_clicked(self):
        downloads_dir = Path.home() / "Downloads"
        start_dir = str(downloads_dir) if downloads_dir.is_dir() else str(Path.home())
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select the downloaded language package zip", start_dir,
            "Zip files (*.zip)"
        )
        if not file_path:
            return
        src = Path(file_path)
        version = parse_version_from_filename(src.name)
        if version == "unknown":
            self.log(
                f"WARNING: couldn't parse a version number from "
                f"'{src.name}' - proceeding anyway, but version tracking "
                "won't be accurate for this import."
            )

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached_path = CACHE_DIR / src.name
        if src.resolve() != cached_path.resolve():
            shutil.copy2(src, cached_path)
        self.log(f"Imported {cached_path} (version {version})")
        self._finalize_new_package(cached_path, version)

    def on_select_extracted_folder_clicked(self):
        """For people who'd rather unzip it themselves - skips our
        extraction step and uses the folder directly, as long as it
        contains LANG and KPCONF subfolders."""
        downloads_dir = Path.home() / "Downloads"
        start_dir = str(downloads_dir) if downloads_dir.is_dir() else str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self, "Select the already-extracted language package folder", start_dir
        )
        if not directory:
            return
        folder = Path(directory)
        if find_subdir(folder, "LANG") is None or find_subdir(folder, "KPCONF") is None:
            QMessageBox.warning(
                self, "Unexpected folder",
                f"{folder} doesn't contain LANG and KPCONF subfolders, so "
                "it doesn't look like an extracted language package. "
                "Point this at the folder you unzipped, not a parent or "
                "sub-folder of it."
            )
            return
        version = parse_version_from_filename(folder.name)
        self.extract_dir = folder
        self.state.installed_version = version
        self.state.zip_path = ""
        self.state.extracted_dir = str(folder)
        self.state.save()
        self.local_version_label.setText(self._local_version_text())
        self.log(f"Using already-extracted folder {folder} (version {version})")
        self._populate_language_list(folder)
        self.update_apply_enabled()

    def _finalize_new_package(self, zip_path: Path, version: str):
        try:
            self.extract_dir = extract_package(zip_path)
            self.log(f"Extracted to {self.extract_dir}")
        except Exception as e:
            self.log(f"ERROR extracting package: {e}")
            QMessageBox.warning(self, "Extraction failed", str(e))
            return

        self.state.installed_version = version
        self.state.zip_path = str(zip_path)
        self.state.extracted_dir = str(self.extract_dir)
        self.state.save()
        self.local_version_label.setText(self._local_version_text())

        self._populate_language_list(self.extract_dir)
        self.update_apply_enabled()

    def refresh_detected_drives(self):
        self.drive_list.clear()
        candidates = detect_candidate_drives()
        if not candidates:
            item = QListWidgetItem("No removable drives detected - use Browse Manually")
            item.setFlags(Qt.NoItemFlags)
            self.drive_list.addItem(item)
            return
        for path, is_keypad in candidates:
            label = f"{'✓ ' if is_keypad else ''}{path}"
            if is_keypad:
                label += "  (looks like keypad)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            self.drive_list.addItem(item)

    def on_drive_item_clicked(self, item: QListWidgetItem):
        path_str = item.data(Qt.UserRole)
        if not path_str:
            return
        self._set_target(Path(path_str))

    def on_browse_clicked(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select the keypad's drive/folder"
        )
        if not directory:
            return
        self._set_target(Path(directory))

    def _set_target(self, target: Path):
        self.target_dir = target
        self.target_label.setText(str(target))
        self.eject_btn.setVisible(False)
        if looks_like_keypad_drive(target):
            self.log(f"Selected target {target} (looks like a valid keypad drive)")
        else:
            self.log(
                f"WARNING: {target} does not contain LANG or KPCONF - "
                "double check this is really the keypad before applying."
            )
            QMessageBox.warning(
                self,
                "Unexpected folder",
                "This folder doesn't contain a LANG or KPCONF folder, so it "
                "doesn't look like a VW3A1111/VW3A1121 drive. Make sure "
                "you've selected the keypad's root drive, not a subfolder.",
            )
        self.update_apply_enabled()

    def on_apply_clicked(self):
        codes = self.selected_codes()
        if not self.extract_dir or not self.target_dir:
            return

        confirm = QMessageBox.question(
            self,
            "Confirm update",
            f"This will delete the existing LANG and KPCONF folders on\n"
            f"{self.target_dir}\nand replace them with {len(codes)} "
            f"selected language(s) + fonts.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.apply_progress_bar.setRange(0, 0)  # indeterminate until the thread reports the real step count
        self.apply_progress_bar.setValue(0)
        self.apply_progress_bar.setVisible(True)
        self.eject_btn.setVisible(False)
        self.log("Starting transfer - the app will stay responsive; "
                  "watch the log below for per-step timing.")
        self.keypad_busy_reason = "updating the keypad's language files"
        self.update_apply_enabled()
        self.apply_thread = ApplyThread(
            self.extract_dir, codes, self.target_dir,
            self.backup_checkbox.isChecked(),
            backup_label=self.backup_label_input.text().strip() or "before update",
        )
        self.apply_thread.log_msg.connect(self.log)
        self.apply_thread.progress.connect(self.on_apply_progress)
        self.apply_thread.finished_ok.connect(self.on_apply_ok)
        self.apply_thread.failed.connect(self.on_apply_failed)
        self.apply_thread.start()

    def on_apply_progress(self, done: int, total: int):
        self.apply_progress_bar.setRange(0, total)
        self.apply_progress_bar.setValue(done)

    def on_apply_ok(self):
        self.apply_progress_bar.setVisible(False)
        self.eject_btn.setVisible(True)
        if self.auto_eject_checkbox.isChecked():
            # keypad_busy_reason stays set (on_eject_clicked immediately
            # overwrites it with its own reason) so apply/restore can't be
            # re-enabled in the gap before ejecting starts.
            self.log("Auto-eject enabled - ejecting now...")
            self.on_eject_clicked()
        else:
            self.keypad_busy_reason = None
            self.update_apply_enabled()
            QMessageBox.information(
                self, "Done", "Language files updated. Click 'Eject Drive' "
                "below, then reconnect the keypad to the drive."
            )

    def on_apply_failed(self, err: str):
        self.apply_progress_bar.setVisible(False)
        self.keypad_busy_reason = None
        self.update_apply_enabled()
        self.log(f"ERROR applying update: {err}")
        QMessageBox.critical(self, "Update failed", err)

    def on_eject_clicked(self):
        self.eject_btn.setEnabled(False)
        self.keypad_busy_reason = "ejecting the keypad drive"
        self.update_apply_enabled()
        self.log(f"Ejecting {self.target_dir}...")
        self.eject_thread = EjectThread(self.target_dir)
        self.eject_thread.log_msg.connect(self.log)
        self.eject_thread.finished_ok.connect(self.on_eject_ok)
        self.eject_thread.failed.connect(self.on_eject_failed)
        self.eject_thread.start()

    def on_eject_ok(self):
        self.eject_btn.setVisible(False)
        self.eject_btn.setEnabled(True)
        self.keypad_busy_reason = None
        self.target_dir = None
        self.target_label.setText("No folder selected")
        self.update_apply_enabled()
        self.refresh_detected_drives()
        QMessageBox.information(self, "Ejected", "Safe to unplug the keypad now.")

    def on_eject_failed(self, err: str):
        self.eject_btn.setEnabled(True)
        self.keypad_busy_reason = None
        self.update_apply_enabled()
        self.log(f"ERROR ejecting drive: {err}")
        QMessageBox.critical(self, "Eject failed", err)

    def on_restore_last_clicked(self):
        if not self.target_dir:
            return
        drive_id = _drive_identifier(self.target_dir)
        backup = find_latest_backup_for_drive(drive_id, ["LANG", "KPCONF"])
        if backup is None:
            QMessageBox.information(
                self, "No backup found",
                "No backup containing LANG/KPCONF was found for this drive yet - "
                "backups are only made when 'Back up entire keypad' is checked "
                "during an update, or from the Backup & Restore tab.",
            )
            return
        confirm = QMessageBox.warning(
            self, "Confirm restore",
            f"This will delete the current LANG and KPCONF folders on\n{self.target_dir}\n"
            f"and replace them with this backup:\n\n{describe_backup(backup)}"
            "\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.keypad_busy_reason = "restoring a backup to the keypad"
        self.update_apply_enabled()
        self.log(f"Restoring LANG/KPCONF from backup: {describe_backup(backup)}")
        self.restore_last_thread = RestoreThread(backup, ["LANG", "KPCONF"], self.target_dir)
        self.restore_last_thread.log_msg.connect(self.log)
        self.restore_last_thread.finished_ok.connect(self.on_restore_last_ok)
        self.restore_last_thread.failed.connect(self.on_restore_last_failed)
        self.restore_last_thread.start()

    def on_restore_last_ok(self):
        self.keypad_busy_reason = None
        self.update_apply_enabled()
        QMessageBox.information(self, "Restore complete", "LANG and KPCONF were restored from backup.")

    def on_restore_last_failed(self, err: str):
        self.keypad_busy_reason = None
        self.update_apply_enabled()
        self.log(f"ERROR restoring backup: {err}")
        QMessageBox.critical(self, "Restore failed", err)


def main():
    app = QApplication(sys.argv)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    if sys.platform == "win32":
        # Windows' native theme renders QProgressBar as a barely-different
        # shade of grey against the app's own grey background, making the
        # two apply/eject progress bars hard to read - give just those a
        # visible fill/border. Also adds a colored section-box border
        # (Windows' native style doesn't draw one at all with any accent,
        # unlike Linux's native style) using the same brand green as the
        # progress bar fill, so the two match. Everything else keeps the
        # native Windows look, and none of this runs on Linux/macOS.
        app.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #888888;
                border-radius: 3px;
                text-align: center;
                background-color: #e0e0e0;
                color: black;
            }}
            QProgressBar::chunk {{
                background-color: {SCHNEIDER_GREEN};
            }}
            QGroupBox {{
                border: 1px solid {SCHNEIDER_GREEN};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 6px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
        """)
    elif sys.platform.startswith("linux"):
        # Some Linux desktop themes don't draw a visible border around
        # QGroupBox (just the bold title), unlike Windows' native style.
        # Add a theme-adaptive border using the same accent color the
        # native style already paints progress bars with (palette(highlight)
        # is what KDE/Breeze sets from "accent color from wallpaper", and
        # what its style engine draws progress bar chunks and other
        # accented elements with) - so the border matches automatically
        # instead of using an unrelated fixed/neutral color. (palette(mid)
        # was tried first but isn't reliably distinct from the window
        # background - confirmed some palettes define it *darker* than
        # the background, making the border invisible.)
        # Also rounds the two list "selection fields" and the log output
        # field - Qt only actually rounds a widget's corners once it has
        # an explicit border to apply the curve to (confirmed via a
        # render test: border-radius alone left the frame square).
        app.setStyleSheet("""
            QGroupBox {
                border: 1px solid palette(highlight);
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QListWidget, QTextEdit {
                border: 1px solid palette(light);
                border-radius: 6px;
            }
        """)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
