# BoxR Common Workflows

This document covers the most common BoxR workflows beyond what's in the README.

## First-time setup

1. Install: `bash install.sh`
2. Set up a profile interactively: `boxraudio --interactive`
3. Or edit `~/.boxraudio.yaml` directly (see Configuration in the README)
4. Verify with: `boxraudio --explain --profile YOUR_PROFILE`

## Daily / regular sync

Once a profile is configured, the daily workflow is just:

```bash
boxraudio --profile ipod          # dry run preview
boxraudio --profile ipod --run    # execute
```

## Multi-device updates

To sync the same library to multiple devices in one command:

```bash
boxraudio --profile ipod --profile phone --run
```

Each profile runs sequentially. If one fails, the others still attempt to run.

## Library auditing

Before a long sync, run quality and tag audits to catch problems:

```bash
boxraudio --report-quality --target "/Volumes/Media Backup/Audio"
boxraudio --audit-tags --target "/Volumes/Media Backup/Audio/FLAC"
boxraudio --audit-quality --target "/Volumes/Media Backup/Audio/FLAC"
```

The quality report is fast (tag scan only). The audit-tags pass is also
fast. The audit-quality (spectrum analysis) is the slow one — expect
roughly 1-2 seconds per FLAC.

## Handling interrupted runs

If you Ctrl+C during a long run, the checkpoint is saved automatically:

```bash
boxraudio --list-resumable   # see what's resumable
boxraudio --resume           # pick up where you left off
```

The cache and step tracking will skip work that's already done.

## Undoing a recent run

Every completed pipeline run is reversible:

```bash
boxraudio --history         # find the session ID you want to undo
boxraudio --undo            # reverses the most recent completed session
```

**Exception:** sanitize operations are NOT undoable. Tag strips are
permanent. Always do a dry run first.

## Investigating problem files

If `--audit-tags` flags files with bad metadata:

1. Try sanitization first: `boxraudio --sanitize-tags --target FILE --run`
2. If sanitization can't read the file, the tag block may be corrupt
3. Worst case: re-import from source or re-rip

## When the cache misbehaves

Cache symptoms: stale duplicate matches, wrong file counts, "database is locked" errors.

```bash
boxraudio --cache-info           # confirm cache state
boxraudio --rebuild-cache --profile X --run    # nuke and rebuild
```

## Troubleshooting

### "Database not ready" / Rockbox database issues

Usually caused by malformed tags in one or more files. Run `--audit-tags`
to find them, then `--sanitize-tags` to clean them. Re-rebuild the
Rockbox database after.

### Sync deletes files unexpectedly

If you're using mirror mode (`--mirror`), rsync deletes destination files
that don't exist at the source. Check that your `sync_source` and
`sync_destination` align properly. The pre-flight diff view shows what
will be deleted before any change happens.

### High memory usage during scan

If you have a very large library (50k+ files), consider lowering
`workers` in your config or via `--workers 2`.

### Permission errors during sanitize

macOS may require granting Terminal full disk access for files on
external volumes. Check System Settings > Privacy > Files and Folders.

## Debug mode

Set `BOXRAUDIO_DEBUG=1` to get full tracebacks on unexpected errors:

```bash
BOXRAUDIO_DEBUG=1 boxraudio --profile ipod --run
```
