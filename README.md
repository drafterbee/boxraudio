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

BoxR is a versatile media library tool that handles the full lifecycle of importing new music: moving files to your master backup, deduplicating lower-quality copies, and synchronizing changes to portable devices — with smart caching, parallel processing, transaction logging, and audio integrity verification.

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
- **Tag quality checks** — flag files with missing or malformed metadata

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

---

## Quick Start

```bash
boxraudio --examples            # see usage examples
boxraudio --help                # full options reference
boxraudio --interactive         # guided setup
boxraudio --profile ipod --run  # run with a saved profile

# Audits
boxraudio --audit-quality --target "/path/to/library"
boxraudio --audit-tags    --target "/path/to/library"
boxraudio --report-quality --target "/path/to/library"

# Stats and history
boxraudio --stats
boxraudio --history --detailed

# Undo / resume
boxraudio --undo
boxraudio --resume
boxraudio --list-resumable

# Multi-destination sequential sync
boxraudio --profile ipod --profile phone --run

# Content-based dedup using fingerprints
boxraudio --profile ipod --dedupe-method fingerprint --run
```

---

## Configuration

Edit `~/.boxraudio.yaml`:

```yaml
defaults:
  cache_file: ~/.boxraudio_cache.db
  workers: 4
  size_only: true

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
├── boxraudio_cli            # CLI entry point
├── install.sh               # Installer
├── requirements.txt         # Python deps
├── boxraudio/               # Python package
│   ├── banner.py            # ASCII art
│   ├── config.py            # YAML config + profiles
│   ├── ui.py                # Rich-based UI
│   ├── cache.py             # SQLite tag cache
│   ├── transaction.py       # Action logging + undo
│   ├── scanner.py           # Parallel tag scanning
│   ├── fingerprint.py       # AcoustID/Chromaprint
│   ├── spectrum.py          # Lossy detection via FFT
│   ├── audits.py            # Quality & tag audits
│   ├── operations.py        # File operations primitives
│   ├── stats.py             # Library stats + history
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
