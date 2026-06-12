"""
constants.py — central location for tunable defaults across BoxR.

If you find yourself wanting to change a magic number to tune behavior,
it should live here with a comment explaining what it does and why
the default is what it is.
"""

# ─── Scanner ─────────────────────────────────────────────────────────────────
# Tag reads that take longer than this print a SLOW warning so users can
# investigate problem files (typically bloated artwork or corrupt frames).
SLOW_FILE_THRESHOLD_SECS = 2.0

# ─── Cache ───────────────────────────────────────────────────────────────────
# Bulk writes are batched this many entries at a time to avoid blocking on
# every single file insertion during a parallel scan.
CACHE_WRITE_BATCH_SIZE = 100

# ─── Spectrum (audit-quality) ────────────────────────────────────────────────
# Five sample windows from across the file. The maximum cutoff observed
# wins (loudest sections reveal the true ceiling). More windows = more
# accurate but slower.
SPECTRUM_WINDOW_POSITIONS = [0.10, 0.25, 0.50, 0.75, 0.90]
SPECTRUM_WINDOW_DURATION_SECS = 10.0

# Windows below this RMS loudness are skipped — quiet sections produce
# misleading spectra (lossy encoders preserve more bits in quiet passages).
SPECTRUM_LOUDNESS_GATE_DBFS = -30.0

# Slope thresholds (dB per kHz around the cutoff):
#   STEEP: brick wall — definitive encoder cutoff
#   MODERATE: ambiguous (could be natural rolloff or low-bitrate cutoff)
#   Above MODERATE: gradual rolloff, typical of lossless content
SPECTRUM_SLOPE_STEEP    = -50.0
SPECTRUM_SLOPE_MODERATE = -20.0

# Bins more than this many dB below the peak are considered noise floor.
SPECTRUM_NOISE_FLOOR_DB = -90.0

# Lossy encoder ceiling matching tolerance (Hz).
SPECTRUM_CUTOFF_TOLERANCE = 500

# ─── Transaction log ─────────────────────────────────────────────────────────
# Quarantine files (for --undo) older than this are deleted automatically
# at the end of a successful pipeline run.
QUARANTINE_MAX_AGE_SECS = 30 * 24 * 3600  # 30 days

# ─── Resume / checkpoints ────────────────────────────────────────────────────
# Checkpoint files older than this are considered stale and cleaned up.
CHECKPOINT_MAX_AGE_SECS = 7 * 24 * 3600  # 7 days

# ─── Audits ──────────────────────────────────────────────────────────────────
# Embedded album art larger than this is flagged by --audit-tags. Default
# is 1 MB — anything over this is usually unnecessary for portable playback
# and is a common source of file bloat.
DEFAULT_ART_SIZE_THRESHOLD_MB = 1.0

# ─── Fingerprint matching ────────────────────────────────────────────────────
# Two fingerprints with similarity at or above this are considered a match.
FINGERPRINT_MATCH_THRESHOLD = 0.95
