"""
commands.py — handlers for each CLI subcommand.

Each handler function takes (args, config) and returns an int exit code.
The CLI's main() dispatches to one of these based on which flag the user set.

This keeps boxraudio_cli small and focused on argument parsing + dispatch,
making each command testable in isolation.
"""

import os
import sys
import time

from boxraudio import ui
from boxraudio.config import get_profile, list_profiles, get_defaults, ConfigError


# ─── Information commands ─────────────────────────────────────────────────────

def cmd_version(args, config):
    from boxraudio import __version__, __appname__
    print(f"{__appname__} (boxraudio) {__version__}")
    return 0


def cmd_examples(args, config):
    examples = """
[bold bright_cyan]EXAMPLE USAGE:[/bold bright_cyan]

[yellow]1. Full workflow — move FLACs to backup, dedupe MP3s, mirror to iPod:[/yellow]
   boxraudio \\
     --source ~/Desktop/MusicTFR \\
     --backup "/Volumes/Media Backup/Audio/FLAC" \\
     --destination /Volumes/IPOD/Audio/FLAC \\
     --dedupe-format mp3 \\
     --dedupe-search "/Volumes/Media Backup/Audio/MP3" \\
     --clean-empty-dirs --mirror --size-only --run

[yellow]2. Use a saved profile:[/yellow]
   boxraudio --profile ipod --run

[yellow]3. Multi-destination sync (sequential):[/yellow]
   boxraudio --profile ipod --profile phone --run

[yellow]4. Interactive mode (guided setup):[/yellow]
   boxraudio --interactive

[yellow]5. Library audits:[/yellow]
   boxraudio --audit-quality --target "/Volumes/Media Backup/Audio/FLAC"
   boxraudio --audit-tags --target "/Volumes/Media Backup/Audio"
   boxraudio --report-quality --target "/Volumes/Media Backup/Audio"

[yellow]6. Content-based dedup (fingerprint matching):[/yellow]
   boxraudio --profile ipod --dedupe-method fingerprint --run

[yellow]7. Stats and history:[/yellow]
   boxraudio --stats
   boxraudio --history --detailed

[yellow]8. Undo / resume:[/yellow]
   boxraudio --undo
   boxraudio --resume
   boxraudio --list-resumable

[yellow]9. Sanitize tags (strip superfluous metadata):[/yellow]
   boxraudio --sanitize-tags --target "/Volumes/Media Backup/Audio" --run
   boxraudio --sanitize-tags --target ~/MusicLibrary --strip-art --run
   boxraudio --sanitize-tags --target ~/MusicLibrary --keep-tag custom_tag --run

[yellow]10. Auto-sanitize during sync:[/yellow]
   boxraudio --profile ipod --sanitize-on-move --run

[yellow]11. Cache inspection:[/yellow]
   boxraudio --cache-info
"""
    ui.console.print(examples)
    return 0


def cmd_list_profiles(args, config):
    profiles = list_profiles(config)
    if not profiles:
        ui.warning("No profiles defined in config.")
    else:
        ui.kv_table(
            "Available profiles",
            {p: "Use with --profile " + p for p in profiles},
        )
    return 0


def cmd_cache_info(args, config):
    """Show information about the current cache."""
    from boxraudio.cache import TagCache
    from boxraudio.preflight import format_bytes

    defaults = get_defaults(config)
    cache_path = args.cache_file or defaults.get("cache_file") or os.path.expanduser("~/.boxraudio_cache.db")
    cache_path = os.path.expanduser(cache_path)

    ui.section("Cache information")

    if not os.path.isfile(cache_path):
        ui.warning(f"No cache file found at {cache_path}")
        return 0

    cache = TagCache(cache_path)
    try:
        size = os.path.getsize(cache_path)
        count = cache.count()
        schema_ver = cache.schema_version()
    except OSError as e:
        ui.error(f"Could not read cache: {e}")
        return 1

    info = {
        "Cache file":     cache_path,
        "File size":      format_bytes(size),
        "Entries":        f"{count:,}",
        "Schema version": str(schema_ver) if schema_ver else "unknown",
    }
    ui.kv_table("Cache overview", info)
    return 0


def cmd_explain(args, config):
    """Show what the selected profile(s) would do without running."""
    profiles_to_run = args.profile or []
    if not profiles_to_run:
        ui.error("--explain requires --profile NAME")
        return 1

    for profile_name in profiles_to_run:
        try:
            profile = get_profile(config, profile_name)
        except ConfigError as e:
            ui.error(str(e))
            return 1

        ui.section(f"Profile: {profile_name}")

        # Sync destinations summary
        dests = profile.get("destinations") or (
            [profile["destination"]] if profile.get("destination") else []
        )
        sync_dests = profile.get("sync_destinations") or (
            [profile["sync_destination"]] if profile.get("sync_destination") else []
        )
        effective_dests = sync_dests or dests

        # What this profile does, in plain language
        ui.console.print("  [bold]What it does:[/bold]")
        if profile.get("source") and profile.get("backup"):
            ui.console.print(
                f"    [cyan]1.[/cyan] Move files from [yellow]{profile['source']}[/yellow] "
                f"to [yellow]{profile['backup']}[/yellow]"
            )
        if profile.get("sanitize_on_move"):
            strip_bits = []
            if profile.get("strip_art"):    strip_bits.append("album art")
            if profile.get("strip_lyrics"): strip_bits.append("lyrics")
            strip_note = f" (also strips {', '.join(strip_bits)})" if strip_bits else ""
            ui.console.print(
                f"    [cyan]2.[/cyan] Sanitize tags on moved files{strip_note}"
            )
        if profile.get("dedupe_format"):
            method = profile.get("dedupe_method", "tag")
            searches = profile.get("dedupe_search") or []
            ui.console.print(
                f"    [cyan]3.[/cyan] Find duplicate .{profile['dedupe_format']} files "
                f"({method} matching) in:"
            )
            for s in searches:
                ui.console.print(f"        [dim]· {s}[/dim]")
        if profile.get("clean_empty_dirs"):
            ui.console.print(f"    [cyan]4.[/cyan] Remove empty directories from dedup search paths")
        if effective_dests:
            for d in effective_dests:
                mirror = " (mirror mode — deletes extra files)" if profile.get("mirror") else ""
                ui.console.print(
                    f"    [cyan]5.[/cyan] Sync to [yellow]{d}[/yellow]{mirror}"
                )

        # Full config table
        ui.console.print()
        config_view = {
            "Source":            profile.get("source") or "(none)",
            "Backup":            profile.get("backup") or "(none)",
            "Destinations":      "\n".join(dests) if dests else "(none)",
            "Sync source":       profile.get("sync_source") or "(default: backup)",
            "Sync destinations": "\n".join(sync_dests) if sync_dests else "(default: destinations)",
            "Dedupe format":     profile.get("dedupe_format") or "(none)",
            "Dedupe method":     profile.get("dedupe_method") or "tag (default)",
            "Dedupe searches":   "\n".join(profile.get("dedupe_search") or []) or "(none)",
            "Clean empty dirs":  "yes" if profile.get("clean_empty_dirs") else "no",
            "Mirror mode":       "yes" if profile.get("mirror") else "no",
            "Size-only sync":    "yes" if profile.get("size_only") else "no",
            "Sanitize on move":  "yes" if profile.get("sanitize_on_move") else "no",
            "Strip art":         "yes" if profile.get("strip_art") else "no (keep)",
            "Strip lyrics":      "yes" if profile.get("strip_lyrics") else "no (keep)",
        }
        ui.kv_table("Full configuration", config_view)

    return 0


# ─── History / stats / undo / resume ──────────────────────────────────────────

def cmd_undo(args, config):
    from boxraudio.transaction import TransactionLog
    tx = TransactionLog()
    last = tx.last_session()
    if not last:
        ui.warning("No previous completed session to undo.")
        return 0
    ui.section(f"Undo session {last['id']}: {last.get('description', '')}")
    if not args.no_confirm:
        if not ui.confirm("Reverse all actions in this session?", default=False):
            ui.warning("Cancelled.")
            return 0
    restored, errors = tx.undo_session(last["id"])
    ui.success(f"Restored {restored} items ({errors} errors)")
    return 0


def cmd_stats(args, config):
    from boxraudio.stats import show_stats
    show_stats()
    return 0


def cmd_history(args, config):
    if args.detailed:
        from boxraudio.stats import show_history_detailed
        show_history_detailed()
    else:
        from boxraudio.transaction import TransactionLog
        tx = TransactionLog()
        sessions = tx.list_sessions(limit=20)
        if not sessions:
            ui.warning("No sessions recorded yet.")
            return 0
        ui.section("Recent sessions")
        rows = {}
        for s in sessions:
            started = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"]))
            label = f"#{s['id']:>4}  {started}  [{s['status']}]"
            description = s.get("description") or "(no description)"
            rows[label] = description
        ui.kv_table("Session history", rows)
    return 0


def cmd_list_resumable(args, config):
    from boxraudio.resume import find_resumable
    resumable = find_resumable()
    if not resumable:
        ui.success("No interrupted sessions to resume.")
        return 0
    ui.section("Interrupted sessions")
    for r in resumable:
        interrupted = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(r.get("interrupted_at", 0))
        )
        ui.kv_table(f"Session #{r['session_id']}", {
            "Profile":          r.get("profile") or "(ad-hoc)",
            "Interrupted at":   interrupted,
            "Reason":           r.get("interrupt_reason", "?"),
            "Current step":     r.get("current_step", "(none)"),
            "Completed steps":  ", ".join(r.get("completed_steps", [])) or "(none)",
        })
    return 0


def cmd_resume(args, config):
    from boxraudio.resume import find_resumable, remove_checkpoint
    from boxraudio.pipeline import run_pipeline

    resumable = find_resumable()
    if not resumable:
        ui.warning("No interrupted sessions found.")
        return 0
    latest = resumable[0]
    ui.section(f"Resuming session {latest['session_id']}")
    ui.info(f"Profile: {latest.get('profile') or '(ad-hoc)'}")
    ui.info(f"Completed steps: {', '.join(latest.get('completed_steps', [])) or '(none)'}")
    if not args.no_confirm:
        if not ui.confirm("Resume this session?", default=True):
            ui.warning("Cancelled.")
            return 0
    saved_args = latest.get("args", {})
    saved_args["resume_checkpoint"] = latest
    saved_args["dry_run"] = False
    exit_code = run_pipeline(saved_args)
    if exit_code == 0:
        remove_checkpoint(latest["_checkpoint_path"])
    return exit_code


# ─── Audits and reports ───────────────────────────────────────────────────────

def cmd_audits(args, config):
    """Run any combination of audit-quality, audit-tags, report-quality."""
    target = _resolve_target(args, config)
    if not target:
        ui.error(
            "Audit/report commands need --target, --source, --backup, "
            "or a --profile that provides one."
        )
        return 1

    defaults = get_defaults(config)
    cache_path = args.cache_file or defaults.get("cache_file")
    workers = args.workers or defaults.get("workers", 4)

    from boxraudio import audits

    if args.report_quality:
        audits.quality_report(target, cache_path=cache_path, workers=workers)
    if args.audit_tags:
        audits.tag_audit(
            target,
            cache_path=cache_path,
            workers=workers,
            art_size_threshold_mb=args.art_threshold_mb,
        )
    if args.audit_quality:
        audits.spectrum_audit(target, show_reasons=not args.no_reasons)

    return 0


def _resolve_target(args, config):
    target = args.target
    if target:
        return os.path.expanduser(target)

    candidate = args.backup or args.source
    if not candidate and args.dedupe_search:
        candidate = args.dedupe_search[0]
    if not candidate and args.profile:
        try:
            profile = get_profile(config, args.profile[0])
            candidate = profile.get("backup") or profile.get("source")
            if not candidate and profile.get("dedupe_search"):
                candidate = profile["dedupe_search"][0]
        except ConfigError:
            pass

    return os.path.expanduser(candidate) if candidate else None


# ─── Sanitization ─────────────────────────────────────────────────────────────

def cmd_sanitize_tags(args, config):
    target = _resolve_target(args, config)
    if not target:
        ui.error(
            "--sanitize-tags needs --target, --source, --backup, "
            "or a --profile that provides one."
        )
        return 1

    from boxraudio.sanitize import (
        sanitize_directory, DEFAULT_KEEP_TAGS, ALWAYS_STRIP_PATTERNS
    )
    from boxraudio.preflight import format_bytes
    from boxraudio.errors import translate_error

    defaults = get_defaults(config)
    keep_tags = set(DEFAULT_KEEP_TAGS)
    for t in defaults.get("keep_tags") or []:
        keep_tags.add(t.lower())
    if args.keep_tag:
        for t in args.keep_tag:
            keep_tags.add(t.lower())

    ui.section(f"Sanitize tags — {target}")
    ui.info(f"Mode: {'DRY RUN' if not args.run else 'LIVE RUN'}")
    ui.info(f"Strip album art:  {'yes' if args.strip_art else 'no (keep)'}")
    ui.info(f"Strip lyrics:     {'yes' if args.strip_lyrics else 'no (keep)'}")
    ui.info(f"Keep tags ({len(keep_tags)}): {', '.join(sorted(keep_tags))}")

    if args.run and not args.no_confirm:
        if not ui.confirm(
            "Proceed with sanitization? (NOT REVERSIBLE — --undo will not restore stripped tags)",
            default=False,
        ):
            ui.warning("Cancelled.")
            return 0

    result = sanitize_directory(
        target,
        keep_tags=keep_tags,
        strip_art=args.strip_art,
        strip_lyrics=args.strip_lyrics,
        strip_patterns=ALWAYS_STRIP_PATTERNS,
        dry_run=not args.run,
        art_size_threshold_mb=args.art_threshold_mb,
    )

    summary = {
        "Total files":   f"{result['total_files']:,}",
        "Modified":      f"{result['modified_files']:,}",
        "Errors":        f"{len(result['errors']):,}",
        "Oversized art": f"{len(result['oversized_art']):,}",
    }
    if args.strip_art and args.run:
        summary["Bytes freed"] = format_bytes(result["total_bytes_freed"])
    ui.kv_table("Sanitize summary", summary)

    if result["oversized_art"]:
        result["oversized_art"].sort(key=lambda x: x[1], reverse=True)
        ui.file_table(
            "Files with oversized art",
            [f"{format_bytes(size)}  →  {fp}" for fp, size in result["oversized_art"]],
            limit=20,
        )

    if result["errors"]:
        ui.warning(f"{len(result['errors'])} errors occurred:")
        ui.file_table(
            "Sanitization errors",
            [f"{fp}  —  {translate_error(err, fp)}" for fp, err in result["errors"]],
            limit=50,
        )

    return 0


# ─── Interactive ──────────────────────────────────────────────────────────────

def cmd_interactive(args, config):
    from boxraudio.interactive import interactive_mode
    from boxraudio.pipeline import run_pipeline

    pipeline_args = interactive_mode(args.config)
    if not pipeline_args:
        ui.warning("Interactive setup cancelled.")
        return 0
    return run_pipeline(pipeline_args)


# ─── Main pipeline ────────────────────────────────────────────────────────────

def cmd_pipeline(args, config):
    """Build args from defaults + profile + CLI and run pipeline (possibly multiple times)."""
    from boxraudio.pipeline import run_pipeline

    defaults = get_defaults(config)
    profiles_to_run = args.profile or [None]
    overall_exit_code = 0

    for profile_name in profiles_to_run:
        if profile_name and len(profiles_to_run) > 1:
            ui.section(f"Running profile: {profile_name}")

        pipeline_args = _build_pipeline_args(args, config, defaults, profile_name)

        exit_code = run_pipeline(pipeline_args)
        if exit_code != 0:
            overall_exit_code = exit_code
            if len(profiles_to_run) > 1:
                ui.warning(
                    f"Profile {profile_name} returned exit code {exit_code}; "
                    f"continuing with remaining profiles."
                )

    return overall_exit_code


def _build_pipeline_args(args, config, defaults, profile_name):
    """Compose pipeline args from defaults, profile, and CLI overrides."""
    pipeline_args = {
        "source":           defaults.get("source"),
        "backup":           defaults.get("backup"),
        "destinations":     defaults.get("destinations") or
                            ([defaults["destination"]] if defaults.get("destination") else []),
        "destination":      defaults.get("destination"),
        "sync_source":      defaults.get("sync_source"),
        "sync_destinations": defaults.get("sync_destinations") or
                             ([defaults["sync_destination"]] if defaults.get("sync_destination") else []),
        "dedupe_format":    defaults.get("dedupe_format"),
        "dedupe_search":    defaults.get("dedupe_search") or [],
        "dedupe_method":    defaults.get("dedupe_method", "tag"),
        "clean_empty_dirs": defaults.get("clean_empty_dirs", False),
        "mirror":           defaults.get("mirror", False),
        "size_only":        defaults.get("size_only", True),
        "rsync_flags":      defaults.get("rsync_flags"),
        "cache_file":       defaults.get("cache_file"),
        "workers":          defaults.get("workers", 4),
        "dry_run":          not args.run,
        "no_confirm":       args.no_confirm,
        "rebuild_cache":    args.rebuild_cache,
        "profile":          profile_name,
        "report":           args.report,
        "sanitize_on_move": defaults.get("sanitize_on_move", False),
        "strip_art":        defaults.get("strip_art", False),
        "strip_lyrics":     defaults.get("strip_lyrics", False),
        "keep_tags":        defaults.get("keep_tags") or [],
    }

    if profile_name:
        try:
            profile = get_profile(config, profile_name)
        except ConfigError as e:
            ui.error(str(e))
            sys.exit(1)
        if "destination" in profile and "destinations" not in profile:
            profile["destinations"] = [profile["destination"]]
        if "sync_destination" in profile and "sync_destinations" not in profile:
            profile["sync_destinations"] = [profile["sync_destination"]]
        for k, v in profile.items():
            if k in pipeline_args and v is not None:
                pipeline_args[k] = v

    cli_overrides = {
        "source":            args.source,
        "backup":            args.backup,
        "destinations":      args.destination,
        "sync_source":       args.sync_source,
        "sync_destinations": args.sync_destination,
        "dedupe_format":     args.dedupe_format,
        "dedupe_search":     args.dedupe_search,
        "dedupe_method":     args.dedupe_method,
        "rsync_flags":       args.rsync_flags,
        "cache_file":        args.cache_file,
        "workers":           args.workers,
    }
    for k, v in cli_overrides.items():
        if v is not None:
            pipeline_args[k] = v

    if args.clean_empty_dirs:
        pipeline_args["clean_empty_dirs"] = True
    if args.mirror:
        pipeline_args["mirror"] = True
    if args.size_only:
        pipeline_args["size_only"] = True
    if args.sanitize_on_move:
        pipeline_args["sanitize_on_move"] = True
    if args.strip_art:
        pipeline_args["strip_art"] = True
    if args.strip_lyrics:
        pipeline_args["strip_lyrics"] = True
    if args.keep_tag:
        pipeline_args["keep_tags"] = list(set(
            (pipeline_args.get("keep_tags") or []) + args.keep_tag
        ))

    for k in ("source", "backup", "sync_source", "cache_file"):
        if pipeline_args.get(k):
            pipeline_args[k] = os.path.expanduser(pipeline_args[k])
    for list_key in ("destinations", "sync_destinations", "dedupe_search"):
        if pipeline_args.get(list_key):
            pipeline_args[list_key] = [
                os.path.expanduser(p) for p in pipeline_args[list_key]
            ]

    return pipeline_args
