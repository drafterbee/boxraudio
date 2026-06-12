"""
spectrum.py — FFT-based lossy detection with multi-window analysis.

Analyzes audio files to detect lossy transcodes repackaged as lossless.
Genuine lossless content has gradual high-frequency rolloff; lossy transcodes
have a sharp "brick wall" cutoff at the encoder's ceiling.

This version improves accuracy with:
1. Brick-wall slope detection — measure how sharply energy drops at the cutoff
2. Multi-window analysis — 5 windows from across the file, use max cutoff
3. Loudness gating — only analyze loud sections where transcoders can't hide
4. Evidence reporting — full reasoning attached to every verdict

Verdict logic considers BOTH cutoff frequency AND slope sharpness.
"""

import os
import subprocess
import tempfile
from pathlib import Path

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False

from boxraudio.constants import (
    SPECTRUM_WINDOW_POSITIONS as WINDOW_POSITIONS,
    SPECTRUM_WINDOW_DURATION_SECS as WINDOW_DURATION,
    SPECTRUM_LOUDNESS_GATE_DBFS as LOUDNESS_GATE_DBFS,
    SPECTRUM_SLOPE_STEEP as SLOPE_STEEP,
    SPECTRUM_SLOPE_MODERATE as SLOPE_MODERATE,
    SPECTRUM_NOISE_FLOOR_DB as NOISE_FLOOR_DB,
    SPECTRUM_CUTOFF_TOLERANCE as CUTOFF_TOLERANCE,
)


# Known lossy encoder cutoff ceilings (Hz)
LOSSY_CUTOFFS = {
    "MP3 (~128k)":  16000,
    "MP3 (~192k)":  18500,
    "MP3 (~256k)":  19500,
    "AAC (~256k)":  20000,
    "AAC/MP3 (~320k)": 20500,
}


def is_available() -> bool:
    return NUMPY_AVAILABLE and SOUNDFILE_AVAILABLE


# ─── Decoding helpers ─────────────────────────────────────────────────────────

def _decode_with_ffmpeg(filepath: str, start_secs: float, duration_secs: float):
    """Decode a chunk of audio via ffmpeg into a temp WAV."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ["ffmpeg", "-loglevel", "error", "-y",
               "-ss", str(start_secs),
               "-i", filepath,
               "-t", str(duration_secs),
               "-ac", "1",
               "-c:a", "pcm_s16le",
               tmp_path]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            return None, None

        samples, sr = sf.read(tmp_path)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return samples, sr
    except (subprocess.TimeoutExpired, OSError, Exception):
        return None, None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _decode_window(filepath: str, start_secs: float, duration_secs: float):
    """Decode a window of audio. Returns (samples_array, sample_rate)."""
    try:
        info = sf.info(filepath)
        if info.samplerate == 0 or info.frames == 0:
            return None, None
        start_frame = int(start_secs * info.samplerate)
        frames = int(duration_secs * info.samplerate)
        if start_frame + frames > info.frames:
            frames = info.frames - start_frame
        if frames < info.samplerate:  # less than 1 second
            return None, None
        samples, sr = sf.read(filepath, start=start_frame, frames=frames,
                              dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return samples, sr
    except Exception:
        pass

    return _decode_with_ffmpeg(filepath, start_secs, duration_secs)


def _get_duration_secs(filepath: str) -> float:
    """Try to get audio duration via soundfile, falling back to ffprobe."""
    try:
        info = sf.info(filepath)
        if info.frames and info.samplerate:
            return info.frames / info.samplerate
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ffprobe", "-loglevel", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass

    return 0.0


# ─── Analysis primitives ──────────────────────────────────────────────────────

def _rms_dbfs(samples) -> float:
    """Compute RMS loudness of a sample window in dBFS."""
    if len(samples) == 0:
        return -float("inf")
    rms = np.sqrt(np.mean(samples ** 2))
    if rms <= 0:
        return -float("inf")
    # Reference: full-scale sine has RMS of ~0.707, so dBFS = 20*log10(rms/0.707)
    # But for simplicity we treat 1.0 as full scale.
    return 20.0 * np.log10(rms)


def _compute_spectrum(samples, sample_rate: int):
    """
    Compute averaged FFT spectrum in dB from a sample window.
    Returns (frequencies_array, db_spectrum_array).
    """
    window_size = 4096
    hop_size    = window_size // 2

    if len(samples) < window_size:
        return None, None

    n_windows = (len(samples) - window_size) // hop_size + 1
    n_windows = min(n_windows, 100)  # cap for speed

    avg_spectrum = np.zeros(window_size // 2 + 1)
    window_func  = np.hanning(window_size)

    for i in range(n_windows):
        offset = i * hop_size
        chunk = samples[offset:offset + window_size] * window_func
        spectrum = np.abs(np.fft.rfft(chunk))
        avg_spectrum += spectrum

    avg_spectrum /= n_windows

    peak = avg_spectrum.max()
    if peak <= 0:
        return None, None

    db = 20.0 * np.log10(np.maximum(avg_spectrum, 1e-10) / peak)
    freqs = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)
    return freqs, db


def _find_cutoff_freq(freqs, db, noise_floor_db: float = NOISE_FLOOR_DB) -> float:
    """
    Find the highest frequency with energy above the noise floor.
    Walks from Nyquist downward.
    """
    for i in range(len(db) - 1, -1, -1):
        if db[i] > noise_floor_db:
            return float(freqs[i])
    return 0.0


def _compute_slope_at_cutoff(freqs, db, cutoff_hz: float) -> float:
    """
    Compute the spectral slope (dB per kHz) in a window around the cutoff.
    Returns negative numbers (energy drops with increasing frequency).
    """
    if cutoff_hz <= 0:
        return 0.0

    # Look 500Hz below and 500Hz above the cutoff
    below_low  = cutoff_hz - 1000
    below_high = cutoff_hz - 200
    above_low  = cutoff_hz - 200
    above_high = cutoff_hz + 500

    below_mask = (freqs >= below_low) & (freqs <= below_high)
    above_mask = (freqs >= above_low) & (freqs <= above_high)

    if not below_mask.any() or not above_mask.any():
        return 0.0

    below_db = db[below_mask].mean()
    above_db = db[above_mask].mean()

    # Distance in kHz between the centers
    below_center = (below_low + below_high) / 2.0
    above_center = (above_low + above_high) / 2.0
    delta_khz = (above_center - below_center) / 1000.0

    if delta_khz <= 0:
        return 0.0

    return (above_db - below_db) / delta_khz


# ─── Per-window analysis ──────────────────────────────────────────────────────

def _analyze_window(samples, sample_rate: int) -> dict:
    """Analyze a single window, returning loudness, cutoff, and slope."""
    result = {
        "loudness_dbfs": _rms_dbfs(samples),
        "cutoff_hz":     0.0,
        "slope":         0.0,
        "usable":        False,
    }

    # Loudness gate
    if result["loudness_dbfs"] < LOUDNESS_GATE_DBFS:
        return result

    freqs, db = _compute_spectrum(samples, sample_rate)
    if freqs is None:
        return result

    cutoff = _find_cutoff_freq(freqs, db)
    slope  = _compute_slope_at_cutoff(freqs, db, cutoff)

    result["cutoff_hz"] = cutoff
    result["slope"]     = slope
    result["usable"]    = True
    return result


# ─── Verdict logic ────────────────────────────────────────────────────────────

def _decide_verdict(cutoff: float, slope: float, sample_rate: int) -> dict:
    """
    Decide verdict based on cutoff frequency AND slope sharpness.

    Returns:
        {
            "verdict":          "lossless" | "transcode" | "suspicious",
            "confidence":       0.0–1.0,
            "suspected_source": str or None,
            "reasons":          list of human-readable strings,
        }
    """
    reasons = []
    suspected_source = None

    # Match against known lossy ceilings first
    best_match = None
    best_diff  = float("inf")
    for name, freq in LOSSY_CUTOFFS.items():
        diff = abs(cutoff - freq)
        if diff < CUTOFF_TOLERANCE and diff < best_diff:
            best_match = name
            best_diff  = diff

    is_hires = sample_rate > 50000
    nyquist  = sample_rate / 2.0
    cutoff_ratio = cutoff / nyquist if nyquist > 0 else 0.0

    # ── Hi-res file logic ────────────────────────────────────────────────────
    if is_hires:
        if cutoff > 24000:
            reasons.append(
                f"Content extends to {cutoff/1000:.1f}kHz — no lossy codec "
                f"extends this high"
            )
            return {
                "verdict":          "lossless",
                "confidence":       0.95,
                "suspected_source": None,
                "reasons":          reasons,
            }
        elif cutoff > 22000:
            reasons.append(
                f"Content extends to {cutoff/1000:.1f}kHz at {sample_rate}Hz — "
                f"likely CD-source upsampled to hi-res, still lossless"
            )
            return {
                "verdict":          "lossless",
                "confidence":       0.85,
                "suspected_source": None,
                "reasons":          reasons,
            }
        # Hi-res with cutoff below CD Nyquist — suspicious, check for lossy match
        if best_match and slope <= SLOPE_STEEP:
            reasons.append(
                f"Hi-res file but cutoff at {cutoff/1000:.1f}kHz matches "
                f"{best_match} (±{int(best_diff)}Hz)"
            )
            reasons.append(
                f"Brick-wall slope of {slope:.0f} dB/kHz confirms encoder cutoff"
            )
            return {
                "verdict":          "transcode",
                "confidence":       0.95,
                "suspected_source": best_match,
                "reasons":          reasons,
            }
        if best_match:
            reasons.append(
                f"Hi-res file but cutoff at {cutoff/1000:.1f}kHz matches "
                f"{best_match} (±{int(best_diff)}Hz)"
            )
            reasons.append(
                f"Rolloff slope of {slope:.0f} dB/kHz is ambiguous (not a clear brick wall)"
            )
            return {
                "verdict":          "suspicious",
                "confidence":       0.6,
                "suspected_source": best_match,
                "reasons":          reasons,
            }
        if slope <= SLOPE_STEEP:
            reasons.append(
                f"Hi-res file with cutoff at {cutoff/1000:.1f}kHz and brick-wall "
                f"slope of {slope:.0f} dB/kHz — likely transcoded from unknown lossy source"
            )
            return {
                "verdict":          "transcode",
                "confidence":       0.75,
                "suspected_source": "Unknown lossy source",
                "reasons":          reasons,
            }
        reasons.append(
            f"Hi-res file with low cutoff {cutoff/1000:.1f}kHz but gradual slope "
            f"{slope:.0f} dB/kHz — possibly hi-res with quiet content"
        )
        return {
            "verdict":          "suspicious",
            "confidence":       0.5,
            "suspected_source": None,
            "reasons":          reasons,
        }

    # ── CD-spec file logic (44.1 or 48 kHz) ──────────────────────────────────
    # Strong lossless signal: cutoff near Nyquist
    if cutoff_ratio >= 0.95:
        reasons.append(
            f"Content extends to {cutoff/1000:.1f}kHz "
            f"({cutoff_ratio:.0%} of Nyquist) — natural lossless ceiling"
        )
        return {
            "verdict":          "lossless",
            "confidence":       0.95,
            "suspected_source": None,
            "reasons":          reasons,
        }

    # Brick-wall + matching lossy ceiling = definitive transcode
    if best_match and slope <= SLOPE_STEEP:
        reasons.append(
            f"Cutoff at {cutoff/1000:.1f}kHz matches {best_match} "
            f"(±{int(best_diff)}Hz)"
        )
        reasons.append(
            f"Brick-wall slope of {slope:.0f} dB/kHz confirms encoder cutoff"
        )
        return {
            "verdict":          "transcode",
            "confidence":       0.95,
            "suspected_source": best_match,
            "reasons":          reasons,
        }

    # Matching lossy ceiling but gradual slope — suspicious
    if best_match and slope > SLOPE_MODERATE:
        reasons.append(
            f"Cutoff at {cutoff/1000:.1f}kHz matches {best_match}"
        )
        reasons.append(
            f"But slope of {slope:.0f} dB/kHz is gradual — likely lossless "
            f"with natural rolloff in this range"
        )
        return {
            "verdict":          "lossless",
            "confidence":       0.7,
            "suspected_source": None,
            "reasons":          reasons,
        }

    # Brick-wall slope without matching ceiling — unknown lossy
    if slope <= SLOPE_STEEP and cutoff_ratio < 0.93:
        reasons.append(
            f"Brick-wall slope of {slope:.0f} dB/kHz at {cutoff/1000:.1f}kHz "
            f"indicates lossy encoder cutoff"
        )
        return {
            "verdict":          "transcode",
            "confidence":       0.8,
            "suspected_source": "Unknown lossy source",
            "reasons":          reasons,
        }

    # Gradual rolloff — likely genuine
    if slope > SLOPE_MODERATE and cutoff_ratio >= 0.85:
        reasons.append(
            f"Gradual rolloff (slope {slope:.0f} dB/kHz) with content to "
            f"{cutoff/1000:.1f}kHz — natural lossless rolloff"
        )
        return {
            "verdict":          "lossless",
            "confidence":       0.85,
            "suspected_source": None,
            "reasons":          reasons,
        }

    # Cutoff well below Nyquist but moderate slope — vintage or modern compressed
    if cutoff_ratio >= 0.80:
        reasons.append(
            f"Cutoff at {cutoff/1000:.1f}kHz with moderate slope "
            f"{slope:.0f} dB/kHz — likely lossless (vintage analog or "
            f"heavily compressed master)"
        )
        return {
            "verdict":          "lossless",
            "confidence":       0.65,
            "suspected_source": None,
            "reasons":          reasons,
        }

    # Very low cutoff without strong brick-wall signature
    reasons.append(
        f"Low cutoff at {cutoff/1000:.1f}kHz with slope {slope:.0f} dB/kHz — "
        f"manual review recommended"
    )
    return {
        "verdict":          "suspicious",
        "confidence":       0.5,
        "suspected_source": None,
        "reasons":          reasons,
    }


# ─── Main file analysis ───────────────────────────────────────────────────────

def analyze_file(filepath: str) -> dict:
    """
    Analyze a single audio file using multi-window sampling.

    Returns:
        {
            "path":             input filepath
            "format":           extension (lowercase, no dot)
            "sample_rate":      audio sample rate (Hz)
            "nyquist":          sample_rate / 2
            "cutoff_hz":        max detected cutoff across all loud windows
            "slope":            steepest slope at that cutoff
            "cutoff_ratio":     cutoff_hz / nyquist
            "verdict":          "lossless" | "transcode" | "suspicious" | "error"
            "confidence":       0.0–1.0
            "suspected_source": str or None
            "reasons":          list of human-readable strings
            "windows_analyzed": total windows attempted
            "loud_windows":     windows above the loudness gate
            "error":            error message if applicable
        }
    """
    result = {
        "path":             filepath,
        "format":           Path(filepath).suffix.lower().lstrip("."),
        "verdict":          "unknown",
        "confidence":       0.0,
        "reasons":          [],
        "windows_analyzed": 0,
        "loud_windows":     0,
    }

    if not is_available():
        result["verdict"] = "error"
        result["error"]   = "numpy or soundfile not available"
        return result

    duration = _get_duration_secs(filepath)
    if duration <= 0:
        result["verdict"] = "error"
        result["error"]   = "could not determine duration"
        return result

    # Determine window positions, skipping ones that fall outside the file
    usable_windows = []
    sample_rate = None

    for pos in WINDOW_POSITIONS:
        start = pos * duration - (WINDOW_DURATION / 2)
        start = max(0, start)
        if start + WINDOW_DURATION > duration:
            start = max(0, duration - WINDOW_DURATION)

        samples, sr = _decode_window(filepath, start, WINDOW_DURATION)
        if samples is None or sr is None or sr == 0:
            continue
        sample_rate = sr  # last successful

        result["windows_analyzed"] += 1

        analysis = _analyze_window(samples, sr)
        if analysis["usable"]:
            result["loud_windows"] += 1
            usable_windows.append(analysis)

    if sample_rate is None:
        result["verdict"] = "error"
        result["error"]   = "could not decode any audio"
        return result

    result["sample_rate"] = sample_rate
    result["nyquist"]     = sample_rate / 2.0

    if not usable_windows:
        # All windows below loudness gate
        result["verdict"] = "suspicious"
        result["confidence"] = 0.3
        result["cutoff_hz"]  = 0.0
        result["slope"]      = 0.0
        result["cutoff_ratio"] = 0.0
        result["reasons"] = [
            f"All {result['windows_analyzed']} sample windows were below the "
            f"loudness gate ({LOUDNESS_GATE_DBFS} dBFS RMS) — cannot reliably "
            f"detect lossy artifacts on quiet content"
        ]
        return result

    # Aggregate: max cutoff (loudest sections reveal true ceiling),
    # steepest slope at that cutoff
    max_cutoff_window = max(usable_windows, key=lambda w: w["cutoff_hz"])
    cutoff = max_cutoff_window["cutoff_hz"]

    # Among windows whose cutoff is close to the max, find the steepest slope
    similar = [w for w in usable_windows
               if abs(w["cutoff_hz"] - cutoff) < 1000]
    steepest = min(similar, key=lambda w: w["slope"])
    slope = steepest["slope"]

    result["cutoff_hz"]    = cutoff
    result["slope"]        = slope
    result["cutoff_ratio"] = cutoff / result["nyquist"] if result["nyquist"] > 0 else 0.0

    verdict = _decide_verdict(cutoff, slope, sample_rate)
    result.update(verdict)
    result["reasons"] = [
        f"Analyzed {result['loud_windows']}/{result['windows_analyzed']} "
        f"loud windows; max cutoff {cutoff/1000:.1f}kHz, slope {slope:.0f} dB/kHz"
    ] + verdict["reasons"]

    return result


def analyze_directory(directory: str, extensions: list = None,
                      progress_callback=None) -> list:
    """Walk a directory and analyze all matching audio files."""
    if extensions is None:
        extensions = [".flac"]

    if not os.path.isdir(directory):
        return []

    files = []
    for root, dirs, filenames in os.walk(directory):
        for filename in filenames:
            if Path(filename).suffix.lower() in extensions:
                files.append(os.path.join(root, filename))

    results = []
    for i, fp in enumerate(files):
        result = analyze_file(fp)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(files), fp, result)

    return results
