"""
audits.py — library quality and tag audit reports.

Provides:
- Quality report: bitrate distribution, format breakdown, sample rates
- Tag audit: missing/incomplete metadata detection
- Spectrum audit: integration with spectrum.py for lossy detection
"""

import os
import re
from pathlib import Path
from collections import Counter, defaultdict

from boxraudio import ui
from boxraudio.scanner import (
    AUDIO_EXTENSIONS, collect_audio_files, scan_files_parallel
)
from boxraudio.cache import TagCache
from boxraudio import spectrum


# ─── Quality report ───────────────────────────────────────────────────────────

def format_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def bitrate_bucket(bitrate):
    """Categorize a bitrate into a labeled bucket."""
    if bitrate is None:
        return "unknown"
    if bitrate >= 900:        return "≥900 kbps (lossless)"
    if bitrate >= 320:        return "320 kbps"
    if bitrate >= 256:        return "256 kbps"
    if bitrate >= 192:        return "192 kbps"
    if bitrate >= 128:        return "128 kbps"
    return "<128 kbps"


def quality_report(directory: str, cache_path: str = None, workers: int = 4):
    """Generate a quality report for an audio library directory."""
    ui.section(f"Quality report — {directory}")

    if not os.path.isdir(directory):
        ui.error(f"Directory not found: {directory}")
        return

    cache = TagCache(cache_path or os.path.expanduser("~/.boxraudio_cache.db"))

    files = collect_audio_files(directory)
    ui.info(f"Found {len(files):,} audio files")

    if not files:
        ui.warning("Nothing to analyze.")
        return

    scan = scan_files_parallel(files, cache, workers=workers,
                               description="Reading tags")

    by_format       = Counter()
    by_bitrate      = Counter()
    by_format_size  = defaultdict(int)
    total_size      = 0
    total_duration  = 0.0
    missing_bitrate = 0
    missing_duration = 0

    for entry in scan["all"]:
        fmt = entry.get("format") or "unknown"
        by_format[fmt] += 1
        try:
            size = os.path.getsize(entry["filepath"])
            total_size += size
            by_format_size[fmt] += size
        except OSError:
            pass

        br = entry.get("bitrate")
        if br is None:
            missing_bitrate += 1
        else:
            by_bitrate[bitrate_bucket(br)] += 1

        dur = entry.get("duration")
        if dur is None:
            missing_duration += 1
        else:
            total_duration += dur

    # ── Summary table ────────────────────────────────────────────────────────
    summary = {
        "Total files":   f"{len(files):,}",
        "Total size":    format_bytes(total_size),
        "Total length":  f"{total_duration / 3600:.1f} hours",
        "Cache hits":    f"{scan['hits']:,}",
        "Cache misses":  f"{scan['misses']:,}",
    }
    ui.kv_table("Library overview", summary)

    # ── Format breakdown ─────────────────────────────────────────────────────
    fmt_rows = {}
    for fmt, count in by_format.most_common():
        size = by_format_size[fmt]
        fmt_rows[fmt.upper()] = f"{count:,}  ({format_bytes(size)})"
    ui.kv_table("Format breakdown", fmt_rows)

    # ── Bitrate breakdown ────────────────────────────────────────────────────
    if by_bitrate:
        br_rows = {}
        order = ["≥900 kbps (lossless)", "320 kbps", "256 kbps",
                 "192 kbps", "128 kbps", "<128 kbps", "unknown"]
        for bucket in order:
            if bucket in by_bitrate:
                br_rows[bucket] = f"{by_bitrate[bucket]:,}"
        ui.kv_table("Bitrate distribution", br_rows)

    if missing_bitrate:
        ui.warning(f"{missing_bitrate:,} files have no bitrate metadata")
    if missing_duration:
        ui.warning(f"{missing_duration:,} files have no duration metadata")


# ─── Tag audit ────────────────────────────────────────────────────────────────

SUSPICIOUS_TAG_VALUES = {
    "unknown", "unknown artist", "unknown album", "unknown title",
    "untitled", "n/a", "various", "[unknown]", "(unknown)",
    "track", "audio track", "untagged",
}

TRACK_NUM_PATTERN = re.compile(r"^track\s*\d+$", re.IGNORECASE)


def tag_audit(directory: str, cache_path: str = None, workers: int = 4):
    """Find files with missing or suspicious metadata."""
    ui.section(f"Tag audit — {directory}")

    if not os.path.isdir(directory):
        ui.error(f"Directory not found: {directory}")
        return

    cache = TagCache(cache_path or os.path.expanduser("~/.boxraudio_cache.db"))

    files = collect_audio_files(directory)
    ui.info(f"Found {len(files):,} audio files")

    if not files:
        ui.warning("Nothing to audit.")
        return

    scan = scan_files_parallel(files, cache, workers=workers,
                               description="Reading tags")

    missing_artist   = []
    missing_album    = []
    missing_title    = []
    missing_all      = []
    suspicious_tags  = []
    filename_mismatch = []

    for entry in scan["all"]:
        artist = entry.get("artist") or ""
        album  = entry.get("album")  or ""
        title  = entry.get("title")  or ""
        fp     = entry["filepath"]

        if not artist and not album and not title:
            missing_all.append(fp)
            continue

        if not artist: missing_artist.append(fp)
        if not album:  missing_album.append(fp)
        if not title:  missing_title.append(fp)

        for value, label in ((artist, "artist"), (album, "album"), (title, "title")):
            if value.lower() in SUSPICIOUS_TAG_VALUES or TRACK_NUM_PATTERN.match(value):
                suspicious_tags.append((fp, label, value))

        # Check title vs filename
        if title:
            filename_base = Path(fp).stem.lower()
            # Strip leading track numbers and separators
            filename_clean = re.sub(r"^[0-9\-_\s\.]+", "", filename_base).strip()
            if (filename_clean and
                title.lower() not in filename_clean and
                filename_clean not in title.lower()):
                # Only flag if there's no overlap at all
                if not any(word in filename_clean for word in title.lower().split() if len(word) > 3):
                    filename_mismatch.append((fp, title))

    # ── Report ───────────────────────────────────────────────────────────────
    issues = {
        "Files with no tags at all":     f"{len(missing_all):,}",
        "Missing artist tag":            f"{len(missing_artist):,}",
        "Missing album tag":             f"{len(missing_album):,}",
        "Missing title tag":             f"{len(missing_title):,}",
        "Suspicious tag values":         f"{len(suspicious_tags):,}",
        "Title doesn't match filename":  f"{len(filename_mismatch):,}",
    }
    ui.kv_table("Tag issues found", issues)

    if missing_all:
        ui.file_table("Files with no tags", missing_all, limit=10)
    if suspicious_tags:
        ui.file_table(
            "Suspicious tag values",
            [f"{label}=\"{value}\"  →  {fp}" for fp, label, value in suspicious_tags],
            limit=10,
        )


# ─── Spectrum audit ───────────────────────────────────────────────────────────

def spectrum_audit(directory: str, extensions: list = None):
    """Run spectrum analysis to detect lossy transcodes in FLAC files."""
    ui.section(f"Spectrum audit — {directory}")

    if not spectrum.is_available():
        ui.error("Spectrum analysis requires numpy and soundfile. "
                 "Install with: pip3 install numpy soundfile --break-system-packages")
        return

    if not os.path.isdir(directory):
        ui.error(f"Directory not found: {directory}")
        return

    extensions = extensions or [".flac"]
    files = []
    for root, dirs, filenames in os.walk(directory):
        for fn in filenames:
            if Path(fn).suffix.lower() in extensions:
                files.append(os.path.join(root, fn))

    ui.info(f"Found {len(files):,} files to analyze")
    if not files:
        return

    transcodes  = []
    suspicious  = []
    lossless    = []
    errors      = []

    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Analyzing spectra", total=len(files))
        for fp in files:
            result = spectrum.analyze_file(fp)
            verdict = result.get("verdict", "unknown")
            if verdict == "transcode":
                transcodes.append(result)
                progress.console.print(
                    f"  [red]TRANSCODE[/red] {fp}  "
                    f"({result.get('cutoff_hz', 0)/1000:.1f}kHz cutoff, "
                    f"{result.get('suspected_source', '?')})"
                )
            elif verdict == "suspicious":
                suspicious.append(result)
                progress.console.print(
                    f"  [yellow]SUSPECT[/yellow] {fp}  "
                    f"({result.get('cutoff_hz', 0)/1000:.1f}kHz cutoff)"
                )
            elif verdict == "lossless":
                lossless.append(result)
            else:
                errors.append(result)
            progress.advance(task)

    # ── Summary ──────────────────────────────────────────────────────────────
    summary = {
        "Lossless":   f"{len(lossless):,}",
        "Suspicious": f"{len(suspicious):,}",
        "Transcode":  f"{len(transcodes):,}",
        "Errors":     f"{len(errors):,}",
    }
    ui.kv_table("Spectrum audit summary", summary)

    if transcodes:
        ui.file_table(
            "Confirmed transcodes",
            [f"{r['path']}  ({r.get('suspected_source', '?')})" for r in transcodes],
            limit=20,
        )
    if suspicious:
        ui.file_table(
            "Suspicious files (manual review recommended)",
            [r["path"] for r in suspicious],
            limit=20,
        )
