"""
sanitize.py — strip superfluous metadata from audio files.

Removes tags that bloat files, cause playback database issues, or contain
custom/non-standard data while preserving essential identifying metadata.

Defaults (configurable):
  Keep: artist, albumartist, album, title, tracknumber, totaltracks,
        discnumber, totaldiscs, date, year, genre, composer,
        replaygain_* tags, embedded album art, lyrics, chapter info
  Strip: encoder noise, iTunes internals, MusicBrainz IDs, custom
         user-defined fields (TXXX in ID3, _* in FLAC), ID3v1 dupes

Custom user-defined fields are ALWAYS removed (not configurable).

Sanitization is destructive and not reversible by --undo.
"""

import os
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError, APIC, USLT, SYLT, CHAP, CTOC
from mutagen.mp4 import MP4

from boxraudio import ui


# Default essential tag whitelist (case-insensitive)
DEFAULT_KEEP_TAGS = {
    # Identification
    "artist", "albumartist", "album_artist",
    "album",
    "title",
    "tracknumber", "track", "totaltracks", "tracktotal",
    "discnumber", "disc", "totaldiscs", "disctotal",
    "date", "year",
    "genre",
    "composer",

    # ReplayGain (worked hard on these — never strip)
    "replaygain_track_gain", "replaygain_album_gain",
    "replaygain_track_peak", "replaygain_album_peak",
    "replaygain_reference_loudness",

    # Performance attribution
    "performer", "conductor",
}

# ID3 frame IDs we always keep (in addition to anything mapped from KEEP_TAGS)
DEFAULT_KEEP_ID3_FRAMES = {
    "TPE1",  # artist
    "TPE2",  # album artist
    "TALB",  # album
    "TIT2",  # title
    "TRCK",  # track number
    "TPOS",  # disc number
    "TDRC", "TYER", "TDRL",  # date / year
    "TCON",  # genre
    "TCOM",  # composer
    "TPE3",  # conductor
    "TPE4",  # remixer
    "TXXX:replaygain_track_gain",
    "TXXX:replaygain_album_gain",
    "TXXX:replaygain_track_peak",
    "TXXX:replaygain_album_peak",
    "TXXX:replaygain_reference_loudness",
}

# MP4 atom keys we always keep
DEFAULT_KEEP_MP4_ATOMS = {
    "\xa9ART",   # artist
    "aART",      # album artist
    "\xa9alb",   # album
    "\xa9nam",   # title
    "trkn",      # track number
    "disk",      # disc number
    "\xa9day",   # date
    "\xa9gen",   # genre
    "\xa9wrt",   # composer
    "----:com.apple.iTunes:replaygain_track_gain",
    "----:com.apple.iTunes:replaygain_album_gain",
    "----:com.apple.iTunes:replaygain_track_peak",
    "----:com.apple.iTunes:replaygain_album_peak",
}


# Tags to ALWAYS strip (regardless of configuration)
ALWAYS_STRIP_PATTERNS = [
    "musicbrainz_",        # MusicBrainz IDs — often very large
    "encoder",             # Encoder version string
    "encoded_by",
    "encoded-by",
    "encoding",
    "encodersettings",
    "itunes_cddb",
    "itunsmpb",
    "itunnorm",
    "itunpgap",
    "itunes_normalization",
    "comment",             # Comment fields — often contain noise
    "comments",
    "description",
    "purl",
    "rip",
    "ripper",
    "source",
    "media",
    "label",
    "publisher",
    "barcode",
    "catalognumber",
    "isrc",
    "originaldate",
    "originalalbum",
    "originalartist",
    "originalyear",
    "asin",
    "tool",                # Encoder tool
    "tool_name",
    "tool_version",
    "creation_time",
    "language",
    "compilation",
]


def _is_user_defined_flac(key: str) -> bool:
    """FLAC: tags starting with _ or __ are user-defined and always removed."""
    return key.startswith("_") or key.startswith("__")


def _should_strip(key: str, keep_set: set, strip_patterns: list) -> bool:
    """
    Determine whether a tag key should be removed.
    Returns True if the tag should be stripped.
    """
    key_lower = key.lower()
    if key_lower in keep_set:
        return False
    # Always strip if it matches a strip pattern
    for pattern in strip_patterns:
        if pattern in key_lower:
            return True
    # If not in keep list and not explicitly known, strip it
    return True


def _picture_info(audio_or_pic) -> tuple:
    """Return (count, total_bytes) of embedded album art."""
    if isinstance(audio_or_pic, FLAC):
        pics = audio_or_pic.pictures
        return len(pics), sum(len(p.data) for p in pics)
    return 0, 0


def sanitize_flac(filepath: str,
                  keep_tags: set,
                  strip_art: bool = False,
                  strip_lyrics: bool = False,
                  strip_patterns: list = None,
                  dry_run: bool = True) -> dict:
    """
    Sanitize a FLAC file's tags.
    Returns a dict describing what was removed.
    """
    if strip_patterns is None:
        strip_patterns = ALWAYS_STRIP_PATTERNS

    result = {
        "path":      filepath,
        "format":    "flac",
        "removed":   [],
        "kept":      [],
        "art_bytes": 0,
        "errors":    [],
        "changed":   False,
    }

    try:
        audio = FLAC(filepath)
    except Exception as e:
        result["errors"].append(f"open failed: {e}")
        return result

    # Examine each tag
    to_remove = []
    for key in list(audio.keys()):
        key_lower = key.lower()

        # Custom user-defined fields — always strip
        if _is_user_defined_flac(key):
            to_remove.append(key)
            continue

        # Lyrics handling
        if "lyrics" in key_lower or "uslt" in key_lower:
            if strip_lyrics:
                to_remove.append(key)
            else:
                result["kept"].append(key)
            continue

        # Standard strip logic
        if _should_strip(key, keep_tags, strip_patterns):
            to_remove.append(key)
        else:
            result["kept"].append(key)

    # Album art
    if strip_art and audio.pictures:
        result["art_bytes"] = sum(len(p.data) for p in audio.pictures)
        if not dry_run:
            audio.clear_pictures()
        result["removed"].append(f"<{len(audio.pictures)} embedded picture(s)>")
        result["changed"] = True

    if to_remove:
        result["removed"].extend(to_remove)
        result["changed"] = True
        if not dry_run:
            for k in to_remove:
                del audio[k]

    if result["changed"] and not dry_run:
        try:
            audio.save()
        except Exception as e:
            result["errors"].append(f"save failed: {e}")

    return result


def sanitize_mp3(filepath: str,
                 keep_tags: set,
                 strip_art: bool = False,
                 strip_lyrics: bool = False,
                 strip_patterns: list = None,
                 dry_run: bool = True) -> dict:
    """Sanitize an MP3 file's ID3 tags."""
    if strip_patterns is None:
        strip_patterns = ALWAYS_STRIP_PATTERNS

    result = {
        "path":      filepath,
        "format":    "mp3",
        "removed":   [],
        "kept":      [],
        "art_bytes": 0,
        "errors":    [],
        "changed":   False,
    }

    try:
        audio = ID3(filepath)
    except ID3NoHeaderError:
        return result  # No tags, nothing to do
    except Exception as e:
        result["errors"].append(f"open failed: {e}")
        return result

    # Build set of essential frame IDs from KEEP_TAGS
    keep_id3 = set(DEFAULT_KEEP_ID3_FRAMES)

    # Examine each frame
    frames_to_remove = []
    chapter_frames_kept = []
    for frame_id in list(audio.keys()):
        # Chapter frames (CHAP and CTOC) — always keep
        if frame_id.startswith("CHAP") or frame_id.startswith("CTOC"):
            chapter_frames_kept.append(frame_id)
            result["kept"].append(frame_id)
            continue

        # Lyrics frames
        if frame_id.startswith("USLT") or frame_id.startswith("SYLT"):
            if strip_lyrics:
                frames_to_remove.append(frame_id)
            else:
                result["kept"].append(frame_id)
            continue

        # Album art (APIC)
        if frame_id.startswith("APIC"):
            apic = audio[frame_id]
            result["art_bytes"] += len(apic.data) if hasattr(apic, "data") else 0
            if strip_art:
                frames_to_remove.append(frame_id)
            else:
                result["kept"].append(frame_id)
            continue

        # TXXX user-defined frames — always strip unless on whitelist
        if frame_id.startswith("TXXX"):
            if frame_id in keep_id3:
                result["kept"].append(frame_id)
            else:
                frames_to_remove.append(frame_id)
            continue

        # Otherwise check if it's a keeper
        if frame_id in keep_id3:
            result["kept"].append(frame_id)
        else:
            # Check against strip patterns
            should_strip = False
            for pat in strip_patterns:
                if pat in frame_id.lower():
                    should_strip = True
                    break
            if should_strip or frame_id not in keep_id3:
                frames_to_remove.append(frame_id)
            else:
                result["kept"].append(frame_id)

    if frames_to_remove:
        result["removed"].extend(frames_to_remove)
        result["changed"] = True
        if not dry_run:
            for frame_id in frames_to_remove:
                audio.delall(frame_id)

    if result["changed"] and not dry_run:
        try:
            audio.save(filepath, v1=0, v2_version=4)  # No ID3v1, ID3v2.4
        except Exception as e:
            result["errors"].append(f"save failed: {e}")

    return result


def sanitize_mp4(filepath: str,
                 keep_tags: set,
                 strip_art: bool = False,
                 strip_lyrics: bool = False,
                 strip_patterns: list = None,
                 dry_run: bool = True) -> dict:
    """Sanitize an M4A/MP4 file's tags."""
    if strip_patterns is None:
        strip_patterns = ALWAYS_STRIP_PATTERNS

    result = {
        "path":      filepath,
        "format":    Path(filepath).suffix.lower().lstrip("."),
        "removed":   [],
        "kept":      [],
        "art_bytes": 0,
        "errors":    [],
        "changed":   False,
    }

    try:
        audio = MP4(filepath)
    except Exception as e:
        result["errors"].append(f"open failed: {e}")
        return result

    keep_mp4 = set(DEFAULT_KEEP_MP4_ATOMS)

    atoms_to_remove = []
    for atom in list(audio.keys()):
        # Album art
        if atom == "covr":
            for pic in audio[atom]:
                result["art_bytes"] += len(pic)
            if strip_art:
                atoms_to_remove.append(atom)
            else:
                result["kept"].append(atom)
            continue

        # Lyrics
        if atom == "\xa9lyr":
            if strip_lyrics:
                atoms_to_remove.append(atom)
            else:
                result["kept"].append(atom)
            continue

        # Custom freeform atoms — these start with "----:"
        if atom.startswith("----:"):
            if atom in keep_mp4:
                result["kept"].append(atom)
            else:
                atoms_to_remove.append(atom)
            continue

        # Otherwise check keep list
        if atom in keep_mp4:
            result["kept"].append(atom)
        else:
            atoms_to_remove.append(atom)

    if atoms_to_remove:
        result["removed"].extend(atoms_to_remove)
        result["changed"] = True
        if not dry_run:
            for a in atoms_to_remove:
                del audio[a]

    if result["changed"] and not dry_run:
        try:
            audio.save()
        except Exception as e:
            result["errors"].append(f"save failed: {e}")

    return result


def sanitize_file(filepath: str,
                  keep_tags: set = None,
                  strip_art: bool = False,
                  strip_lyrics: bool = False,
                  strip_patterns: list = None,
                  dry_run: bool = True) -> dict:
    """
    Sanitize a single audio file. Dispatches to the format-specific function.
    """
    if keep_tags is None:
        keep_tags = DEFAULT_KEEP_TAGS

    # Normalize keep_tags to lowercase
    keep_tags = {k.lower() for k in keep_tags}

    ext = Path(filepath).suffix.lower()
    if ext == ".flac":
        return sanitize_flac(filepath, keep_tags, strip_art, strip_lyrics,
                             strip_patterns, dry_run)
    elif ext == ".mp3":
        return sanitize_mp3(filepath, keep_tags, strip_art, strip_lyrics,
                            strip_patterns, dry_run)
    elif ext in (".m4a", ".m4p", ".alac", ".mp4"):
        return sanitize_mp4(filepath, keep_tags, strip_art, strip_lyrics,
                            strip_patterns, dry_run)
    else:
        return {
            "path":      filepath,
            "format":    ext.lstrip("."),
            "removed":   [],
            "kept":      [],
            "art_bytes": 0,
            "errors":    [f"unsupported format: {ext}"],
            "changed":   False,
        }


def sanitize_directory(directory: str,
                       keep_tags: set = None,
                       strip_art: bool = False,
                       strip_lyrics: bool = False,
                       strip_patterns: list = None,
                       dry_run: bool = True,
                       extensions: list = None,
                       art_size_threshold_mb: float = 1.0) -> dict:
    """
    Sanitize all audio files in a directory.
    Reports oversized embedded art files separately.

    Returns:
        {
            'total_files':       int
            'modified_files':    int
            'errors':            list
            'oversized_art':     list of (filepath, art_bytes)
            'total_bytes_freed': int (only meaningful if not dry_run)
            'details':           list of per-file result dicts
        }
    """
    from boxraudio.scanner import AUDIO_EXTENSIONS

    if extensions is None:
        extensions = AUDIO_EXTENSIONS

    if not os.path.isdir(directory):
        ui.error(f"Directory not found: {directory}")
        return {
            "total_files":       0,
            "modified_files":    0,
            "errors":            [f"directory not found: {directory}"],
            "oversized_art":     [],
            "total_bytes_freed": 0,
            "details":           [],
        }

    files = []
    for root, dirs, filenames in os.walk(directory):
        for fn in filenames:
            if Path(fn).suffix.lower() in extensions:
                files.append(os.path.join(root, fn))

    ui.info(f"Found {len(files):,} audio files to sanitize")

    results = []
    modified = 0
    errors = []
    oversized = []
    total_freed = 0

    art_threshold_bytes = int(art_size_threshold_mb * 1024 * 1024)

    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Sanitizing", total=len(files))
        for fp in files:
            r = sanitize_file(fp, keep_tags, strip_art, strip_lyrics,
                              strip_patterns, dry_run)
            results.append(r)
            if r.get("errors"):
                errors.extend((fp, e) for e in r["errors"])
            if r.get("changed"):
                modified += 1
            if r.get("art_bytes", 0) > art_threshold_bytes:
                oversized.append((fp, r["art_bytes"]))
            if strip_art and r.get("changed"):
                total_freed += r.get("art_bytes", 0)
            progress.advance(task)

    return {
        "total_files":       len(files),
        "modified_files":    modified,
        "errors":            errors,
        "oversized_art":     oversized,
        "total_bytes_freed": total_freed,
        "details":           results,
    }
