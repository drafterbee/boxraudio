"""
errors.py — translate raw exceptions from underlying libraries into
human-readable, actionable messages.

Error handling contract for BoxR modules:
- Library functions (cache, scanner, operations, etc): catch exceptions and
  return them via the result dict (e.g. {"errors": [...]}) — never raise.
  Callers tally the errors and report.
- UI/CLI functions: catch top-level KeyboardInterrupt and unexpected
  exceptions in main(). Otherwise let exceptions propagate.
- Helpers in this module translate raw exception messages from mutagen,
  shutil, sqlite3, etc into actionable user-facing strings.
"""

# Common mutagen / tag-related error patterns and the user-facing messages
# we should show instead. Pattern is a substring of the raw exception message.
TAG_ERROR_TRANSLATIONS = [
    (
        "expected bytes",
        "Corrupt or malformed tag frame — try sanitizing this file "
        "with: boxraudio --sanitize-tags --target {path} --run",
    ),
    (
        "no tags",
        "No tags found in file",
    ),
    (
        "Permission denied",
        "Permission denied — check that the file is writable "
        "(macOS: System Settings > Privacy > Files and Folders may need adjustment)",
    ),
    (
        "Read-only file system",
        "File system is read-only — if this is a mounted volume, remount with write access",
    ),
    (
        "No such file or directory",
        "File not found — it may have been moved or deleted",
    ),
    (
        "Insufficient space",
        "Insufficient disk space at destination",
    ),
    (
        "No space left",
        "Disk is full",
    ),
    (
        "Operation timed out",
        "Operation timed out — the storage device may be slow or disconnected",
    ),
    (
        "is not a tag file",
        "File format is not supported or the file is corrupt",
    ),
]


def translate_error(raw_error: str, filepath: str = "") -> str:
    """
    Translate a raw exception message into something more useful.
    Returns the original message if no translation applies.
    """
    if not raw_error:
        return raw_error

    for pattern, replacement in TAG_ERROR_TRANSLATIONS:
        if pattern.lower() in raw_error.lower():
            return replacement.format(path=filepath or "FILE")

    return raw_error
