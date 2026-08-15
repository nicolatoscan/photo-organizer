from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "photo_organizer.config.json"
DEFAULT_CAMERA_MAPPING_PATH = BASE_DIR / "camera_mappings.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif", ".gif", ".bmp", ".webp"}
RAW_EXTENSIONS = {".cr2", ".cr3", ".arw", ".dng", ".nef", ".orf", ".rw2"}
VIDEO_EXTENSIONS = {".mp4", ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".mts", ".m2ts", ".mpg", ".mpeg"}
SIDE_CAR_EXTENSIONS = {".xmp", ".xml"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | RAW_EXTENSIONS | VIDEO_EXTENSIONS
SUPPORTED_EXTENSIONS = MEDIA_EXTENSIONS | SIDE_CAR_EXTENSIONS

DEFAULT_CAMERA_MODELS = {
    "Pixel 8a": "PXL",
    "Pixel 7a": "PXL",
    "Pixel 7": "PXL",
    "Pixel 10 Pro": "PXL",
    "FC3682": "DJI",
    "Canon EOS 1100D": "CNN",
    "Canon EOS 6D": "CNN",
    "Canon EOS 1300D": "CNN",
    "Canon EOS 80D": "CNN",
    "ILCE-9": "SNY",
    "ILCE-7RM4": "SNY",
    "Lumia 720": "LMA",
    "Lumia 930": "LMA",
    "Mi A1": "MIX",
    "Mi A2 Lite": "MIX",
    "Redmi Note 7": "MIX",
    "SM-G991B": "SAM",
    "SM-S908B": "SAM",
    "SM-A505FN": "SAM",
    "SM-A528B": "SAM",
    "Galaxy S24 Ultra": "SAM",
    "iPhone XS Max": "IPH",
    "iPhone 12": "IPH",
    "iPhone 11": "IPH",
    "iPhone 16 Pro": "IPH",
    "iPhone 13 Pro": "IPH",
    "iPhone 13": "IPH",
    "iPhone 15": "IPH",
    "iPhone 14 Pro": "IPH",
    "KODAK EASYSHARE C613 ZOOM DIGITAL CAMERA": "KDK",
    "NIKON D80": "NKN",
    "NIKON D200": "NKN",
}

DEFAULT_CONFIG = {
    "paths": {
        "organize_source": "",
        "organize_dest": "",
        "backup_main": "",
        "backup_copy": "",
        "digikam_db": "",
        "green_flagged_dest": "",
    },
    "organize": {
        "default_description": "Unsorted",
        "category_patterns": {
            "Camera": "{category}/{year}/{date_folder}",
            "Drone": "{category}/{year}/{date_folder}",
            "Phone": "{category}/{year}/{month}",
            "default": "{year}/{month}",
        },
    },
    "extensions": {
        "organize": [
            "jpg", "jpeg", "png", "tiff", "tif", "webp", "heic", "heif",
            "nef", "orf", "rw2", "cr2", "cr3", "arw", "dng",
            "mp4", "mov", "3gp", "mts", "m2ts", "avi", "mkv", "wmv", "flv", "m4v", "mpg", "mpeg",
            "xmp", "xml",
        ],
        "find_missing_dates": [
            "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heic", "heif",
            "nef", "orf", "rw2", "cr2", "cr3", "arw", "dng",
            "mp4", "mov", "3gp", "mts", "m2ts", "avi", "mkv", "wmv", "flv", "m4v", "mpg", "mpeg",
        ],
    },
}

PICK_LABELS = {
    "Pick Label None": "No Pick",
    "Pick Label Rejected": "Rejected",
    "Pick Label Pending": "Pending",
    "Pick Label Accepted": "Accepted (Green Flag)",
}

EXIFTOOL_PATH = shutil.which("exiftool") or shutil.which("exiftool.exe") or ""
HAS_EXIFTOOL = bool(EXIFTOOL_PATH)

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import piexif
    HAS_PIEXIF = True
except Exception:
    HAS_PIEXIF = False


@dataclass(frozen=True)
class FileInfo:
    path: Path
    relative_path: str
    size: int
    mtime_ns: int


@dataclass
class OrganizeFile:
    path: Path
    folder: Path
    filename: str
    extension: str
    kind: str
    logical_stem: str
    category: str
    source_group_key: str
    metadata: dict[str, Any] | None = None
    destination_folder: Path | None = None
    destination_stem: str | None = None
    duplicate: bool = False
    is_linked: bool = False


def load_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | None) -> dict[str, Any]:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    return merge_dicts(DEFAULT_CONFIG, load_json_file(path, {}))


def load_camera_mapping(mapping_path: str | None) -> dict[str, Any]:
    path = Path(mapping_path).expanduser() if mapping_path else DEFAULT_CAMERA_MAPPING_PATH
    loaded = load_json_file(path, {})
    models = loaded.get("models", {}) if isinstance(loaded.get("models", {}), dict) else {}
    filename_prefixes = loaded.get("filename_prefixes", {}) if isinstance(loaded.get("filename_prefixes", {}), dict) else {}
    return {
        "default_prefix": loaded.get("default_prefix", "UNK"),
        "models": {str(key): str(value) for key, value in {**DEFAULT_CAMERA_MODELS, **models}.items()},
        "filename_prefixes": {str(key): str(value) for key, value in filename_prefixes.items()},
    }


def normalize_tag_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_exif_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.match(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})([+-]\d{2}:\d{2})?$", text)
    if not match:
        return None
    try:
        dt = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
        )
        offset = match.group(7)
        if offset:
            sign = 1 if offset[0] == "+" else -1
            hours = int(offset[1:3])
            minutes = int(offset[4:6])
            dt -= timedelta(minutes=sign * (hours * 60 + minutes))
        return dt
    except Exception:
        return None


def pick_metadata_value(metadata: dict[str, Any], wanted_tags: list[str]) -> Any:
    wanted = [normalize_tag_name(tag) for tag in wanted_tags]
    for key, value in metadata.items():
        key_name = normalize_tag_name(key.split(":")[-1])
        if key_name in wanted and value is not None and str(value).strip():
            return value
    return None


def exiftool_metadata(file_path: Path, tags: list[str]) -> dict[str, Any]:
    if not HAS_EXIFTOOL:
        return {}
    try:
        result = subprocess.run([EXIFTOOL_PATH, "-json", "-G", "-s"] + tags + [str(file_path)], capture_output=True, text=True, timeout=60)
        if not result.stdout.strip():
            return {}
        data = json.loads(result.stdout)
        return data[0] if data else {}
    except Exception:
        return {}


def exiftool_metadata_batch(file_paths: list[Path], tags: list[str], progress_label: str | None = None) -> dict[Path, dict[str, Any]]:
    if not HAS_EXIFTOOL or not file_paths:
        return {file_path: {} for file_path in file_paths}

    results: dict[Path, dict[str, Any]] = {}
    batch_size = 100
    total_batches = (len(file_paths) + batch_size - 1) // batch_size
    for batch_index, start in enumerate(range(0, len(file_paths), batch_size), 1):
        batch = file_paths[start:start + batch_size]
        if progress_label:
            print(f"  {progress_label}: batch {batch_index}/{total_batches} ({len(batch)} file(s))")
        try:
            cmd = [EXIFTOOL_PATH, "-json", "-G", "-s"] + tags + [str(item) for item in batch]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if not result.stdout.strip():
                for item in batch:
                    results[item] = {}
                continue
            data = json.loads(result.stdout)
            for entry in data:
                source_file = Path(entry.get("SourceFile", ""))
                results[source_file] = entry
            for item in batch:
                results.setdefault(item, {})
        except Exception:
            for item in batch:
                results[item] = {}
    return results


def image_exif_date(file_path: Path) -> datetime | None:
    if not HAS_PIL:
        return None
    try:
        with Image.open(file_path) as image:
            exif_data = image._getexif()
            if not exif_data:
                return None
            exif = {TAGS.get(tag, tag): value for tag, value in exif_data.items()}
            for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                if key in exif and exif[key]:
                    parsed = parse_exif_datetime(str(exif[key]))
                    if parsed:
                        return parsed
    except Exception:
        return None
    return None


def get_file_date(file_path: Path) -> tuple[datetime | None, str | None]:
    if HAS_EXIFTOOL:
        metadata = exiftool_metadata(file_path, [
            "-DateTimeOriginal",
            "-CreateDate",
            "-DateTimeDigitized",
            "-ModifyDate",
            "-XMP:DateTimeOriginal",
            "-XMP:CreateDate",
            "-XMP:DateCreated",
            "-XMP:MetadataDate",
            "-QuickTime:CreateDate",
            "-QuickTime:MediaCreateDate",
            "-QuickTime:TrackCreateDate",
        ])
        for tag in [
            "DateTimeOriginal",
            "CreateDate",
            "DateTimeDigitized",
            "ModifyDate",
            "XMP:DateTimeOriginal",
            "XMP:CreateDate",
            "XMP:DateCreated",
            "XMP:MetadataDate",
            "QuickTime:CreateDate",
            "QuickTime:MediaCreateDate",
            "QuickTime:TrackCreateDate",
        ]:
            parsed = parse_exif_datetime(pick_metadata_value(metadata, [tag]))
            if parsed:
                return parsed, tag
        return None, None

    if file_path.suffix.lower() in VIDEO_EXTENSIONS:
        return None, None
    parsed = image_exif_date(file_path)
    if parsed:
        return parsed, "EXIF (PIL)"
    return None, None


def set_date_exiftool(file_path: Path, date_time: datetime, dry_run: bool = False) -> bool:
    if dry_run:
        return True
    try:
        exif_date = date_time.strftime("%Y:%m:%d %H:%M:%S")
        cmd = [EXIFTOOL_PATH, "-overwrite_original", f"-DateTimeOriginal={exif_date}", f"-CreateDate={exif_date}", f"-ModifyDate={exif_date}", str(file_path)]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).returncode == 0
    except Exception:
        return False


def set_date_piexif(file_path: Path, date_time: datetime, dry_run: bool = False) -> bool:
    if not HAS_PIEXIF:
        return False
    if dry_run:
        return True
    try:
        exif_date = date_time.strftime("%Y:%m:%d %H:%M:%S")
        try:
            exif_dict = piexif.load(str(file_path))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode()
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_date.encode()
        exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_date.encode()
        piexif.insert(piexif.dump(exif_dict), str(file_path))
        return True
    except Exception:
        return False


def set_file_date(file_path: Path, date_time: datetime, dry_run: bool = False) -> bool:
    if HAS_EXIFTOOL:
        return set_date_exiftool(file_path, date_time, dry_run)
    if file_path.suffix.lower() in VIDEO_EXTENSIONS:
        return False
    return set_date_piexif(file_path, date_time, dry_run)


def parse_date_from_filename(filename: str) -> datetime | None:
    match = re.match(r"^[A-Za-z0-9]+_(\d{8})_(\d{6})", filename)
    if not match:
        return None
    date_str = match.group(1)
    time_str = match.group(2)
    try:
        return datetime(
            int(date_str[0:4]),
            int(date_str[4:6]),
            int(date_str[6:8]),
            int(time_str[0:2]),
            int(time_str[2:4]),
            int(time_str[4:6]),
        )
    except ValueError:
        return None


def logical_stem_for_file(file_path: Path) -> str:
    stem = file_path.stem
    if file_path.suffix.lower() == ".xml" and re.match(r"^.+M\d\d$", stem, re.IGNORECASE):
        return stem[:-3]
    return stem


def classify_file(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "IMAGE"
    if ext in RAW_EXTENSIONS:
        return "RAW"
    if ext in VIDEO_EXTENSIONS:
        return "VIDEO"
    if ext == ".xmp":
        return "XMP"
    if ext == ".xml":
        return "XML"
    return "UNKNOWN"


def detect_category(file_path: Path, source_root: Path) -> str:
    try:
        relative_parts = file_path.relative_to(source_root).parts
    except Exception:
        relative_parts = file_path.parts
    for part in relative_parts:
        if part in {"Camera", "Drone", "Phone"}:
            return part
    return "default"


def extract_description(source_folder: Path, fallback: str) -> str:
    for part in reversed(source_folder.parts):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*-\s*(.+)$", part)
        if match:
            return match.group(2).strip() or fallback
    return fallback


def build_destination_folder(file_info: OrganizeFile, source_root: Path, config: dict[str, Any]) -> Path:
    capture_date, _ = get_file_date(file_info.path)
    if capture_date is None:
        capture_date = datetime.fromtimestamp(file_info.path.stat().st_mtime)

    year = capture_date.strftime("%Y")
    month = capture_date.strftime("%m")
    day = capture_date.strftime("%d")

    fallback_description = config["organize"].get("default_description", "Unsorted")
    description = extract_description(file_info.folder, fallback_description)
    if description == fallback_description:
        try:
            source_bits = list(file_info.path.relative_to(source_root).parts)
        except Exception:
            source_bits = list(file_info.path.parts)
        for part in source_bits:
            match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*-\s*(.+)$", part)
            if match:
                description = match.group(2).strip() or description
                break

    date_folder = f"{year}-{month}-{day} - {description}" if description else f"{year}-{month}-{day}"
    patterns = config["organize"].get("category_patterns", {})
    pattern = patterns.get(file_info.category, patterns.get("default", "{category}/{year}/{month}"))
    relative = pattern.format(
        category=file_info.category,
        year=year,
        month=month,
        day=day,
        date=f"{year}-{month}-{day}",
        date_folder=date_folder,
        description=description,
    )
    return Path(relative)


def collect_organize_files(source_root: Path, extensions: set[str], progress_every: int = 5000) -> list[OrganizeFile]:
    files: list[OrganizeFile] = []
    scanned = 0
    matched = 0
    for item in source_root.rglob("*"):
        scanned += 1
        if progress_every > 0 and scanned % progress_every == 0:
            print(f"  Scanned {scanned} paths, matched {matched} media file(s)...")
        if not item.is_file():
            continue
        ext = item.suffix.lower()
        if ext not in extensions:
            continue
        matched += 1
        files.append(OrganizeFile(
            path=item,
            folder=item.parent,
            filename=item.name,
            extension=ext,
            kind=classify_file(item),
            logical_stem=logical_stem_for_file(item),
            category=detect_category(item, source_root),
            source_group_key=f"{item.parent.as_posix()}::{logical_stem_for_file(item)}",
        ))
    print(f"  Scanned {scanned} paths total, matched {matched} media file(s).")
    return files


def choose_primary_file(group: list[OrganizeFile]) -> OrganizeFile | None:
    priority = {"IMAGE": 0, "RAW": 1, "VIDEO": 2, "XMP": 3, "XML": 4, "UNKNOWN": 5}
    media = [item for item in group if item.kind in {"IMAGE", "RAW", "VIDEO"}]
    if media:
        return sorted(media, key=lambda item: (priority.get(item.kind, 99), item.filename))[0]
    if group:
        return sorted(group, key=lambda item: (priority.get(item.kind, 99), item.filename))[0]
    return None


def camera_prefix_for_file(file_path: Path, metadata: dict[str, Any], mappings: dict[str, Any]) -> str:
    default_prefix = mappings.get("default_prefix", "UNK")
    filename_prefixes = mappings.get("filename_prefixes", {})
    models = mappings.get("models", {})

    stem = file_path.stem
    for prefix, mapped in sorted(filename_prefixes.items(), key=lambda item: len(item[0]), reverse=True):
        if stem.startswith(prefix):
            return mapped

    model = pick_metadata_value(metadata, ["Model"])
    if model is not None:
        mapped = models.get(str(model), models.get(str(model).strip(), default_prefix))
        if mapped:
            return mapped

    lowered = stem.lower()
    if stem.startswith("PXL"):
        return "PXL"
    if stem.startswith("DJI"):
        return "DJI"
    if stem.startswith("CNN"):
        return "CNN"
    if stem.startswith("WP"):
        return "LMA"
    if stem.startswith("VID"):
        return "MIX"
    if stem.startswith("WIN"):
        return "WIN"
    if stem.startswith("C") and file_path.suffix.lower() == ".mp4":
        return "SNY"
    if "whatsapp" in lowered or " wa" in lowered:
        return "WAP"
    return default_prefix


def organize_destination_base(file_path: Path, metadata: dict[str, Any], mappings: dict[str, Any]) -> str:
    prefix = camera_prefix_for_file(file_path, metadata, mappings)
    capture_value = pick_metadata_value(metadata, ["CreateDate", "DateTimeOriginal", "DateTimeDigitized", "FileModifyDate", "ModifyDate"])
    capture_date = parse_exif_datetime(capture_value)
    if capture_date is None:
        capture_date = datetime.fromtimestamp(file_path.stat().st_mtime)
    return f"{prefix}_{capture_date.strftime('%Y%m%d_%H%M%S')}"


def organize_files(source_root: Path, dest_root: Path, config: dict[str, Any], mappings: dict[str, Any], dry_run: bool = False, progress_every: int = 100) -> None:
    extensions = {f".{ext.lower().lstrip('.')}" for ext in config["extensions"].get("organize", sorted(SUPPORTED_EXTENSIONS))}
    print("Scanning source tree...")
    files = collect_organize_files(source_root, extensions)
    print(f"Processing folder: {source_root}")
    print(f"Found {len(files)} file(s) to consider.")

    grouped: dict[str, list[OrganizeFile]] = defaultdict(list)
    for item in files:
        grouped[item.source_group_key].append(item)

    group_records: list[tuple[OrganizeFile, list[OrganizeFile]]] = []
    for group in grouped.values():
        primary = choose_primary_file(group)
        if primary is None:
            continue
        group_records.append((primary, group))

    metadata_cache = exiftool_metadata_batch([record[0].path for record in group_records], [
        "-Model",
        "-CreateDate",
        "-DateTimeOriginal",
        "-DateTimeDigitized",
        "-ModifyDate",
        "-FileModifyDate",
    ], progress_label="Reading metadata") if HAS_EXIFTOOL else {}

    assigned_counts: dict[str, int] = defaultdict(int)
    plan: list[tuple[Path, Path]] = []

    total_groups = len(group_records)
    for group_index, (primary, group) in enumerate(sorted(group_records, key=lambda item: item[0].path.as_posix()), 1):
        if progress_every > 0 and (group_index == 1 or group_index % progress_every == 0 or group_index == total_groups):
            print(f"  Planning group {group_index}/{total_groups} ({primary.path.name})")
        metadata = metadata_cache.get(primary.path, {}) if HAS_EXIFTOOL else {}
        primary.metadata = metadata
        destination_folder = build_destination_folder(primary, source_root, config)
        destination_base = organize_destination_base(primary.path, metadata, mappings)
        count = assigned_counts[destination_base]
        assigned_counts[destination_base] += 1
        if count > 0:
            destination_base = f"{destination_base}_{count:03d}"
            primary.duplicate = True

        for item in group:
            item.destination_folder = dest_root / destination_folder
            item.destination_stem = destination_base

        for item in group:
            if item.destination_folder is None or item.destination_stem is None:
                continue
            destination_name = f"{item.destination_stem}{item.extension}"
            plan.append((item.path, item.destination_folder / destination_name))

    copied = 0
    skipped = 0
    total_plan = len(plan)
    print(f"Preparing to move/copy {total_plan} file(s)...")
    for plan_index, (source, destination) in enumerate(plan, 1):
        if progress_every > 0 and (plan_index == 1 or plan_index % progress_every == 0 or plan_index == total_plan):
            print(f"  Executing file {plan_index}/{total_plan}")
        if destination.exists():
            print(f"File already exists, skipping: {source} -> {destination}")
            skipped += 1
            continue
        print(f"{source} --> {destination}")
        if dry_run:
            copied += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        copied += 1

    print()
    print(f"Organized files: {copied}")
    print(f"Skipped files:   {skipped}")


def file_inventory(root: Path, progress_label: str | None = None, progress_every: int = 5000) -> dict[str, FileInfo]:
    inventory: dict[str, FileInfo] = {}
    scanned = 0
    matched = 0
    for current_root, dir_names, file_names in os.walk(root, topdown=True):
        dir_names[:] = [name for name in dir_names if not name.startswith(".")]
        scanned += 1 + len(file_names)
        if progress_label and progress_every > 0 and scanned % progress_every == 0:
            print(f"  {progress_label}: scanned {scanned} paths, indexed {matched} file(s)...")
        current_root_path = Path(current_root)
        for file_name in file_names:
            item = current_root_path / file_name
            stat = item.stat()
            rel = item.relative_to(root).as_posix()
            inventory[rel] = FileInfo(item, rel, stat.st_size, stat.st_mtime_ns)
            matched += 1
    if progress_label:
        print(f"  {progress_label}: scanned {scanned} paths total, indexed {matched} file(s).")
    return inventory


def sync_folders(main_root: Path, backup_root: Path, dry_run: bool = False, move_new: bool = False, progress_every: int = 1000) -> None:
    print("Indexing main drive...")
    main_inventory = file_inventory(main_root, progress_label="Main inventory", progress_every=progress_every)
    print("Indexing backup drive...")
    backup_inventory = file_inventory(backup_root, progress_label="Backup inventory", progress_every=progress_every)

    new_or_updated = 0
    warnings = 0
    skipped = 0
    copied = 0
    moved = 0
    total_main = len(main_inventory)

    print(f"Comparing {total_main} file(s) from main against backup...")
    for index, (rel_path, source_info) in enumerate(sorted(main_inventory.items()), 1):
        # if progress_every > 0 and (index == 1 or index % progress_every == 0 or index == total_main):
        #     print(f"  Compared {index}/{total_main} file(s) from main drive")
        destination_info = backup_inventory.get(rel_path)
        destination_path = backup_root / rel_path

        if destination_info is None:
            print(f"[NEW] {source_info.path} -> {destination_path}")
            new_or_updated += 1
            if not dry_run:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source_info.path), str(destination_path))
                copied += 1
                if move_new:
                    source_info.path.unlink()
                    moved += 1
            continue

        if destination_info.mtime_ns > source_info.mtime_ns:
            print(f"[WARNING] Backup is newer for {rel_path}")
            warnings += 1
            continue

        if source_info.size == destination_info.size and source_info.mtime_ns <= destination_info.mtime_ns:
            skipped += 1
            continue

        print(f"[UPDATE] {source_info.path} -> {destination_path}")
        new_or_updated += 1
        if not dry_run:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_info.path), str(destination_path))
            copied += 1
            if move_new:
                source_info.path.unlink()
                moved += 1

    total_backup = len(backup_inventory)
    print(f"Checking {total_backup} backup file(s) for orphan warnings...")
    for index, (rel_path, destination_info) in enumerate(sorted(backup_inventory.items()), 1):
        # if progress_every > 0 and (index == 1 or index % progress_every == 0 or index == total_backup):
        #     print(f"  Checked {index}/{total_backup} backup file(s)")
        if rel_path not in main_inventory:
            print(f"[WARNING] Present only in backup: {destination_info.path}")
            warnings += 1

    print()
    print("Sync summary")
    print(f"  New/updated files: {new_or_updated}")
    print(f"  Copied:            {copied}")
    print(f"  Moved:             {moved}")
    print(f"  Skipped:           {skipped}")
    print(f"  Warnings:          {warnings}")


def find_digikam_db() -> str | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path(appdata) / "digikam" / "digikam4.db",
            Path(localappdata) / "digikam" / "digikam4.db",
            Path(appdata) / "digikam" / "digikam.db",
        ]
    else:
        home = Path.home()
        candidates = [
            home / ".local" / "share" / "digikam" / "digikam4.db",
            home / ".digikam" / "digikam4.db",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def get_green_flagged_files(db_path: str, pick_label_name: str | None = None) -> list[tuple[str, str, str]]:
    if pick_label_name is None:
        pick_label_name = "Pick Label Accepted"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT DISTINCT
            ar.specificPath,
            a.relativePath,
            i.name AS filename
        FROM ImageTags it
        JOIN Tags t          ON t.id = it.tagid
        JOIN Images i        ON i.id = it.imageid
        JOIN Albums a        ON a.id = i.album
        JOIN AlbumRoots ar   ON ar.id = a.albumRoot
        WHERE t.name = ?
        ORDER BY ar.specificPath, a.relativePath, i.name
    """
    results: list[tuple[str, str, str]] = []
    try:
        cursor = conn.execute(query, (pick_label_name,))
        for row in cursor:
            root_path = row["specificPath"]
            rel_path = row["relativePath"]
            filename = row["filename"]
            if sys.platform == "win32":
                root_path = root_path.replace("/", "\\")
                rel_path = rel_path.replace("/", "\\")
            results.append((root_path, rel_path, filename))
    finally:
        conn.close()
    return results


def extract_year_month(rel_path: str) -> tuple[str, str]:
    normalized = rel_path.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    for part in reversed(parts):
        match = re.match(r"(\d{4})-(\d{2})-\d{2}", part)
        if match:
            return match.group(1), match.group(2)
    for index, part in enumerate(parts):
        if re.match(r"^\d{4}$", part) and index + 1 < len(parts):
            next_part = parts[index + 1]
            if re.match(r"^\d{1,2}$", next_part):
                return part, next_part.zfill(2)
    for part in parts:
        if re.match(r"^\d{4}$", part):
            return part, "00"
    return "unknown", "00"


def check_orphaned_files(files: list[tuple[str, str, str]], dest_root: Path) -> list[Path]:
    if not dest_root.exists():
        return []
    expected_files = set()
    for _root_path, rel_path, filename in files:
        year, month = extract_year_month(rel_path)
        expected_files.add((dest_root / year / month / filename).resolve())

    orphaned: list[Path] = []
    for dest_file in dest_root.rglob("*"):
        if dest_file.is_file() and dest_file.resolve() not in expected_files:
            orphaned.append(dest_file)
    return orphaned


def copy_green_flagged(db_path: str, dest_root: Path, label: str, dry_run: bool = False) -> None:
    label_name = PICK_LABELS.get(label, label)
    print(f"Looking for files with pick label tag: '{label}' ({label_name})")
    if dry_run:
        print("*** DRY RUN — no files will be copied ***")
    print()

    files = get_green_flagged_files(db_path, pick_label_name=label)
    print(f"Found {len(files)} file(s) in database with pick label '{label}'.")
    if not files:
        print("Nothing to do.")
        return

    copied = 0
    replaced = 0
    skipped = 0
    missing = 0
    errors = 0

    for idx, (root_path, rel_path, filename) in enumerate(files, 1):
        source = Path(root_path) / rel_path.lstrip("\\/") / filename
        year, month = extract_year_month(rel_path)
        destination = dest_root / year / month / filename

        if not source.is_file():
            print(f"  [{idx}/{len(files)}] [MISSING] {source}")
            missing += 1
            continue

        if destination.exists():
            if destination.stat().st_size == source.stat().st_size:
                skipped += 1
                continue
            if dry_run:
                print(f"  [{idx}/{len(files)}] [DRY-RUN-REPLACE] {source}")
                print(f"                  -> {destination} (will overwrite)")
                replaced += 1
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
                replaced += 1
                print(f"  [{idx}/{len(files)}] [REPLACED] {source}")
            except Exception as exc:
                print(f"  [{idx}/{len(files)}] [ERROR]   {source} -> {exc}")
                errors += 1
            continue

        if dry_run:
            print(f"  [{idx}/{len(files)}] [DRY-RUN] {source}")
            print(f"             -> {destination}")
            copied += 1
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))
            copied += 1
            print(f"  [{idx}/{len(files)}] [COPIED] {source}")
        except Exception as exc:
            print(f"  [{idx}/{len(files)}] [ERROR]  {source} -> {exc}")
            errors += 1

    print()
    print("=" * 50)
    action = "Would copy" if dry_run else "Copied"
    print(f"  {action}:                {copied}")
    print(f"  Replaced (overwritten): {replaced}")
    print(f"  Skipped (same file):    {skipped}")
    print(f"  Missing source files:   {missing}")
    if errors:
        print(f"  Errors:                 {errors}")
    print("=" * 50)

    print()
    print("Checking for files in destination that are no longer green-flagged...")
    orphaned = check_orphaned_files(files, dest_root)
    if orphaned:
        print(f"\nFound {len(orphaned)} file(s) in destination that are NOT currently green-flagged:")
        print("(These may have had their green flag removed)")
        print()
        for orphan in orphaned:
            print(f"  {orphan}")
        print()
        print("=" * 50)
        print(f"  Orphaned files (no longer green-flagged): {len(orphaned)}")
        print("=" * 50)
    else:
        print("No orphaned files found. All files in destination are still green-flagged.")


def scan_folder(source_path: Path, extensions: set[str], recursive: bool = False) -> list[Path]:
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    if not source_path.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {source_path}")

    files: list[Path] = []
    pattern = "**/*" if recursive else "*"
    for item in source_path.glob(pattern):
        if item.is_file() and item.suffix.lower().lstrip(".") in extensions:
            files.append(item)
    return sorted(files)


def _try_set_date_from_filename(file_path: Path, dry_run: bool, files_fixed: list[tuple[Path, datetime]], files_no_pattern: list[Path]) -> None:
    parsed_date = parse_date_from_filename(file_path.name)
    if parsed_date:
        can_write = HAS_EXIFTOOL or HAS_PIEXIF
        if not can_write and not dry_run:
            print("    WARNING: No tool available to write dates.")
            print(f"    Install ExifTool or piexif. Would set to: {parsed_date.strftime('%Y:%m:%d %H:%M:%S')}")
            files_no_pattern.append(file_path)
            return

        action = "[DRY-RUN]" if dry_run else "[SETTING]"
        print(f"    {action} Date from filename: {parsed_date.strftime('%Y:%m:%d %H:%M:%S')}")
        if set_file_date(file_path, parsed_date, dry_run):
            files_fixed.append((file_path, parsed_date))
            if not dry_run:
                print("    ✓ Date set successfully")
        else:
            files_no_pattern.append(file_path)
    else:
        print("    No date pattern found in filename")
        files_no_pattern.append(file_path)


def check_files_for_dates(files: list[Path], set_dates: bool = False, dry_run: bool = False) -> tuple[list[Path], list[tuple[Path, str]], list[tuple[Path, datetime]], list[Path]]:
    without_dates: list[Path] = []
    with_dates: list[tuple[Path, str]] = []
    files_fixed: list[tuple[Path, datetime]] = []
    files_no_pattern: list[Path] = []
    total = len(files)

    method = "ExifTool (EXIF + XMP + QuickTime)" if HAS_EXIFTOOL else "PIL (basic EXIF only)"
    print(f"Checking {total} file(s) for date metadata using {method}...\n")

    if HAS_EXIFTOOL:
        print("  Reading metadata in batches...")
        date_results = exiftool_metadata_batch(files, [
            "-DateTimeOriginal",
            "-CreateDate",
            "-DateTimeDigitized",
            "-ModifyDate",
            "-XMP:DateTimeOriginal",
            "-XMP:CreateDate",
            "-XMP:DateCreated",
            "-XMP:MetadataDate",
            "-QuickTime:CreateDate",
            "-QuickTime:MediaCreateDate",
            "-QuickTime:TrackCreateDate",
        ])

        for idx, file_path in enumerate(files, 1):
            if idx % 1000 == 0 or idx == total:
                print(f"  Processed {idx}/{total} files...")
            metadata = date_results.get(file_path, {})
            date = None
            tag = None
            for candidate in [
                "DateTimeOriginal",
                "CreateDate",
                "DateTimeDigitized",
                "ModifyDate",
                "XMP:DateTimeOriginal",
                "XMP:CreateDate",
                "XMP:DateCreated",
                "XMP:MetadataDate",
                "QuickTime:CreateDate",
                "QuickTime:MediaCreateDate",
                "QuickTime:TrackCreateDate",
            ]:
                parsed = parse_exif_datetime(pick_metadata_value(metadata, [candidate]))
                if parsed:
                    date = parsed
                    tag = candidate
                    break

            if date:
                with_dates.append((file_path, f"{date.strftime('%Y-%m-%d %H:%M:%S')} [{tag}]"))
            else:
                without_dates.append(file_path)
                print(f"  [{idx}/{total}] [NO DATE]  {file_path}")
                if set_dates:
                    _try_set_date_from_filename(file_path, dry_run, files_fixed, files_no_pattern)
    else:
        for idx, file_path in enumerate(files, 1):
            if idx % 1000 == 0 or idx == total:
                print(f"  Checked {idx}/{total} files...")
            if file_path.suffix.lower() in VIDEO_EXTENSIONS:
                without_dates.append(file_path)
                print(f"  [{idx}/{total}] [NO DATE]  {file_path}  (video - needs ExifTool)")
                if set_dates:
                    _try_set_date_from_filename(file_path, dry_run, files_fixed, files_no_pattern)
                continue

            date = image_exif_date(file_path)
            if date:
                with_dates.append((file_path, date.strftime("%Y-%m-%d %H:%M:%S")))
            else:
                without_dates.append(file_path)
                print(f"  [{idx}/{total}] [NO DATE]  {file_path}")
                if set_dates:
                    _try_set_date_from_filename(file_path, dry_run, files_fixed, files_no_pattern)

    return without_dates, with_dates, files_fixed, files_no_pattern


def save_results(files_without_dates: list[Path], output_path: Path) -> None:
    try:
        with output_path.open("w", encoding="utf-8") as handle:
            handle.write("Files without date metadata\n")
            handle.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write(f"Total files: {len(files_without_dates)}\n")
            handle.write("=" * 80 + "\n\n")
            for file_path in files_without_dates:
                handle.write(f"{file_path}\n")
        print(f"\nResults saved to: {output_path}")
    except Exception as exc:
        print(f"\nERROR: Could not save results to {output_path}: {exc}")


def run_missing_dates(args: argparse.Namespace, _config: dict[str, Any]) -> None:
    source = Path(args.source).expanduser()
    extensions = [ext.strip().lower().lstrip(".") for ext in args.extensions.split(",") if ext.strip()]
    print(f"Source folder: {source}")
    print(f"Recursive: {args.recursive}")
    print(f"Extensions: {', '.join(extensions)}")
    print(f"Metadata reader: {'ExifTool (EXIF + XMP + QuickTime)' if HAS_EXIFTOOL else 'PIL (basic EXIF only)'}")
    if not HAS_EXIFTOOL:
        print("  -> Videos and XMP-only files will be reported as missing dates")
        print("  -> Install ExifTool for full coverage: winget install OliverBetz.ExifTool")
    if args.set_dates:
        print("Set dates from filename: YES")
        writer = "ExifTool" if HAS_EXIFTOOL else ("piexif" if HAS_PIEXIF else "NONE")
        print(f"Date writer: {writer}")
        print("Mode: DRY RUN (no files will be modified)" if args.dry_run else "Mode: LIVE (files will be modified)")
    print()

    print("Scanning for files...")
    files = scan_folder(source, set(extensions), args.recursive)
    print(f"Found {len(files)} file(s) matching extensions.\n")
    if not files:
        print("No files to check.")
        return

    without_dates, with_dates, files_fixed, files_no_pattern = check_files_for_dates(files, set_dates=args.set_dates, dry_run=args.dry_run)

    print()
    print("=" * 80)
    print(f"  Files WITH date metadata:    {len(with_dates)}")
    print(f"  Files WITHOUT date metadata: {len(without_dates)}")
    if args.set_dates:
        action = "Would fix" if args.dry_run else "Fixed"
        print(f"  {action} from filename:      {len(files_fixed)}")
        print(f"  No pattern in filename:      {len(files_no_pattern)}")
    print(f"  Total files checked:         {len(files)}")
    print("=" * 80)

    if without_dates and args.output:
        save_results(without_dates, Path(args.output).expanduser())
    elif without_dates and not args.set_dates:
        print("\nFiles without date metadata:")
        for file_path in without_dates:
            print(f"  {file_path}")

    if args.set_dates:
        if files_fixed:
            action = "Would be fixed" if args.dry_run else "Successfully fixed"
            print(f"\n{action}: {len(files_fixed)} file(s)")
        if files_no_pattern:
            print(f"Could not fix: {len(files_no_pattern)} file(s) (no valid date pattern in filename)")
        if args.dry_run and files_fixed:
            print("\nRun without --dry-run to actually modify the files.")
    elif without_dates:
        print(f"\nFound {len(without_dates)} file(s) without date metadata.")
        print("Use --set-dates to automatically set dates from filenames (pattern: XXX_YYYYMMDD_HHMMSS)")
    else:
        print("\nAll files have date metadata!")


def run_sync(args: argparse.Namespace, _config: dict[str, Any]) -> None:
    main_root = Path(args.main).expanduser()
    backup_root = Path(args.backup).expanduser()
    if not main_root.is_dir():
        raise NotADirectoryError(f"Main drive does not exist or is not a directory: {main_root}")
    if not backup_root.exists() and not args.dry_run:
        backup_root.mkdir(parents=True, exist_ok=True)
    if not backup_root.is_dir() and not args.dry_run:
        raise NotADirectoryError(f"Backup drive is not a directory: {backup_root}")
    print(f"Main drive:   {main_root}")
    print(f"Backup drive: {backup_root}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()
    sync_folders(main_root, backup_root, dry_run=args.dry_run, move_new=args.move_new, progress_every=args.progress_every)


def run_organize(args: argparse.Namespace, config: dict[str, Any], mappings: dict[str, Any]) -> None:
    source = Path(args.source).expanduser()
    dest = Path(args.dest).expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"Source directory does not exist or is not a directory: {source}")
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    print(f"Source: {source}")
    print(f"Destination: {dest}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()
    organize_files(source, dest, config, mappings, dry_run=args.dry_run, progress_every=args.progress_every)


def run_green_flagged(args: argparse.Namespace, _config: dict[str, Any]) -> None:
    db_path = args.db
    if db_path is None:
        db_path = find_digikam_db()
        if db_path is None:
            raise FileNotFoundError("Could not find digikam4.db automatically. Please specify it with --db <path>")
        print(f"Found digiKam database: {db_path}")
    elif not os.path.isfile(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    print(f"Looking for pick label: {args.label}")
    print(f"Destination: {args.dest}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()
    copy_green_flagged(db_path, Path(args.dest).expanduser(), args.label, dry_run=args.dry_run)


def build_parser(config: dict[str, Any], config_path: str, camera_mapping_path: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Photo organizer CLI")
    parser.add_argument("--config", default=config_path, help="Path to the JSON config file with your paths.")
    parser.add_argument("--camera-mapping", default=camera_mapping_path, help="Path to the JSON camera mapping file.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Sync files from main drive to backup drive.")
    sync_parser.add_argument("--main", default=config["paths"].get("backup_main", ""), help="Main drive / source folder.")
    sync_parser.add_argument("--backup", default=config["paths"].get("backup_copy", ""), help="Backup drive / destination folder.")
    sync_parser.add_argument("--dry-run", action="store_true", help="Preview changes without copying.")
    sync_parser.add_argument("--move-new", action="store_true", help="Remove files from main drive after copying them to backup.")
    sync_parser.add_argument("--progress-every", type=int, default=1000, help="Print sync progress every N scanned or compared files.")

    organize_parser = subparsers.add_parser("organize", help="Rename and sort media into category/year/date folders.")
    organize_parser.add_argument("--source", default=config["paths"].get("organize_source", ""), help="Incoming folder with camera, phone, and drone files.")
    organize_parser.add_argument("--dest", default=config["paths"].get("organize_dest", ""), help="Destination root folder for the organized library.")
    organize_parser.add_argument("--dry-run", action="store_true", help="Preview changes without moving files.")
    organize_parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N scanned/planned files.")

    green_parser = subparsers.add_parser("green-flagged", help="Copy digiKam green-flagged files.")
    green_parser.add_argument("--db", default=config["paths"].get("digikam_db", "") or None, help="Path to digikam4.db. If omitted, common locations are tried.")
    green_parser.add_argument("--dest", default=config["paths"].get("green_flagged_dest", ""), help="Destination root folder.")
    green_parser.add_argument("--dry-run", action="store_true", help="Preview changes without copying.")
    green_parser.add_argument("--label", default="Pick Label Accepted", choices=list(PICK_LABELS.keys()), help="Pick label to match.")

    dates_parser = subparsers.add_parser("missing-dates", help="Find files without dates and optionally set them from filenames.")
    dates_parser.add_argument("--source", default=config["paths"].get("organize_source", ""), help="Source folder to scan.")
    dates_parser.add_argument("--recursive", action="store_true", help="Scan subfolders recursively.")
    dates_parser.add_argument("--output", default=None, help="Optional output file for files without dates.")
    dates_parser.add_argument("--extensions", default=",".join(DEFAULT_CONFIG["extensions"]["find_missing_dates"]), help="Comma-separated list of file extensions to check.")
    dates_parser.add_argument("--set-dates", action="store_true", help="Set dates from the filename pattern XXX_YYYYMMDD_HHMMSS.")
    dates_parser.add_argument("--dry-run", action="store_true", help="Preview date changes without modifying files.")

    return parser


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    pre_parser.add_argument("--camera-mapping", default=str(DEFAULT_CAMERA_MAPPING_PATH))
    pre_args, remaining = pre_parser.parse_known_args()

    config = load_config(pre_args.config)
    mappings = load_camera_mapping(pre_args.camera_mapping)
    parser = build_parser(config, pre_args.config, pre_args.camera_mapping)
    args = parser.parse_args(remaining)
    args.config = pre_args.config
    args.camera_mapping = pre_args.camera_mapping

    try:
        if args.command == "sync":
            run_sync(args, config)
        elif args.command == "organize":
            run_organize(args, config, mappings)
        elif args.command == "green-flagged":
            run_green_flagged(args, config)
        elif args.command == "missing-dates":
            run_missing_dates(args, config)
        else:
            parser.error(f"Unknown command: {args.command}")
    except (FileNotFoundError, NotADirectoryError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()