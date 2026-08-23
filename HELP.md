# AltiKit Help

A utility for Schneider Electric VW3A1111 / VW3A1121 Graphic Display
Terminal keypads: update language files, and export/import VFD
configuration files and screenshots saved on the keypad.

## Update Keypad tab

1. **Get the language package** - "Check && Download Latest" fetches
   the current version straight from Schneider's site. If that fails
   (e.g. first run before Chromium is set up), use "Open Download Page
   in Browser" to grab it yourself, then "Select Downloaded Zip..." or
   "Select Extracted Folder...".
2. **Select languages** - check up to 10 (English is pre-checked -
   Schneider recommends always keeping it installed). `Fonts.ums` is
   always included automatically.
3. **Select keypad drive** - plug in the keypad; connected drives that
   look like a keypad (contain `LANG`/`KPCONF`) are flagged
   automatically. Hit "Refresh Detected Drives" if you plugged it in
   after opening the app, or "Browse Manually..." if auto-detection
   misses it.
4. **Apply Update** - back up existing files first (on by default).
   Once done, click "Eject Drive" (or check "Automatically eject
   after updating" beforehand) before unplugging.

Note: the keypad's other settings (e.g. wheel sensitivity) get reset
by Schneider's own update process after a language change - you may
need to reconfigure those separately.

## Config Files tab

- **Export from Keypad** - lists files found in the keypad's `DRVCONF`
  folder. Check the ones you want (or "Select All"), pick a
  destination folder, and export. "Delete Selected" permanently
  removes the checked files from the keypad after confirming.
- **Import to Keypad** - browse to one or more configuration files
  anywhere on your PC and copy them onto the connected keypad's
  `DRVCONF` folder directly.

## Export Screenshots tab

Same as the Config Files tab's export side, for the keypad's `PRTSCR`
folder - select, export, or delete. No import here, since uploading a
screenshot back to the device isn't a real use case.

## Things to know

- Drive auto-detection only scans conventional removable-media
  locations for your OS - if your setup is unusual, use "Browse
  Manually...". Always double-check you've got the right drive before
  applying changes or deleting anything.
- "Check for Updates" needs this project's GitHub releases to be
  public to work - if it says it couldn't check, that's likely why.
- This is not an official Schneider tool/integration - it automates
  publicly documented manual steps.

## More information

Full documentation, source code, and issue reporting:
[github.com/AGrant9130/AltiKit](https://github.com/AGrant9130/AltiKit)
