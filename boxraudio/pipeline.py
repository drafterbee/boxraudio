"""
pipeline.py — main workflow orchestrator for BoxR.
"""

import os
import sys
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
    preflight_summary, verify_disk_space_for_plan, format_bytes
)


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


def run_pipeline(args: dict) -> int:
    source           = args.get("source")
    backup           = args.get("backup")
    destination      = args.get("destination")
    sync_source      = args.get("sync_source")       # NEW
    sync_destination = args.get("sync_destination")  # NEW
    dedup_fmt        = args.get("dedupe_format")
    dedup_search     = args.get("dedupe_search") or []
    clean_empty      = args.get("clean_empty_dirs", False)
    mirror           = args.get("mirror", False)
    size_only        = args.get("size_only", True)
    rsync_extra      = args.get("rsync_flags")
    cache_file       = args.get("cache_file")
    workers          = args.get("workers", 4)
    dry_run          = args.get("dry_run", True)
    no_confirm       = args.get("no_confirm", False)
    rebuild          = args.get("rebuild_cache", False)
    profile_name     = args.get("profile")

    if not source:
        ui.error("--source is required.")
        return 1
    if not backup and not destination and not sync_destination:
        ui.error("at least one of --backup, --destination, or --sync-destination is required.")
        return 1

    # ── Determine effective sync paths ────────────────────────────────────────
    # sync_source defaults to backup if not explicitly set
    # sync_destination defaults to destination if not explicitly set
    effective_sync_source = sync_source or backup or source
    effective_sync_dest   = sync_destination or destination

    if dedup_fmt and not dedup_search:
        if backup:               dedup_search.append(backup)
        if destination:          dedup_search.append(destination)
        if sync_destination and sync_destination != destination:
            dedup_search.append(sync_destination)

    if not os.path.isdir(source):
        ui.error(f"Source not found: {source}")
        return 1
    if backup and not os.path.isdir(backup) and not dry_run:
        try:
            os.makedirs(backup, exist_ok=True)
        except OSError as e:
            ui.error(f"Could not create backup: {backup} ({e})")
            return 1
    # Validate sync destination (either explicit sync_destination or destination)
    final_dest = effective_sync_dest
    if final_dest and not os.path.isdir(final_dest) and not dry_run:
        try:
            os.makedirs(final_dest, exist_ok=True)
        except OSError as e:
            ui.error(f"Sync destination not accessible: {final_dest} ({e})")
            return 1

    cache_path = cache_file or os.path.expanduser("~/.boxraudio_cache.db")
    if rebuild and os.path.isfile(cache_path):
        os.remove(cache_path)
        ui.warning(f"Cache rebuilt: removed {cache_path}")
    cache = TagCache(cache_path)

    tx = TransactionLog()
    session_desc = f"Profile: {profile_name}" if profile_name else "Ad-hoc run"
    tx.start_session(description=session_desc, profile=profile_name)
    ui.info(f"Session ID: {tx.session_id}")
    ui.info(f"Cache: {cache_path} ({cache.count():,} entries)")

    # Update step list — sync runs if either destination is set
    has_sync_step = bool(effective_sync_dest)
    step_titles = ["Index source files"]
    if backup:
        step_titles.append(f"Move to backup")
    if dedup_fmt:
        for sp in dedup_search:
            step_titles.append(f"Dedupe .{dedup_fmt} in {os.path.basename(sp.rstrip('/'))}")
    if clean_empty:
        for sp in dedup_search:
            step_titles.append(f"Clean empty dirs in {os.path.basename(sp.rstrip('/'))}")
    if has_sync_step:
        step_titles.append("Sync to destination")
    total_steps = len(step_titles)
    current_step = 0

    ui.section("Configuration")
    config_table = {
        "Mode":              "DRY RUN" if dry_run else "LIVE RUN",
        "Profile":           profile_name or "(ad-hoc)",
        "Source":            source,
        "Backup":            backup,
        "Destination":       destination,
        "Sync source":       effective_sync_source if has_sync_step else None,
        "Sync destination":  effective_sync_dest    if has_sync_step else None,
        "Dedupe format":     dedup_fmt,
        "Dedupe searches":   ", ".join(dedup_search) if dedup_search else None,
        "Clean empty dirs":  "yes" if clean_empty else "no",
        "Mirror sync":       "yes" if mirror else "no",
        "Size-only sync":    "yes" if size_only else "no",
        "Parallel workers":  workers,
        "Cache file":        cache_path,
    }
    ui.kv_table("Pipeline configuration", config_table)

    current_step += 1
    ui.step_header(current_step, total_steps, "Indexing source files")

    source_audio_files = collect_audio_files(source)
    ui.info(f"Found {len(source_audio_files):,} audio files in source")

    if not source_audio_files:
        ui.warning("No audio files in source — nothing to do.")
        tx.end_session("nothing_to_do")
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
        return 1

    ui.section("Pre-flight summary")
    plan = {
        "source_files":       source_audio_files,
        "backup_target":      backup,
        "destination_target": destination,
        "sync_source":        effective_sync_source if has_sync_step else None,
        "sync_destination":   effective_sync_dest    if has_sync_step else None,
    }
    preflight_summary(plan)
    if not dry_run:
        if not verify_disk_space_for_plan(plan):
            ui.error("Insufficient disk space. Aborting.")
            tx.end_session("insufficient_space")
            return 1

    if not dry_run and not no_confirm:
        if not ui.confirm("Proceed with the operations above?", default=False):
            ui.warning("Aborted by user.")
            tx.end_session("user_aborted")
            return 0

    if backup:
        current_step += 1
        ui.step_header(current_step, total_steps, f"Moving source → backup")
        moved, errors = merge_move(source, backup, tx, dry_run=dry_run)
        if dry_run:
            ui.dim(f"Would move {moved} items")
        else:
            ui.success(f"Moved {moved} items ({errors} errors)")

    if dedup_fmt:
        dedup_ext = "." + dedup_fmt.lstrip(".")
        for sp in dedup_search:
            current_step += 1
            ui.step_header(current_step, total_steps,
                           f"Deduping {dedup_ext} in {sp}")
            if not os.path.isdir(sp):
                ui.warning(f"Skipping — directory not found: {sp}")
                continue

            target_files = collect_audio_files(sp, extensions={dedup_ext})
            ui.info(f"Found {len(target_files):,} {dedup_ext} files")

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
                else:
                    ui.warning("Cancelled — no files deleted")

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
                else:
                    ui.warning("Cancelled — no dirs removed")

    if has_sync_step:
        current_step += 1
        ui.step_header(current_step, total_steps, "Syncing to destination")

        ui.info(f"Source: {effective_sync_source}")
        ui.info(f"Destination: {effective_sync_dest}")
        if mirror:
            ui.warning("Mirror mode: extra files at destination WILL be deleted")

        extra_flags = rsync_extra.split() if rsync_extra else None
        rc = rsync_to_destination(
            effective_sync_source, effective_sync_dest,
            mirror=mirror, size_only=size_only,
            extra_flags=extra_flags, dry_run=dry_run,
        )

        if rc == 0:
            ui.success("rsync completed successfully")
        else:
            ui.error(f"rsync failed with exit code {rc}")
            tx.end_session("rsync_failed")
            return rc

    if not dry_run:
        valid_paths = set()
        if backup:
            valid_paths.update(collect_audio_files(backup))
        for sp in dedup_search:
            valid_paths.update(collect_audio_files(sp))
        if effective_sync_dest and os.path.isdir(effective_sync_dest):
            valid_paths.update(collect_audio_files(effective_sync_dest))

        if valid_paths:
            pruned = cache.prune(valid_paths)
            if pruned:
                ui.dim(f"Pruned {pruned:,} stale cache entries")

        tx.cleanup_old_quarantine()

    tx.end_session("complete")
    ui.print_completion_banner()
    return 0
