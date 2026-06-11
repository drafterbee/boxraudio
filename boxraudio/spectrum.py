"""
spectrum.py — FFT-based lossy detection (Phase 2).
"""

ENABLED = False


def is_available() -> bool:
    try:
        import numpy  # noqa
        import scipy  # noqa
        return True
    except ImportError:
        return False


def analyze_file(filepath: str):
    raise NotImplementedError("Phase 2 feature")
