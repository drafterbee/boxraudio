"""
fingerprint.py — AcoustID/Chromaprint fingerprinting for content-based dedup.

Uses fpcalc (chromaprint) to generate perceptual audio fingerprints that
identify audio content regardless of tag metadata or file format.

Fingerprints are stored in the SQLite cache alongside tag data and used
for local duplicate matching. Optional online AcoustID lookup is supported
if an API key is provided.
"""

import os
import json
import shutil
import subprocess
from pathlib import Path


def is_available() -> bool:
    """Returns True if fpcalc (chromaprint) is installed."""
    return shutil.which("fpcalc") is not None


def fingerprint_file(filepath: str, length_secs: int = 120) -> dict:
    """
    Generate a Chromaprint fingerprint for a single audio file.

    Returns:
        {
            'path':        input filepath
            'duration':    audio duration in seconds (from fpcalc)
            'fingerprint': base64-encoded fingerprint string
            'error':       error message if failed
        }
    """
    if not is_available():
        return {"path": filepath, "error": "fpcalc not installed"}

    if not os.path.isfile(filepath):
        return {"path": filepath, "error": "file not found"}

    try:
        result = subprocess.run(
            ["fpcalc", "-length", str(length_secs), "-json", filepath],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {
                "path":  filepath,
                "error": result.stderr.strip() or "fpcalc failed",
            }

        data = json.loads(result.stdout)
        return {
            "path":        filepath,
            "duration":    data.get("duration"),
            "fingerprint": data.get("fingerprint"),
        }
    except subprocess.TimeoutExpired:
        return {"path": filepath, "error": "fpcalc timeout"}
    except (json.JSONDecodeError, OSError) as e:
        return {"path": filepath, "error": str(e)}


def fingerprints_match(fp1: str, fp2: str, threshold: float = 0.95) -> tuple:
    """
    Compare two Chromaprint fingerprints for similarity.

    Returns (matches, score) where matches is True if similarity >= threshold.

    Chromaprint fingerprints are sequences of 32-bit integers. We compare
    them by computing Hamming distance between corresponding hash values
    and converting to a similarity score (1.0 = identical, 0.0 = unrelated).
    """
    if not fp1 or not fp2:
        return False, 0.0

    try:
        import base64
        import struct

        # Decode base64-url-safe to bytes
        # Chromaprint uses base64 with - and _ instead of + and /
        def decode_fp(fp):
            # Pad to multiple of 4 if needed
            padded = fp + "=" * (4 - len(fp) % 4)
            return base64.urlsafe_b64decode(padded)

        b1 = decode_fp(fp1)
        b2 = decode_fp(fp2)

        # Skip first 4 bytes (chromaprint header)
        b1 = b1[4:]
        b2 = b2[4:]

        # Convert to 32-bit unsigned integers
        n = min(len(b1), len(b2)) // 4
        if n == 0:
            return False, 0.0

        ints1 = struct.unpack(f">{n}I", b1[:n*4])
        ints2 = struct.unpack(f">{n}I", b2[:n*4])

        # Compute average Hamming distance
        total_bits  = n * 32
        diff_bits   = 0
        for a, b in zip(ints1, ints2):
            diff_bits += bin(a ^ b).count("1")

        similarity = 1.0 - (diff_bits / total_bits)
        return similarity >= threshold, similarity
    except Exception:
        return False, 0.0


def lookup_acoustid(fingerprint: str, duration: float,
                    api_key: str = None) -> list:
    """
    Look up a fingerprint in the AcoustID online database.
    Requires a free API key from https://acoustid.org/api-key.

    Returns a list of match dicts with artist/title/album info.
    """
    if not api_key:
        return []

    try:
        import acoustid
        results = acoustid.lookup(api_key, fingerprint, int(duration))
        matches = []
        for score, recording_id, title, artist in results:
            matches.append({
                "score":        score,
                "recording_id": recording_id,
                "title":        title,
                "artist":       artist,
            })
        return matches
    except Exception:
        return []
