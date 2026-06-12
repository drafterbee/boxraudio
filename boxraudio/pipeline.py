"""
pipeline.py — main workflow orchestrator for BoxR.

Defines the full ingest pipeline:
  1. Index source files (parallel tag scan with cache)
  2. Move source → backup (with merge into existing artist/album folders)
  3. Sanitize tags on moved files (optional, opt-in via --sanitize-on-move)
  4. Dedupe lower-quality copies (tag-based or fingerprint-based)
  5. Clean up empty directories in dedup search paths
  6. Sync to one or more destinations (rsync, FAT32-optimized)

Each step is wrapped in transaction logging for --undo, checkpointing
for interrupt-safe resume, and metrics recording for --stats.

The diff between source and destination is shown both in the pre-flight
summary (so users see total scope before any operations) and again
per-step (so users see what each individual sync will do).
"""

import os
import sys
import time
from pathlib import Path

from boxraudio import ui
from boxraudio.cache import TagCache
from boxraudio.scanner import (
    AUDIO_EXTENSIONS, collect_audio_files, scan_files_parallel
)
from boxraudio.operations import (
    merge_move, delete_files, get_empty_dirs,
    remove_empty_directories, rsync_to_destination
)
from boxraudio.transaction import TransactionLog
from boxraudio.preflight import (
    preflight_summary, verify_disk_space_for_plan, format_bytes, get_total_size
)
from boxraudio.stats import StatsTracker
from boxraudio.resume import Checkpoint
from boxraudio import fingerprint


def find_dedup_matches(source_index: dict, target_index: dict):
    matches = []
    for key, target_path in target_index.items():
        if key in source_index:
            matches.append({
                "artist": key[0],
                "album":  key[1],
                "title":  key[2],
                "target": target_path,
                "source": source_index[key],
            })
    matches.sort(key=lambda x: (x["artist"], x["album"], x["title"]))
    return matches


def find_dedup_matches_fingerprint(source_files: list, target_files: list,
                                     threshold: float = 0.95) -> list:
    """
    Find duplicates by audio fingerprint matching (content-based).
    Slower than tag matching but catches duplicates with mismatched metadata.

    Uses fingerprint bucketing by leading hash prefix to avoid O(N*M)
    comparison — only fingerprints sharing a prefix are compared in full.
    """
    if not fingerprint.is_available():
        ui.error("Fingerprint dedup requires chromaprint. Install: brew install chromaprint")
        return []

    matches = []

    ui.info(f"Generating fingerprints for {len(source_files):,} source files...")
    source_fps = {}
    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Source fingerprints", total=len(source_files))
        for fp_path in source_files:
            result = fingerprint.fingerprint_file(fp_path, length_secs=60)
            if "fingerprint" in result and result["fingerprint"]:
                source_fps[fp_path] = result["fingerprint"]
            progress.advance(task)

    ui.info(f"Generating fingerprints for {len(target_files):,} target files...")
    target_fps = {}
    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Target fingerprints", total=len(target_files))
        for fp_path in target_files:
            result = fingerprint.fingerprint_file(fp_path, length_secs=60)
            if "fingerprint" in result and result["fingerprint"]:
                target_fps[fp_path] = result["fingerprint"]
            progress.advance(task)

    # Bucket source fingerprints by their first 8 characters. This gives
    # ~64^8 buckets — for any reasonable library, each bucket holds only
    # a handful of fingerprints, turning the full N*M scan into roughly
    # N*K where K is the average bucket size.
    BUCKET_PREFIX = 8
    source_buckets = {}
    for path, fp_str in source_fps.items():
        bucket = fp_str[:BUCKET_PREFIX] if fp_str else ""
        source_buckets.setdefault(bucket, []).append((path, fp_str))

    ui.info("Matching fingerprints...")
    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Comparing", total=len(target_fps))
        for target_path, target_fp in target_fps.items():
            bucket = target_fp[:BUCKET_PREFIX] if target_fp else ""
            # Only check fingerprints in the same prefix bucket plus neighbors
            # (a single bit-flip in early bytes could put a near-match in a
            # different bucket; we accept the rare miss for the speedup)
            candidates = source_buckets.get(bucket, [])
            for source_path, source_fp in candidates:
                matches_ok, score = fingerprint.fingerprints_match(
                    source_fp, target_fp, threshold=threshold
                )
                if matches_ok:
                    matches.append({
                        "target":     target_path,
                        "source":     source_path,
                        "similarity": score,
                    })
                    break
            progress.advance(task)

    return matches


def print_diff_summary(diff: dict):
    """Print a colored diff-style summary of planned actions."""
    rows = []
    if diff.get("files_to_add"):
        rows.append(f"[green]+ {len(diff['files_to_add']):,} files to add[/green]")
    if diff.get("files_to_delete"):
        rows.append(f"[red]- {len(diff['files_to_delete']):,} files to delete[/red]")
    if diff.get("dirs_to_remove"):
        rows.append(f"[red]- {len(diff['dirs_to_remove']):,} empty directories to remove[/red]")
    if diff.get("bytes_to_sync"):
        rows.append(f"[cyan]~ {format_bytes(diff['bytes_to_sync'])} to sync[/cyan]")
    if not rows:
        rows.append("[dim]No changes — destination is already in sync[/dim]")

    ui.console.print()
    for r in rows:
        ui.console.print(f"  {r}")
    ui.console.print()


def run_single_destination(args: dict, destination_path: str,
                            sync_source_override: str = None,
                            tx: TransactionLog = None,
                            stats: StatsTracker = None,
                            cache: TagCache = None,
                            source_scan: dict = None) -> int:
    """
    Run the sync portion of the pipeline for a single destination.
    Used by the main run_pipeline for multi-destination support.
    """
    mirror      = args.get("mirror", False)
    size_only   = args.get("size_only", True)
    rsync_extra = args.get("rsync_flags")
    dry_run     = args.get("dry_run", True)

    sync_source = sync_source_override or args.get("sync_source") or args.get("backup") or args.get("source")

    ui.info(f"Source:      {sync_source}")
    ui.info(f"Destination: {destination_path}")
    if mirror:
        ui.warning("Mirror mode: extra files at destination WILL be deleted")

    # Diff view — compute additions/removals before sync
    if not os.path.isdir(destination_path):
        if not dry_run:
            try:
                os.makedirs(destination_path, exist_ok=True)
            except OSError as e:
                ui.error(f"Could not create destination: {destination_path} ({e})")
                return 1

    source_files = set()
    dest_files   = set()
    if os.path.isdir(sync_source):
        for fp in collect_audio_files(sync_source):
            rel = os.path.relpath(fp, sync_source)
            source_files.add(rel)
    if os.path.isdir(destination_path):
        for fp in collect_audio_files(destination_path):
            rel = os.path.relpath(fp, destination_path)
            dest_files.add(rel)

    to_add    = source_files - dest_files
    to_remove = dest_files - source_files
    in_both   = source_files & dest_files

    diff = {
        "files_to_add":    [os.path.join(sync_source, f) for f in to_add],
        "files_to_delete": [os.path.join(destination_path, f) for f in to_remove] if mirror else [],
        "bytes_to_sync":   get_total_size([os.path.join(sync_source, f) for f in to_add]),
    }

    ui.section("Sync diff")
    print_diff_summary(diff)

    extra_flags = rsync_extra.split() if rsync_extra else None
    rc = rsync_to_destination(
        sync_source, destination_path,
        mirror=mirror, size_only=size_only,
        extra_flags=extra_flags, dry_run=dry_run,
    )

    if rc == 0:
        ui.success(f"rsync completed for {destination_path}")
        if stats and not dry_run:
            stats.record_metrics(
                files_synced=len(to_add),
                bytes_synced=diff["bytes_to_sync"],
            )
    else:
        ui.error(f"rsync failed for {destination_path} (exit code {rc})")
        if stats:
            stats.record_metrics(errors=1)

    return rc


def run_pipeline(args: dict) -> int:
    source             = args.get("source")
    backup             = args.get("backup")
    destinations       = args.get("destinations") or []
    sync_source        = args.get("sync_source")
    sync_destinations  = args.get("sync_destinations") or []
    dedup_fmt          = args.get("dedupe_format")
    dedup_search       = args.get("dedupe_search") or []
    dedup_method       = args.get("dedupe_method", "tag")  # 'tag' or 'fingerprint'
    clean_empty        = args.get("clean_empty_dirs", False)
    rebuild            = args.get("rebuild_cache", False)
    cache_file         = args.get("cache_file")
    workers            = args.get("workers", 4)
    dry_run            = args.get("dry_run", True)
    no_confirm         = args.get("no_confirm", False)
    profile_name       = args.get("profile")
    resume_checkpoint  = args.get("resume_checkpoint")  # passed when resuming

    # Sanitization options
    sanitize_on_move   = args.get("sanitize_on_move", False)
    strip_art          = args.get("strip_art", False)
    strip_lyrics       = args.get("strip_lyrics", False)
    keep_tags_extra    = args.get("keep_tags") or []

    # Back-compat: also accept singular destination
    if not destinations and args.get("destination"):
        destinations = [args["destination"]]
    if not sync_destinations and args.get("sync_destination"):
        sync_destinations = [args["sync_destination"]]

    if not source:
        ui.error("--source is required.")
        return 1
    if not backup and not destinations and not sync_destinations:
        ui.error("at least one of --backup, --destination, or --sync-destination is required.")
        return 1

    # Effective sync targets: pair each sync_destination with a sync_source,
    # or fall back to backup → source
    effective_sync_source = sync_source or backup or source

    # Build the list of (sync_source, dest) pairs for sync steps
    sync_pairs = []
    if sync_destinations:
        # If sync_destinations supplied, pair each with the effective sync source
        for sd in sync_destinations:
            sync_pairs.append((effective_sync_source, sd))
    else:
        # Otherwise pair the effective_sync_source with each destination
        for d in destinations:
            sync_pairs.append((effective_sync_source, d))

    if dedup_fmt and not dedup_search:
        if backup:               dedup_search.append(backup)
        for d in destinations:   dedup_search.append(d)
        for sd in sync_destinations:
            if sd not in dedup_search:
                dedup_search.append(sd)

    if not os.path.isdir(source):
        ui.error(f"Source not found: {source}")
        return 1
    if backup and not os.path.isdir(backup) and not dry_run:
        try:
            os.makedirs(backup, exist_ok=True)
        except OSError as e:
            ui.error(f"Could not create backup: {backup} ({e})")
            return 1

    cache_path = cache_file or os.path.expanduser("~/.boxraudio_cache.db")
    if rebuild and os.path.isfile(cache_path):
        # Atomic move-then-delete to avoid race with concurrent reads
        backup_path = cache_path + ".rebuild-backup"
        try:
            os.rename(cache_path, backup_path)
            os.remove(backup_path)
            ui.warning(f"Cache rebuilt: removed {cache_path}")
        except OSError as e:
            ui.error(f"Could not rebuild cache: {e}")
            return 1
        # Also clear any WAL/journal files
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                os.remove(cache_path + suffix)
            except OSError:
                pass
    cache = TagCache(cache_path)

    tx = TransactionLog()
    session_desc = f"Profile: {profile_name}" if profile_name else "Ad-hoc run"
    tx.start_session(description=session_desc, profile=profile_name)

    stats = StatsTracker()
    stats.start_run(
        profile=profile_name,
        source=source,
        backup=backup,
        destination=", ".join(d for _, d in sync_pairs) if sync_pairs else None,
    )

    # Checkpoint for interrupt-safe resume
    checkpoint = Checkpoint(session_id=tx.session_id, profile=profile_name)
    checkpoint.set_args(args)

    ui.info(f"Session ID: {tx.session_id}")
    ui.info(f"Cache: {cache_path} ({cache.count():,} entries)")

    # Build step list
    step_titles = ["Index source files"]
    if backup:
        step_titles.append("Move to backup")
    if sanitize_on_move and backup:
        step_titles.append("Sanitize tags")
    if dedup_fmt:
        for sp in dedup_search:
            step_titles.append(f"Dedupe .{dedup_fmt} in {os.path.basename(sp.rstrip('/'))}")
    if clean_empty:
        for sp in dedup_search:
            step_titles.append(f"Clean empty dirs in {os.path.basename(sp.rstrip('/'))}")
    for _, dest in sync_pairs:
        step_titles.append(f"Sync to {os.path.basename(dest.rstrip('/'))}")
    total_steps = len(step_titles)
    current_step = 0

    ui.section("Configuration")
    config_table = {
        "Mode":              "DRY RUN" if dry_run else "LIVE RUN",
        "Profile":           profile_name or "(ad-hoc)",
        "Source":            source,
        "Backup":            backup,
        "Destinations":      "\n".join(d for _, d in sync_pairs) if sync_pairs else None,
        "Dedupe format":     dedup_fmt,
        "Dedupe searches":   "\n".join(dedup_search) if dedup_search else None,
        "Clean empty dirs":  "yes" if clean_empty else "no",
        "Mirror sync":       "yes" if args.get("mirror") else "no",
        "Size-only sync":    "yes" if args.get("size_only", True) else "no",
        "Parallel workers":  workers,
        "Cache file":        cache_path,
    }
    ui.kv_table("Pipeline configuration", config_table)

    # ── STEP — Index source files ─────────────────────────────────────────────
    current_step += 1
    ui.step_header(current_step, total_steps, "Indexing source files")

    source_audio_files = collect_audio_files(source)
    ui.info(f"Found {len(source_audio_files):,} audio files in source")

    if not source_audio_files:
        ui.warning("No audio files in source — nothing to do.")
        tx.end_session("nothing_to_do")
        stats.end_run("nothing_to_do")
        return 0

    source_scan = scan_files_parallel(
        source_audio_files, cache, workers=workers,
        description="Reading source tags",
    )
    ui.success(f"Indexed {len(source_scan['index']):,} unique tracks")
    ui.dim(f"Cache hits: {source_scan['hits']:,}  •  misses: {source_scan['misses']:,}")
    if source_scan["skipped"]:
        ui.warning(f"{len(source_scan['skipped']):,} files skipped (missing tags)")

    if not source_scan["index"]:
        ui.error("No tagged files in source — cannot proceed.")
        tx.end_session("no_source_tags")
        stats.end_run("no_source_tags")
        return 1

    # ── Pre-flight summary with diff ─────────────────────────────────────────
    ui.section("Pre-flight summary")
    plan = {
        "source_files":     source_audio_files,
        "backup_target":    backup,
        "destination_target": destinations[0] if destinations else None,
        "sync_source":      effective_sync_source if sync_pairs else None,
        "sync_destination": sync_pairs[0][1] if sync_pairs else None,
    }
    preflight_summary(plan)

    # Show per-destination diff so the user knows exactly what will sync
    if sync_pairs:
        ui.console.print()
        ui.console.print("  [bold]Sync diff per destination:[/bold]")
        for sync_src, sync_dest in sync_pairs:
            if not os.path.isdir(sync_src):
                ui.dim(f"  {sync_dest}: sync source not yet accessible")
                continue
            src_files = set()
            dst_files = set()
            for fp in collect_audio_files(sync_src):
                src_files.add(os.path.relpath(fp, sync_src))
            if os.path.isdir(sync_dest):
                for fp in collect_audio_files(sync_dest):
                    dst_files.add(os.path.relpath(fp, sync_dest))
            to_add    = src_files - dst_files
            to_remove = dst_files - src_files
            bytes_add = get_total_size([os.path.join(sync_src, f) for f in to_add])
            ui.console.print(f"  [dim]→[/dim] {sync_dest}")
            ui.console.print(
                f"      [green]+ {len(to_add):,} files ({format_bytes(bytes_add)})[/green]"
            )
            if args.get("mirror") and to_remove:
                ui.console.print(
                    f"      [red]- {len(to_remove):,} files would be deleted (mirror mode)[/red]"
                )
            elif not to_add and not (args.get("mirror") and to_remove):
                ui.console.print(f"      [dim]no changes — already in sync[/dim]")
        ui.console.print()

    if not dry_run:
        if not verify_disk_space_for_plan(plan):
            ui.error("Insufficient disk space. Aborting.")
            tx.end_session("insufficient_space")
            stats.end_run("insufficient_space")
            return 1

    if not dry_run and not no_confirm:
        if not ui.confirm("Proceed with the operations above?", default=False):
            ui.warning("Aborted by user.")
            tx.end_session("user_aborted")
            stats.end_run("user_aborted")
            return 0

    # ── STEP — Move source → backup ──────────────────────────────────────────
    if backup:
        current_step += 1
        ui.step_header(current_step, total_steps, "Moving source → backup")
        moved, errors = merge_move(source, backup, tx, dry_run=dry_run)
        if dry_run:
            ui.dim(f"Would move {moved} items")
        else:
            ui.success(f"Moved {moved} items ({errors} errors)")
            stats.record_metrics(files_moved=moved, errors=errors)

    # ── STEP — Sanitize tags on moved files (optional) ───────────────────────
    if sanitize_on_move and backup:
        current_step += 1
        ui.step_header(current_step, total_steps, "Sanitizing tags on moved files")

        from boxraudio.sanitize import (
            sanitize_directory, DEFAULT_KEEP_TAGS, ALWAYS_STRIP_PATTERNS
        )
        keep_tags_set = set(DEFAULT_KEEP_TAGS)
        for t in keep_tags_extra:
            keep_tags_set.add(t.lower())

        ui.info(f"Strip album art:  {'yes' if strip_art else 'no (keep)'}")
        ui.info(f"Strip lyrics:     {'yes' if strip_lyrics else 'no (keep)'}")
        ui.info(f"Keep tags: {len(keep_tags_set)} entries")

        if dry_run:
            ui.dim(f"DRY RUN — would sanitize files in {backup}")
        else:
            sanitize_result = sanitize_directory(
                backup,
                keep_tags=keep_tags_set,
                strip_art=strip_art,
                strip_lyrics=strip_lyrics,
                strip_patterns=ALWAYS_STRIP_PATTERNS,
                dry_run=False,
            )
            ui.success(f"Sanitized {sanitize_result['modified_files']:,} files")
            if sanitize_result["errors"]:
                ui.warning(f"{len(sanitize_result['errors'])} sanitization errors")
                stats.record_metrics(errors=len(sanitize_result["errors"]))

    # ── STEP — Dedupe in each search location ─────────────────────────────────
    if dedup_fmt:
        dedup_ext = "." + dedup_fmt.lstrip(".")
        for sp in dedup_search:
            current_step += 1
            method_label = "fingerprint" if dedup_method == "fingerprint" else "tag"
            ui.step_header(current_step, total_steps,
                           f"Deduping {dedup_ext} in {sp} ({method_label}-based)")
            checkpoint.begin_step(f"dedupe_{sp}")
            if not os.path.isdir(sp):
                ui.warning(f"Skipping — directory not found: {sp}")
                continue

            target_files = collect_audio_files(sp, extensions={dedup_ext})
            ui.info(f"Found {len(target_files):,} {dedup_ext} files")

            if dedup_method == "fingerprint":
                # Content-based matching via Chromaprint
                if not fingerprint.is_available():
                    ui.error("Fingerprint dedup needs chromaprint. "
                             "Install: brew install chromaprint")
                    ui.warning("Falling back to tag-based dedup.")
                    dedup_method = "tag"

            if dedup_method == "fingerprint":
                fp_matches = find_dedup_matches_fingerprint(
                    list(source_scan["index"].values()),
                    target_files,
                )
                if not fp_matches:
                    ui.success(f"No matching duplicates in {sp}")
                    continue
                ui.warning(f"{len(fp_matches):,} fingerprint matches in {sp}")
                ui.file_table(
                    f"Sample of fingerprint matches ({sp})",
                    [f"{m['target']}  (~{m['similarity']:.0%} match)" for m in fp_matches],
                    limit=15,
                )

                if dry_run:
                    ui.dim(f"DRY RUN — would delete {len(fp_matches)} files")
                else:
                    proceed = no_confirm or ui.confirm_yes_required(
                        f"Delete {len(fp_matches):,} fingerprint-matched files from {sp}?"
                    )
                    if proceed:
                        deleted, errs = delete_files(
                            [m["target"] for m in fp_matches], tx, dry_run=False,
                            description=f"Deleting from {os.path.basename(sp.rstrip('/'))}"
                        )
                        ui.success(f"Deleted {deleted:,} ({errs} errors)")
                        stats.record_metrics(files_deleted=deleted, errors=errs)
                    else:
                        ui.warning("Cancelled — no files deleted")
            else:
                # Tag-based matching (default)
                target_scan = scan_files_parallel(
                    target_files, cache, workers=workers,
                    description=f"Reading {dedup_ext} tags",
                )
                ui.success(f"Indexed {len(target_scan['index']):,} unique tracks")
                ui.dim(f"Cache hits: {target_scan['hits']:,}  •  misses: {target_scan['misses']:,}")

                matches = find_dedup_matches(source_scan["index"], target_scan["index"])
                if not matches:
                    ui.success(f"No matching duplicates in {sp}")
                    continue

                ui.warning(f"{len(matches):,} duplicates to delete in {sp}")
                ui.file_table(f"Sample of matches ({sp})",
                              [f"{m['artist']} / {m['album']} / {m['title']}" for m in matches],
                              limit=15)

                if dry_run:
                    ui.dim(f"DRY RUN — would delete {len(matches)} files")
                else:
                    proceed = no_confirm or ui.confirm_yes_required(
                        f"Delete {len(matches):,} duplicate {dedup_ext} files from {sp}?"
                    )
                    if proceed:
                        deleted, errs = delete_files(
                            [m["target"] for m in matches], tx, dry_run=False,
                            description=f"Deleting from {os.path.basename(sp.rstrip('/'))}"
                        )
                        ui.success(f"Deleted {deleted:,} ({errs} errors)")
                        stats.record_metrics(
                            files_deleted=deleted,
                            errors=errs,
                        )
                    else:
                        ui.warning("Cancelled — no files deleted")

            checkpoint.complete_step(f"dedupe_{sp}")

    # ── STEP — Clean empty directories ───────────────────────────────────────
    if clean_empty:
        for sp in dedup_search:
            current_step += 1
            ui.step_header(current_step, total_steps,
                           f"Cleaning empty dirs in {sp}")
            if not os.path.isdir(sp):
                ui.warning(f"Skipping — directory not found: {sp}")
                continue

            empty_dirs = get_empty_dirs(sp)
            if not empty_dirs:
                ui.success(f"No empty directories in {sp}")
                continue

            ui.warning(f"{len(empty_dirs):,} empty directories to remove")
            ui.file_table(f"Sample of dirs ({sp})", empty_dirs, limit=15)

            if dry_run:
                ui.dim(f"DRY RUN — would remove {len(empty_dirs)} dirs")
            else:
                proceed = no_confirm or ui.confirm_yes_required(
                    f"Remove {len(empty_dirs):,} empty directories from {sp}?"
                )
                if proceed:
                    removed, errs = remove_empty_directories(
                        empty_dirs, tx, dry_run=False,
                    )
                    ui.success(f"Removed {removed:,} ({errs} errors)")
                    stats.record_metrics(dirs_removed=removed, errors=errs)
                else:
                    ui.warning("Cancelled — no dirs removed")

    # ── STEP — Sync to each destination sequentially ─────────────────────────
    any_sync_failed = False
    for sync_src, sync_dest in sync_pairs:
        current_step += 1
        ui.step_header(current_step, total_steps,
                       f"Syncing to {sync_dest}")

        if not dry_run and not os.path.isdir(sync_dest):
            try:
                os.makedirs(sync_dest, exist_ok=True)
            except OSError as e:
                ui.error(f"Sync destination not accessible: {sync_dest} ({e})")
                any_sync_failed = True
                continue

        rc = run_single_destination(
            args, sync_dest, sync_source_override=sync_src,
            tx=tx, stats=stats, cache=cache, source_scan=source_scan,
        )

        if rc != 0:
            any_sync_failed = True
            ui.warning(f"Sync to {sync_dest} failed; continuing with remaining destinations.")

    # ── Library snapshot for backup (for stats history) ──────────────────────
    if not dry_run and backup and os.path.isdir(backup):
        try:
            backup_files = collect_audio_files(backup)
            if backup_files:
                backup_scan = scan_files_parallel(
                    backup_files, cache, workers=workers,
                    description="Snapshotting library state",
                )
                stats.snapshot_library(backup, backup_scan)
        except Exception:
            pass

    # ── Cache prune (only on clean completion) ───────────────────────────────
    if not dry_run and not any_sync_failed:
        valid_paths = set()
        if backup:
            valid_paths.update(collect_audio_files(backup))
        for sp in dedup_search:
            valid_paths.update(collect_audio_files(sp))
        for _, sd in sync_pairs:
            if os.path.isdir(sd):
                valid_paths.update(collect_audio_files(sd))

        if valid_paths:
            pruned = cache.prune(valid_paths)
            if pruned:
                ui.dim(f"Pruned {pruned:,} stale cache entries")

        tx.cleanup_old_quarantine()
    elif not dry_run and any_sync_failed:
        ui.warning("Skipping cache prune because one or more sync steps failed.")

    final_status = "complete" if not any_sync_failed else "partial_failure"
    tx.end_session(final_status)
    stats.end_run(final_status)
    if not any_sync_failed:
        checkpoint.mark_complete()
    ui.print_completion_banner()
    return 0 if not any_sync_failed else 2
