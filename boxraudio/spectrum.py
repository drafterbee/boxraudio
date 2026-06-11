"""
spectrum.py — FFT-based lossy detection for FLAC files.

Analyzes audio files to detect lossy transcodes repackaged as lossless.
A genuine lossless file has frequency content extending up to its Nyquist
limit; a transcode from MP3/AAC has a brick-wall cutoff at the lossy
encoder's ceiling (typically 16, 19, 20, or 22 kHz).
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


LOSSY_CUTOFFS = {
    "MP3 (low bitrate, ~128k)":  16000,
    "MP3 (mid bitrate, ~192k)":  18500,
    "MP3 (high bitrate, ~256k)": 19500,
    "AAC (256k)":                20000,
    "AAC/MP3 (~320k)":           20500,
    "CD-source FLAC ceiling":    22000,
}

CUTOFF_TOLERANCE = 500


def is_available() -> bool:
    return NUMPY_AVAILABLE and SOUNDFILE_AVAILABLE


def _decode_with_ffmpeg(filepath: str, duration_secs: float = 30.0,
                        start_offset: float = None):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ["ffmpeg", "-loglevel", "error", "-y"]
        if start_offset is not None:
            cmd.extend(["-ss", str(start_offset)])
        cmd.extend(["-i", filepath, "-t", str(duration_secs),
                    "-ac", "1",
                    "-c:a", "pcm_s16le",
                    tmp_path])
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


def _decode_audio(filepath: str, duration_secs: float = 30.0):
    try:
        info = sf.info(filepath)
        total_secs = info.frames / info.samplerate
        start = max(0, (total_secs - duration_secs) / 2)
        frames_to_read = int(duration_secs * info.samplerate)
        start_frame = int(start * info.samplerate)

        samples, sr = sf.read(filepath,
                              start=start_frame,
                              frames=frames_to_read,
                              dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return samples, sr
    except Exception:
        pass

    return _decode_with_ffmpeg(filepath, duration_secs)


def _find_high_freq_cutoff(samples, sample_rate: int,
                            noise_floor_db: float = -90.0) -> float:
    if len(samples) == 0:
        return 0.0

    window_size = 4096
    hop_size = window_size // 2
    if len(samples) < window_size:
        return 0.0

    n_windows = (len(samples) - window_size) // hop_size + 1
    avg_spectrum = np.zeros(window_size // 2 + 1)
    window_func = np.hanning(window_size)

    for i in range(min(n_windows, 100)):
        offset = i * hop_size
        chunk = samples[offset:offset + window_size] * window_func
        spectrum = np.abs(np.fft.rfft(chunk))
        avg_spectrum += spectrum

    avg_spectrum /= max(1, min(n_windows, 100))

    peak = avg_spectrum.max()
    if peak <= 0:
        return 0.0
    db_spectrum = 20 * np.log10(np.maximum(avg_spectrum, 1e-10) / peak)
    freqs = np.fft.rfftfreq(window_size, d=1.0/sample_rate)

    for i in range(len(db_spectrum) - 1, -1, -1):
        if db_spectrum[i] > noise_floor_db:
            return float(freqs[i])

    return 0.0


def analyze_file(filepath: str, duration_secs: float = 30.0,
                 noise_floor_db: float = -90.0) -> dict:
    if not is_available():
        return {
            "path":   filepath,
            "verdict": "error",
            "error":  "numpy or soundfile not available",
        }

    result = {
        "path":    filepath,
        "format":  Path(filepath).suffix.lower().lstrip("."),
        "verdict": "unknown",
        "confidence": 0.0,
    }

    samples, sr = _decode_audio(filepath, duration_secs)
    if samples is None or sr is None or sr == 0:
        result["verdict"] = "error"
        result["error"]   = "could not decode audio"
        return result

    nyquist = sr / 2.0
    cutoff = _find_high_freq_cutoff(samples, sr, noise_floor_db)

    result["sample_rate"]  = sr
    result["nyquist"]      = nyquist
    result["cutoff_hz"]    = cutoff
    result["cutoff_ratio"] = cutoff / nyquist if nyquist > 0 else 0.0

    cutoff_ratio = result["cutoff_ratio"]
    if cutoff_ratio >= 0.95:
        result["verdict"]    = "lossless"
        result["confidence"] = 0.95
    elif cutoff_ratio >= 0.88:
        result["verdict"]    = "lossless"
        result["confidence"] = 0.75
    else:
        suspected = None
        best_diff = float("inf")
        for name, freq in LOSSY_CUTOFFS.items():
            diff = abs(cutoff - freq)
            if diff < CUTOFF_TOLERANCE and diff < best_diff:
                suspected = name
                best_diff = diff

        if suspected:
            result["verdict"]          = "transcode"
            result["confidence"]       = 0.9
            result["suspected_source"] = suspected
        elif cutoff_ratio < 0.75:
            result["verdict"]    = "transcode"
            result["confidence"] = 0.7
            result["suspected_source"] = "Unknown lossy source"
        else:
            result["verdict"]    = "suspicious"
            result["confidence"] = 0.5

    return result


def analyze_directory(directory: str, extensions: list = None,
                      progress_callback=None) -> list:
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
