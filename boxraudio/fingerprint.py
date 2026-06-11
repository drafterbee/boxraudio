"""
fingerprint.py — AcoustID/Chromaprint integration (Phase 2).
"""

ENABLED = False


def is_available() -> bool:
    try:
        import acoustid  # noqa
        import shutil
        return shutil.which("fpcalc") is not None
    except ImportError:
        return False


def fingerprint_file(filepath: str):
    raise NotImplementedError("Phase 2 feature")
