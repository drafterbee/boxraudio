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
- **AcoustID fingerprinting** — match audio content, not just tags (requires `chromaprint`)
- **Quality reporting** — see your library's bitrate and format composition
- **Tag quality checks** — flag files with missing or malformed metadata

### User Experience
- **Rich terminal UI** — beautiful tables, progress bars, and panels
- **Interactive mode** — guided prompts for occasional use (Phase 3)
- **Profiles** — save common workflows in `~/.boxraudio.yaml`
- **Pre-flight summary** — see exactly what will change before committing

### Performance
- **Parallel tag reading** — uses all CPU cores
- **SQLite cache** — fast lookups, atomic writes, query support
- **Incremental scanning** — skip unchanged directories

### Workflow
- **Multi-destination sync** — push to multiple devices in one command (Phase 3)
- **Stats and history** — track library growth and transfer activity (Phase 3)
- **Dry-run diff view** — see additions/removals/changes as a colored diff (Phase 3)

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/boxraudio.git
cd boxraudio
bash install.sh
```

The installer will:
1. Install Python dependencies (`mutagen`, `tqdm`, `rich`, `pyyaml`, `numpy`, `scipy`, `pyacoustid`, `psutil`)
2. Verify `chromaprint` is installed (or prompt to install via Homebrew)
3. Copy the package to `/usr/local/lib/boxraudio/`
4. Install the `boxraudio` command to `/usr/local/bin/`
5. Create default config at `~/.boxraudio.yaml`

---

## Quick Start

```bash
# See examples
boxraudio --examples

# Show help
boxraudio --help

# Interactive mode (Phase 3)
boxraudio --interactive

# Typical workflow with a saved profile
boxraudio --profile ipod --run

# Audit a library for lossy FLAC transcodes
boxraudio --audit-quality --target "/Volumes/Media Backup/Audio/FLAC"

# Audit tag quality
boxraudio --audit-tags --target "/Volumes/Media Backup/Audio"

# Library quality report
boxraudio --report-quality --target "/Volumes/Media Backup/Audio"

# Manual full pipeline
boxraudio \
  --source ~/Desktop/MusicTFR \
  --backup "/Volumes/Media Backup/Audio/FLAC" \
  --destination /Volumes/IPOD/Audio \
  --dedupe-format mp3 \
  --dedupe-search "/Volumes/Media Backup/Audio/MP3" \
  --clean-empty-dirs \
  --mirror --size-only \
  --run
```

---

## Configuration

Edit `~/.boxraudio.yaml` to define reusable profiles:

```yaml
defaults:
  cache_file: ~/.boxraudio_cache.db
  workers: 4
  size_only: true

profiles:
  ipod:
    source: ~/Desktop/MusicTFR
    backup: /Volumes/Media Backup/Audio/FLAC
    destination: /Volumes/IPOD/Audio
    dedupe_format: mp3
    dedupe_search:
      - /Volumes/Media Backup/Audio/MP3
    clean_empty_dirs: true
    mirror: true
```

Then: `boxraudio --profile ipod --run`

---

## Architecture

```
boxraudio/
├── boxraudio                # CLI entry point
├── install.sh               # Installer
├── requirements.txt         # Python deps
├── boxraudio/               # Python package
│   ├── banner.py            # ASCII art
│   ├── config.py            # YAML config + profiles
│   ├── ui.py                # Rich-based UI
│   ├── cache.py             # SQLite cache
│   ├── transaction.py       # Action logging + undo
│   ├── scanner.py           # Parallel tag scanning
│   ├── fingerprint.py       # AcoustID/Chromaprint
│   ├── spectrum.py          # Lossy detection via FFT
│   ├── audits.py            # Quality & tag audits
│   ├── operations.py        # File operations primitives
│   ├── stats.py             # Library stats + history (Phase 3)
│   ├── preflight.py         # Disk space + pre-flight summary
│   └── pipeline.py          # Workflow orchestration
└── README.md
```

---

## License

MIT — see `LICENSE`

---

## Author

Built by Ben — Archiframe Integration, Los Angeles.
