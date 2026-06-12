# BoxR

```
   __________             __________ 
   \______   \ _______  __\______   \
    |    |  _//  _ \  \/  /|       _/
    |    |   (  <_> >    < |    |   \
    |______  /\____/__/\_ \|____|_  /
           \/            \/       \/
```

> **Smart sync, lossless integrity — open-source music library management for people who care about their files.**

BoxR is a versatile media library tool that handles the full lifecycle of importing new music: moving files to your master backup, deduplicating lower-quality copies, sanitizing metadata, and synchronizing changes to portable devices — with smart caching, parallel processing, transaction logging, and audio integrity verification.

Invoke with the `boxraudio` command.

---

## Features

### Reliability & Safety
- **Transaction log with undo** — every operation is logged and reversible
- **Disk space pre-check** — fail fast before destructive operations
- **Interrupt-safe resume** — pick up where you left off after Ctrl+C

### Audio Intelligence
- **Spectrum analysis** — detect lossy files repackaged as FLAC via FFT analysis
- **AcoustID fingerprinting** — match audio content, not just tags (requires chromaprint)
- **Quality reporting** — see your library's bitrate and format composition
- **Tag quality checks** — flag files with missing or malformed metadata, plus oversized embedded album art

### Tag Management
- **Tag sanitization** — strip superfluous metadata that bloats files or causes playback database issues
- **Configurable keep list** — choose which tags to preserve per profile or globally
- **Album art and lyrics control** — kept by default, strippable on demand
- **Auto-sanitize on move** — opt-in cleanup as files flow from source to backup
- **Custom user-defined fields always removed** — non-negotiable for library hygiene

### User Experience
- **Rich terminal UI** — beautiful tables, progress bars, and panels
- **Interactive mode** — guided prompts via `--interactive`
- **Profiles** — save common workflows in `~/.boxraudio.yaml`
- **Pre-flight summary** — see exactly what will change before committing
- **Dry-run diff view** — colored summary of additions/removals before sync

### Performance
- **Parallel tag reading** — uses all CPU cores
- **SQLite cache** — fast lookups, atomic writes, query support
- **Incremental scanning** — skip unchanged directories

### Workflow
- **Multi-destination sync** — push to multiple devices in one command (sequential)
- **Stats and history** — track library growth, transfer activity, errors
- **Content-based dedup** — match duplicates by audio fingerprint, not just tags

---

## Installation

```bash
git clone https://github.com/drafterbee/boxraudio.git
cd boxraudio
bash install.sh
```

The installer will:
1. Install Python dependencies
2. Verify chromaprint is installed (or prompt to install via Homebrew)
3. Copy the package to `/usr/local/lib/boxraudio/`
4. Install the `boxraudio` command to `/usr/local/bin/`
5. Create default config at `~/.boxraudio.yaml`

System dependencies for audio analysis:
```bash
brew install chromaprint ffmpeg
```

---

## Quick Start

```bash
boxraudio --examples            # see usage examples
boxraudio --help                # full options reference
boxraudio --interactive         # guided setup
boxraudio --profile ipod --run  # run with a saved profile
boxraudio --explain --profile ipod    # see what a profile would do

# Library audits
boxraudio --audit-quality   --target "/path/to/library"
boxraudio --audit-tags      --target "/path/to/library"
boxraudio --report-quality  --target "/path/to/library"

# Tag sanitization
boxraudio --sanitize-tags --target "/path/to/library" --run
boxraudio --sanitize-tags --target ~/MusicLibrary --strip-art --run
boxraudio --sanitize-tags --target ~/MusicLibrary --keep-tag custom_field --run

# Stats and history
boxraudio --stats
boxraudio --history --detailed
boxraudio --cache-info

# Undo / resume
boxraudio --undo
boxraudio --resume
boxraudio --list-resumable

# Multi-destination sequential sync
boxraudio --profile ipod --profile phone --run

# Content-based dedup using fingerprints
boxraudio --profile ipod --dedupe-method fingerprint --run

# Full pipeline with auto-sanitize
boxraudio --profile ipod --sanitize-on-move --run
```

See [docs/WORKFLOWS.md](docs/WORKFLOWS.md) for common workflows and troubleshooting.

---

## Tag Sanitization

BoxR can strip superfluous metadata from your audio files to reduce bloat, prevent playback database issues, and standardize your library.

### What's kept by default

- **Identification:** `artist`, `albumartist`, `album`, `title`, `tracknumber`, `discnumber`, `date`, `year`, `genre`, `composer`, `performer`, `conductor`
- **ReplayGain (all variants):** `replaygain_track_gain`, `replaygain_album_gain`, peaks, reference loudness
- **Embedded album art** — kept by default, use `--strip-art` to remove
- **Lyrics** — kept by default, use `--strip-lyrics` to remove
- **Chapter info** — `CHAP` and `CTOC` frames are always preserved

### What's always removed

- **Custom user-defined fields** — `TXXX` frames in ID3, `_*` keys in FLAC, freeform atoms in MP4 (this is not configurable)
- **MusicBrainz IDs** — often very large
- **iTunes-specific tags** — `itunsmpb`, `itunnorm`, `itunpgap`, `itunes_cddb_*`
- **Encoder noise** — `encoder`, `encoded_by`, `encoder_settings`, `tool`, `tool_version`
- **Comment fields** — often contain ripper artifacts
- **Origin metadata** — `purl`, `rip`, `source`, `media`, `label`, `publisher`, `barcode`, `catalognumber`, `isrc`, `originaldate`, `originalalbum`, `asin`
- **Compilation flag**, `language`, `creation_time`

### Configuring extra tags to keep

In `~/.boxraudio.yaml`:

```yaml
defaults:
  keep_tags:
    - my_custom_field
    - another_field
```

Or via CLI for a one-off:

```bash
boxraudio --sanitize-tags --target ~/MusicLibrary --keep-tag my_field --keep-tag other --run
```

### Auto-sanitize on move

Enable in a profile to clean tags automatically as files flow from source to backup:

```yaml
profiles:
  ipod:
    source: ~/Desktop/MusicTFR
    backup: /Volumes/Media Backup/Audio/FLAC
    sanitize_on_move: true
```

Or via CLI:
```bash
boxraudio --profile ipod --sanitize-on-move --run
```

**Note:** Sanitization is irreversible — `--undo` does not restore stripped tags. Run a dry run first to see what would be removed.

---

## Configuration

Edit `~/.boxraudio.yaml`:

```yaml
defaults:
  cache_file: ~/.boxraudio_cache.db
  workers: 4
  size_only: true

  # Sanitization defaults
  sanitize_on_move: false
  # strip_art: false
  # strip_lyrics: false
  # keep_tags:
  #   - extra_field_to_keep

profiles:
  ipod:
    source: ~/Desktop/MusicTFR
    backup: /Volumes/Media Backup/Audio/FLAC
    destination: /Volumes/IPOD/Audio/FLAC
    sync_source: /Volumes/Media Backup/Audio/FLAC
    sync_destination: /Volumes/IPOD/Audio/FLAC
    dedupe_format: mp3
    dedupe_search:
      - /Volumes/Media Backup/Audio/MP3
      - /Volumes/IPOD/Audio/MP3
    clean_empty_dirs: true
    mirror: true
    sanitize_on_move: true

  phone:
    source: ~/Desktop/MusicTFR
    destination: /Volumes/Phone/Music
    mirror: true
```

Then: `boxraudio --profile ipod --run` or `boxraudio --profile ipod --profile phone --run`

---

## Architecture

```
boxraudio/
├── boxraudio_cli            # CLI entry point (dispatcher)
├── install.sh               # Installer
├── requirements.txt         # Python deps
├── tests/
│   └── test_smoke.py        # Smoke tests
├── docs/
│   └── WORKFLOWS.md         # Common workflows guide
├── boxraudio/               # Python package
│   ├── banner.py            # ASCII art
│   ├── commands.py          # CLI command handlers
│   ├── constants.py         # Centralized tunable constants
│   ├── errors.py            # Error message translation
│   ├── config.py            # YAML config + profiles
│   ├── ui.py                # Rich-based UI
│   ├── cache.py             # SQLite tag cache (WAL mode, schema versioned)
│   ├── transaction.py       # Action logging + undo (WAL mode)
│   ├── scanner.py           # Parallel tag scanning
│   ├── fingerprint.py       # AcoustID/Chromaprint
│   ├── spectrum.py          # Lossy detection via FFT
│   ├── audits.py            # Quality & tag audits
│   ├── sanitize.py          # Tag sanitization
│   ├── operations.py        # File operations primitives
│   ├── stats.py             # Library stats + history (WAL mode)
│   ├── preflight.py         # Disk space + pre-flight summary
│   ├── resume.py            # Interrupt-safe checkpointing
│   ├── interactive.py       # Guided interactive mode
│   └── pipeline.py          # Workflow orchestration
└── README.md
```

---

## License

MIT — see `LICENSE`

---

## Author

Built by DrafterBee.
