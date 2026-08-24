# AltiKit Help

**Not affiliated with Schneider Electric.** This is an independent
hobby project that automates publicly documented manual steps - it is
not an official Schneider Electric tool, and Schneider Electric
doesn't support it. Please report bugs/issues via the GitHub repo
linked below, not to Schneider Electric.

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
4. **Apply Update** - with "Back up entire keypad before updating"
   checked (on by default), it snapshots the entire keypad drive before
   making any changes. Once done, click "Eject Drive" (or check
   "Automatically eject after updating" beforehand) before unplugging.
   If something goes wrong, "Restore Last Backup (undo update)" puts
   `LANG`/`KPCONF` back the way they were, from the most recent backup
   for that drive.

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

## Backup & Restore tab

A full safety net for the connected keypad, independent of the Update
Keypad tab's own automatic backup - use it any time, not just before an
update.

- **Take a full backup now** - snapshots *everything* on the keypad's
  drive (not just `LANG`/`KPCONF`/`DRVCONF`/`PRTSCR`, but anything else
  found there too, minus common OS clutter like `Thumbs.db`) into its
  own dated folder, with an optional label to help you tell backups
  apart later. Nothing is ever overwritten - a full history builds up.
- **Existing backups** - browse every backup taken, and delete old ones
  you no longer need (with confirmation).
- **Restore from selected backup** - pick a backup, check which items
  you want back, and restore them onto the connected keypad. This
  replaces each selected folder entirely, so double-check you've got
  the right backup before confirming.

## Things to know

- Drive auto-detection only scans conventional removable-media
  locations for your OS - if your setup is unusual, use "Browse
  Manually...". Always double-check you've got the right drive before
  applying changes or deleting anything.
- "Check for Updates" needs this project's GitHub releases to be
  public to work - if it says it couldn't check, that's likely why.

## More information

Full documentation, source code, and issue reporting:
[github.com/AGrant9130/AltiKit](https://github.com/AGrant9130/AltiKit)
