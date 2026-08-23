#!/usr/bin/env python3
"""
VW3A1111 / VW3A1121 Graphic Display Terminal - Language File Updater

Checks Schneider Electric's public download page for the current language
package version, downloads and extracts it, lets you pick which languages
to install (device limit: 10 languages + fonts = 11 files), and copies
them onto a keypad connected as a USB mass-storage drive.

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
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
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

APP_DIR = Path.home() / ".vw3a_lang_updater"
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


SE_HOMEPAGE = "https://www.se.com/us/en/"

# NOTE: fetching / downloading from se.com uses a real headless Chromium
# via Playwright, not the `requests` library. Confirmed by testing: even
# with a full set of browser-matching headers (User-Agent, Accept,
# Sec-Fetch-*, a homepage warm-up request for cookies/Referer), plain
# `requests` calls to www.se.com get a 403 Forbidden that a real browser
# never sees - almost certainly TLS/JA3-level fingerprinting rather than
# anything header-based, which only an actual browser network stack can
# pass. Playwright is a genuine Chromium instance, so it does.


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
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise RuntimeError(
                "Couldn't launch the headless Chromium browser. If this "
                "is the first run, you likely need: playwright install "
                f"chromium\n\nOriginal error: {e}"
            ) from e

        try:
            page = browser.new_page()
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
            # ourselves (Schneider has changed both before - the domain
            # moved from download.schneider-electric.com to download.se.com,
            # and p_enDocType's value has changed too), scrape the actual
            # download link straight out of the page. It's the file name
            # (which embeds the version, e.g. "..._U1.78.zip") that we
            # actually need; everything else about the URL is just
            # whatever Schneider currently uses to serve it.
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

            dest_path = dest_dir / zip_filename
            if dest_path.exists() and dest_path.stat().st_size > 0:
                log(f"Already have {zip_filename} cached, skipping download.")
                return version, dest_path

            log(f"Found V{version} ({zip_filename}), downloading...")
            with page.expect_download(timeout=60000) as download_info:
                try:
                    page.goto(download_url)
                except Exception:
                    # Navigating to a file that triggers a download always
                    # raises a navigation error in Playwright even on
                    # success - the download itself is captured separately
                    # below, so this is expected and safe to ignore.
                    pass
            download = download_info.value
            download.save_as(str(dest_path))
            log(f"Downloaded to {dest_path}")
            return version, dest_path
        finally:
            browser.close()


def extract_package(zip_path: Path) -> Path:
    """Extract into CACHE_DIR/<version-named-subdir>, returns that dir."""
    extract_dir = CACHE_DIR / zip_path.stem
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
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


def apply_update(
    extract_dir: Path,
    selected_codes: list[str],
    target_root: Path,
    make_backup: bool = True,
    log=lambda msg: None,
    progress=lambda done, total: None,
) -> None:
    """Deletes LANG+KPCONF on target and copies the new ones over,
    filtering LANG down to the selected languages + Fonts.ums.

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

    backup_targets = [
        (existing, label)
        for existing, label in ((dst_lang, "LANG"), (dst_kpconf, "KPCONF"))
        if make_backup and existing.is_dir()
    ]
    delete_targets = [existing for existing in (dst_lang, dst_kpconf) if existing.is_dir()]
    fonts_src = src_lang / "Fonts.ums"
    lang_files = [
        (code, src_lang / f"{code}_labels.ums")
        for code in selected_codes
        if (src_lang / f"{code}_labels.ums").exists()
    ]
    total_steps = (
        len(backup_targets) + len(delete_targets)
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

    if backup_targets:
        backup_root = APP_DIR / "backups" / target_root.name
        backup_root.mkdir(parents=True, exist_ok=True)
        for existing, label in backup_targets:
            backup_dest = backup_root / label
            if backup_dest.exists():
                shutil.rmtree(backup_dest)
            log(f"Backing up existing {label}...")
            timed_step(f"backup {label}", lambda e=existing, b=backup_dest: shutil.copytree(e, b))
            log(f"Backed up existing {label} to {backup_dest}")

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

    def __init__(self, extract_dir, selected_codes, target_root, make_backup):
        super().__init__()
        self.extract_dir = extract_dir
        self.selected_codes = selected_codes
        self.target_root = target_root
        self.make_backup = make_backup

    def run(self):
        try:
            apply_update(
                self.extract_dir,
                self.selected_codes,
                self.target_root,
                make_backup=self.make_backup,
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VW3A1111 / VW3A1121 Language File Updater")
        self.resize(640, 640)

        self.state = LocalState.load()
        self.extract_dir: Path | None = None
        self.target_dir: Path | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        # Extra top margin so the floating help button (see below) always
        # has clear space above the "1. ..." box regardless of platform -
        # trying to pixel-align it flush with the box's own border/title
        # text turned out to need more headroom than some platforms'
        # default margins leave, causing it to hit the window's title bar.
        margins = layout.contentsMargins()
        layout.setContentsMargins(margins.left(), 30, margins.right(), margins.bottom())

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

        # Help button - floats over the top-right corner rather than living
        # in the normal layout, so it can be aligned to the bottom of the
        # "1. ..." title text (part of the group box's border, not
        # something a regular layout row can align against).
        self.help_btn = QPushButton("?", central)
        self.help_btn.setFixedSize(22, 22)
        self.help_btn.setToolTip("View README")
        self.help_btn.clicked.connect(self.on_help_clicked)
        self._position_help_button()

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
        self.backup_checkbox = QCheckBox("Back up existing LANG/KPCONF before replacing")
        self.backup_checkbox.setChecked(True)
        apply_layout.addWidget(self.backup_checkbox)
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
        layout.addWidget(apply_box)

        # --- Log ---
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)

        self._populate_languages_from_cache()
        self.refresh_detected_drives()
        # The initial help-button placement above runs before the layout is
        # fully activated, so its geometry is stale; re-run once the event
        # loop has processed the pending layout (a plain resizeEvent isn't
        # guaranteed to fire if the window's final size already matches its
        # size hint).
        QTimer.singleShot(0, self._position_help_button)

    # -- helpers --------------------------------------------------------

    def _position_help_button(self):
        """Places the floating help button in the top-right corner, a
        small fixed margin down from the window top. A previous version
        tried to pixel-align its bottom edge with the "1. ..." box's own
        border/title text using the style's reported label geometry, but
        that geometry leaves different amounts of headroom per platform -
        on Windows there wasn't enough room for the button's full height
        without it poking into the title bar. A fixed offset (paired with
        the extra top margin reserved on the main layout) is more robust
        than chasing per-platform/style pixel offsets."""
        x = self.centralWidget().width() - self.help_btn.width() - 12
        y = 6
        self.help_btn.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "help_btn"):
            self._position_help_button()

    def log(self, msg: str):
        self.log_view.append(msg)

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
        ok = (
            self.extract_dir is not None
            and self.target_dir is not None
            and 0 < n <= MAX_LANGUAGES
        )
        self.apply_btn.setEnabled(ok)

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
        if not README_PATH.is_file():
            QMessageBox.warning(
                self, "README not found",
                f"Couldn't find README.md next to the script at "
                f"{README_PATH}."
            )
            return
        text = README_PATH.read_text(encoding="utf-8")

        dialog = QDialog(self)
        dialog.setWindowTitle("README")
        dialog.resize(700, 600)
        dialog_layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setMarkdown(text)
        dialog_layout.addWidget(view)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dialog_layout.addWidget(close_btn)
        dialog.exec()

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

        self.apply_btn.setEnabled(False)
        self.apply_progress_bar.setRange(0, 0)  # indeterminate until the thread reports the real step count
        self.apply_progress_bar.setValue(0)
        self.apply_progress_bar.setVisible(True)
        self.eject_btn.setVisible(False)
        self.log("Starting transfer - the app will stay responsive; "
                  "watch the log below for per-step timing.")
        self.apply_thread = ApplyThread(
            self.extract_dir, codes, self.target_dir,
            self.backup_checkbox.isChecked(),
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
        self.apply_btn.setEnabled(True)
        self.eject_btn.setVisible(True)
        if self.auto_eject_checkbox.isChecked():
            self.log("Auto-eject enabled - ejecting now...")
            self.on_eject_clicked()
        else:
            QMessageBox.information(
                self, "Done", "Language files updated. Click 'Eject Drive' "
                "below, then reconnect the keypad to the drive."
            )

    def on_apply_failed(self, err: str):
        self.apply_progress_bar.setVisible(False)
        self.apply_btn.setEnabled(True)
        self.log(f"ERROR applying update: {err}")
        QMessageBox.critical(self, "Update failed", err)

    def on_eject_clicked(self):
        self.eject_btn.setEnabled(False)
        self.log(f"Ejecting {self.target_dir}...")
        self.eject_thread = EjectThread(self.target_dir)
        self.eject_thread.log_msg.connect(self.log)
        self.eject_thread.finished_ok.connect(self.on_eject_ok)
        self.eject_thread.failed.connect(self.on_eject_failed)
        self.eject_thread.start()

    def on_eject_ok(self):
        self.eject_btn.setVisible(False)
        self.eject_btn.setEnabled(True)
        self.target_dir = None
        self.target_label.setText("No folder selected")
        self.update_apply_enabled()
        self.refresh_detected_drives()
        QMessageBox.information(self, "Ejected", "Safe to unplug the keypad now.")

    def on_eject_failed(self, err: str):
        self.eject_btn.setEnabled(True)
        self.log(f"ERROR ejecting drive: {err}")
        QMessageBox.critical(self, "Eject failed", err)


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
