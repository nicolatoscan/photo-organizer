# Photo Organizer CLI Usage

This repository contains a single Python CLI, [photo_organizer.py](photo_organizer.py), plus two editable JSON files:

- [photo_organizer.config.json](photo_organizer.config.json) for paths and folder templates
- [camera_mappings.json](camera_mappings.json) for camera model and filename prefix mappings

## Requirements

- Python 3.10+ recommended
- `exiftool` recommended for best metadata support
- Optional fallback packages:
  - `Pillow` for basic image EXIF reading
  - `piexif` for writing image dates when ExifTool is unavailable

## Top-Level Help

```bash
python3 photo_organizer.py --help
```

The CLI has four subcommands:

- `sync`
- `organize`
- `green-flagged`
- `missing-dates`

## Config Files

You can keep your paths in [photo_organizer.config.json](photo_organizer.config.json). The CLI reads these values as defaults:

- `paths.organize_source`
- `paths.organize_dest`
- `paths.backup_main`
- `paths.backup_copy`
- `paths.digikam_db`
- `paths.green_flagged_dest`

If you want to override the config file, use:

```bash
python3 photo_organizer.py --config /path/to/photo_organizer.config.json ...
```

Camera prefixes are stored separately in [camera_mappings.json](camera_mappings.json). Edit that file when you need to add a new model or filename prefix.

## 1. Sync Main Drive To Backup

Compares files in the main drive against the backup drive.

- New files in the main drive are copied to the backup
- If a file exists only in the backup, the script prints a warning
- If the backup version looks newer, the script prints a warning

```bash
python3 photo_organizer.py sync --main /media/main-drive --backup /media/backup-drive --dry-run
```

Useful options:

- `--dry-run` preview only
- `--move-new` copy to backup and remove the original from the main drive

## 2. Organize Camera / Phone / Drone Imports

This command renames and sorts files in the style of `smistamento.ts`.

It keeps related files grouped together so sidecars stay linked:

- JPG / JPEG
- RAW files
- XMP sidecars
- XML sidecars

Example:

```bash
python3 photo_organizer.py organize --source /media/incoming --dest /media/library --dry-run
```

Default folder behavior:

- `Camera` -> `Camera/YYYY/YYYY-MM-DD - Description`
- `Drone` -> `Drone/YYYY/YYYY-MM-DD - Description`
- `Phone` -> `Phone/YYYY/MM`

The output name is built from the camera prefix and capture timestamp, for example:

- `PXL_20240901_183233.jpg`
- `DJI_20241012_091500.dng`

If a filename cannot be mapped to a known camera model, the script still keeps the timestamped name and uses the default prefix.

## 3. Copy digiKam Green-Flagged Files

This matches the behavior of `copy_green_flagged.py`.

It reads the digiKam SQLite database and copies files with the accepted pick label into `YYYY/MM` folders.

```bash
python3 photo_organizer.py green-flagged --db /path/to/digikam4.db --dest /media/green-flagged --dry-run
```

Useful options:

- `--db` path to `digikam4.db`; if omitted, the script tries common default locations
- `--label` choose a different pick label if needed
- `--dry-run` preview only

## 4. Find Files Without Dates

This matches the behavior of `find_files_without_dates.py`.

It scans image and video files, checks EXIF/XMP/QuickTime metadata, and prints files that do not have a usable date.

```bash
python3 photo_organizer.py missing-dates --source /media/incoming --recursive
```

Useful options:

- `--output missing_dates.txt` save the list to a file
- `--extensions jpg,mp4,raw` limit the scan to specific extensions
- `--set-dates` try to set dates from filenames that match `XXX_YYYYMMDD_HHMMSS`
- `--dry-run` show what would change without modifying files

## Typical Workflow

1. Import new files into your incoming folder.
2. Run `organize` to rename and sort them into the library structure.
3. Run `green-flagged` when you want to copy your digiKam picks to a second location.
4. Run `missing-dates` when you need to find files that may confuse Google Photos or other libraries.
5. Run `sync` to keep the backup drive aligned with the main drive.

## Notes

- The config file is meant to be edited by hand.
- The camera mapping file is meant to be edited by hand.
- Start with `--dry-run` before running any command in live mode.