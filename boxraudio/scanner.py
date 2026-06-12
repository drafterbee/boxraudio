"""
scanner.py — parallel audio file tag scanner.
"""

import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen import File as MutagenFile

from boxraudio import ui
from boxraudio.cache import TagCache
from boxraudio.constants import SLOW_FILE_THRESHOLD_SECS, CACHE_WRITE_BATCH_SIZE


AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".aiff", ".aif",
    ".m4a", ".m4p", ".alac", ".ogg", ".opus",
    ".wv", ".ape", ".dsf", ".dff"
}


def _normalize(s):
    if not s:
        return ""
    return s.strip().lower()


def read_audio_metadata(filepath: str) -> dict:
    ext = Path(filepath).suffix.lower()
    fmt = ext.lstrip(".")
    result = {
        "artist":   None,
        "album":    None,
        "title":    None,
        "bitrate":  None,
        "duration": None,
        "format":   fmt,
    }

    try:
        if ext == ".flac":
            audio = FLAC(filepath)
            result["artist"]   = _normalize(audio.get("artist", [None])[0])
            result["album"]    = _normalize(audio.get("album",  [None])[0])
            result["title"]    = _normalize(audio.get("title",  [None])[0])
            if audio.info:
                result["duration"] = audio.info.length
                result["bitrate"]  = int(audio.info.bitrate / 1000) if audio.info.bitrate else None
        elif ext == ".mp3":
            audio = EasyID3(filepath)
            result["artist"] = _normalize(audio.get("artist", [None])[0])
            result["album"]  = _normalize(audio.get("album",  [None])[0])
            result["title"]  = _normalize(audio.get("title",  [None])[0])
            full = MutagenFile(filepath)
            if full and full.info:
                result["duration"] = full.info.length
                result["bitrate"]  = int(full.info.bitrate / 1000) if full.info.bitrate else None
        elif ext in (".m4a", ".m4p", ".alac"):
            audio = MP4(filepath)
            result["artist"] = _normalize(audio.get("\xa9ART", [None])[0])
            result["album"]  = _normalize(audio.get("\xa9alb", [None])[0])
            result["title"]  = _normalize(audio.get("\xa9nam", [None])[0])
            if audio.info:
                result["duration"] = audio.info.length
                result["bitrate"]  = int(audio.info.bitrate / 1000) if audio.info.bitrate else None
        else:
            audio = MutagenFile(filepath, easy=True)
            if audio is not None:
                result["artist"] = _normalize(audio.get("artist", [None])[0] if audio.get("artist") else None)
                result["album"]  = _normalize(audio.get("album",  [None])[0] if audio.get("album")  else None)
                result["title"]  = _normalize(audio.get("title",  [None])[0] if audio.get("title")  else None)
                if audio.info:
                    result["duration"] = audio.info.length
                    result["bitrate"]  = int(audio.info.bitrate / 1000) if hasattr(audio.info, "bitrate") and audio.info.bitrate else None
    except Exception:
        pass

    return result


def collect_audio_files(directory: str, extensions: set = None):
    if extensions is None:
        extensions = AUDIO_EXTENSIONS
    if not os.path.isdir(directory):
        return []
    found = []
    for root, dirs, files in os.walk(directory):
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in extensions:
                found.append(os.path.join(root, filename))
    return found


def _scan_single(filepath: str, cache: TagCache, slow_threshold: float = None):
    """
    Scan a single file. Returns (entry, status, elapsed_secs).
    status is one of: 'hit', 'miss', 'stat_failed'.
    """
    if slow_threshold is None:
        slow_threshold = SLOW_FILE_THRESHOLD_SECS
    try:
        st = os.stat(filepath)
    except OSError:
        return None, "stat_failed", 0.0

    mtime, size = int(st.st_mtime), st.st_size
    cached = cache.get(filepath, mtime, size)
    if cached:
        return cached, "hit", 0.0

    t_start = time.time()
    meta = read_audio_metadata(filepath)
    elapsed = time.time() - t_start

    entry = {
        "filepath":  filepath,
        "mtime":     mtime,
        "size":      size,
        "artist":    meta["artist"],
        "album":     meta["album"],
        "title":     meta["title"],
        "bitrate":   meta["bitrate"],
        "duration":  meta["duration"],
        "format":    meta["format"],
        "scanned_at": int(time.time()),
    }
    return entry, "miss", elapsed


def scan_files_parallel(filepaths, cache: TagCache,
                        workers: int = 4, description: str = "Scanning",
                        slow_threshold: float = None) -> dict:
    """
    Scan files in parallel. Slow files (>slow_threshold seconds) are reported
    inline while the scan runs.
    """
    if slow_threshold is None:
        slow_threshold = SLOW_FILE_THRESHOLD_SECS
    index = {}
    all_entries = []
    skipped = []
    slow_files = []
    hits, misses = 0, 0
    pending_writes = []
    write_batch_size = CACHE_WRITE_BATCH_SIZE

    progress = ui.make_progress()
    with progress:
        task = progress.add_task(description, total=len(filepaths))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_single, fp, cache, slow_threshold): fp for fp in filepaths}

            for future in as_completed(futures):
                entry, status, elapsed = future.result()
                progress.advance(task)

                if entry is None:
                    continue

                if status == "hit":
                    hits += 1
                elif status == "miss":
                    misses += 1
                    pending_writes.append(entry)
                    if len(pending_writes) >= write_batch_size:
                        cache.put_many(pending_writes)
                        pending_writes = []

                    # Report slow files
                    if elapsed > slow_threshold:
                        slow_files.append((entry["filepath"], elapsed))
                        progress.console.print(
                            f"  [yellow]SLOW[/yellow] ({elapsed:.1f}s): {entry['filepath']}"
                        )

                all_entries.append(entry)

                if entry["artist"] and entry["album"] and entry["title"]:
                    key = (entry["artist"], entry["album"], entry["title"])
                    if key not in index:
                        index[key] = entry["filepath"]
                else:
                    skipped.append(entry["filepath"])

        if pending_writes:
            cache.put_many(pending_writes)

    return {
        "index":   index,
        "all":     all_entries,
        "skipped": skipped,
        "hits":    hits,
        "misses":  misses,
        "slow":    slow_files,
    }
