"""
interactive.py — guided interactive mode for BoxR.

Walks the user through pipeline setup via Rich prompts instead of CLI flags.
Useful for first-time setup, occasional users, or building out new profiles.
"""

import os
import yaml
from pathlib import Path

from boxraudio import ui
from boxraudio.config import (
    load_config, list_profiles, get_profile,
    DEFAULT_CONFIG_PATH, ConfigError
)


def interactive_mode(config_path: str = None) -> dict:
    """
    Run an interactive session to build a set of pipeline args.
    Returns a dict ready to pass to run_pipeline, or None if user cancels.
    """
    ui.section("BoxR Interactive Mode")
    ui.info("This will walk you through setting up a sync operation.")
    ui.info("Press Ctrl+C at any time to exit.")

    try:
        config = load_config(config_path)
    except ConfigError as e:
        ui.error(str(e))
        return None

    profiles = list_profiles(config)

    args = {}

    # ── Choose: use existing profile or build manually ───────────────────────
    ui.section("Profile selection")

    if profiles:
        ui.info("Existing profiles:")
        for p in profiles:
            ui.dim(f"  • {p}")
        use_profile = ui.confirm("Use one of these profiles?", default=True)
        if use_profile:
            profile_name = _select_from_list("Which profile?", profiles)
            if profile_name:
                try:
                    profile_args = get_profile(config, profile_name)
                    args.update(profile_args)
                    args["profile"] = profile_name
                    ui.success(f"Loaded profile: {profile_name}")
                except ConfigError as e:
                    ui.error(str(e))
                    return None

    # ── Allow overrides / fill in missing ────────────────────────────────────
    ui.section("Pipeline configuration")

    args["source"] = _prompt_path(
        "Source directory (new files to process)",
        default=args.get("source") or os.path.expanduser("~/Desktop/MusicTFR"),
        must_exist=True,
    )
    if not args["source"]:
        ui.warning("Source is required.")
        return None

    args["backup"] = _prompt_path(
        "Backup directory (where source files get moved; press Enter to skip)",
        default=args.get("backup") or "",
        must_exist=False,
    )

    # Multi-destination
    destinations = args.get("destinations") or []
    if args.get("destination"):
        destinations = [args["destination"]]
    if destinations:
        ui.dim(f"Currently configured destinations:")
        for d in destinations:
            ui.dim(f"  • {d}")
        if not ui.confirm("Keep these destinations?", default=True):
            destinations = []

    while True:
        new_dest = _prompt_path(
            f"Add a destination (Enter to {'finish' if destinations else 'skip'})",
            default="",
            must_exist=False,
        )
        if not new_dest:
            break
        destinations.append(new_dest)

    args["destinations"] = destinations
    args["destination"]  = destinations[0] if destinations else None

    # Sync source override
    if args.get("sync_source"):
        ui.dim(f"Current sync_source: {args['sync_source']}")
        if not ui.confirm("Keep this sync source?", default=True):
            args["sync_source"] = _prompt_path(
                "New sync source (Enter to use default)",
                default="",
                must_exist=False,
            ) or None
    elif args.get("backup") and destinations:
        if ui.confirm("Use a separate sync_source path (different from backup)?",
                      default=False):
            args["sync_source"] = _prompt_path(
                "Sync source",
                default=args.get("backup") or "",
                must_exist=False,
            ) or None

    # Dedup
    if not args.get("dedupe_format"):
        if ui.confirm("Deduplicate a lower-quality format?", default=False):
            args["dedupe_format"] = ui.prompt(
                "Which format to dedupe (e.g. mp3)", default="mp3"
            )

    if args.get("dedupe_format"):
        existing_searches = args.get("dedupe_search") or []
        if existing_searches:
            ui.dim("Currently configured dedup search paths:")
            for s in existing_searches:
                ui.dim(f"  • {s}")
            if not ui.confirm("Keep these?", default=True):
                existing_searches = []
        while True:
            new_search = _prompt_path(
                f"Add dedup search path (Enter to {'finish' if existing_searches else 'skip'})",
                default="",
                must_exist=False,
            )
            if not new_search:
                break
            existing_searches.append(new_search)
        args["dedupe_search"] = existing_searches

    # Cleanup empty dirs
    args["clean_empty_dirs"] = ui.confirm(
        "Clean up empty/audio-free directories after dedup?",
        default=args.get("clean_empty_dirs", True),
    )

    # Sync options
    args["mirror"] = ui.confirm(
        "Use rsync mirror mode (deletes files at destination not in source)?",
        default=args.get("mirror", False),
    )
    args["size_only"] = ui.confirm(
        "Use --size-only (recommended for FAT32 devices)?",
        default=args.get("size_only", True),
    )

    # Dedupe method
    if args.get("dedupe_format"):
        from boxraudio.fingerprint import is_available as fp_available
        if fp_available():
            if ui.confirm(
                "Use content-based fingerprint matching (slower but more accurate)?",
                default=False,
            ):
                args["dedupe_method"] = "fingerprint"

    # Confirm and run
    ui.section("Summary")
    summary = {
        "Source":            args.get("source"),
        "Backup":            args.get("backup") or "(none)",
        "Destinations":      "\n".join(args.get("destinations", [])) or "(none)",
        "Sync source":       args.get("sync_source") or "(default)",
        "Dedupe format":     args.get("dedupe_format") or "(none)",
        "Dedupe searches":   "\n".join(args.get("dedupe_search") or []) or "(none)",
        "Clean empty dirs":  "yes" if args.get("clean_empty_dirs") else "no",
        "Mirror sync":       "yes" if args.get("mirror") else "no",
        "Size-only sync":    "yes" if args.get("size_only") else "no",
        "Dedupe method":     args.get("dedupe_method", "tag (default)"),
    }
    ui.kv_table("Pipeline configuration", summary)

    do_live = ui.confirm("Execute live (write changes)? (No = dry run)", default=False)
    args["dry_run"] = not do_live

    # Offer to save as a profile
    if ui.confirm("Save this configuration as a profile?", default=False):
        new_name = ui.prompt("Profile name", default="my_profile")
        try:
            _save_profile(config_path or DEFAULT_CONFIG_PATH, new_name, args)
            ui.success(f"Saved profile: {new_name}")
        except Exception as e:
            ui.error(f"Could not save profile: {e}")

    return args


def _prompt_path(message: str, default: str = "", must_exist: bool = False) -> str:
    """Prompt for a path, expanding ~ and optionally checking existence."""
    result = ui.prompt(message, default=default).strip()
    if not result:
        return ""
    result = os.path.expanduser(result)
    if must_exist and not os.path.exists(result):
        ui.error(f"Path does not exist: {result}")
        return _prompt_path(message, default, must_exist)
    return result


def _select_from_list(message: str, options: list) -> str:
    """Show numbered options and let user pick one."""
    ui.console.print(f"\n  [bold]{message}[/bold]")
    for i, opt in enumerate(options, 1):
        ui.console.print(f"    [cyan]{i}[/cyan]  {opt}")
    while True:
        choice = ui.prompt("Enter number (or blank to cancel)", default="")
        if not choice:
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        ui.warning("Invalid choice — try again")


def _save_profile(config_path: str, name: str, args: dict):
    """Append a new profile to the YAML config file."""
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    if "profiles" not in data:
        data["profiles"] = {}

    profile_data = {}
    for key in ("source", "backup", "destinations", "destination",
                "sync_source", "sync_destinations",
                "dedupe_format", "dedupe_search",
                "clean_empty_dirs", "mirror", "size_only",
                "dedupe_method"):
        v = args.get(key)
        if v is not None and v != "" and v != [] and v != "(none)":
            profile_data[key] = v

    data["profiles"][name] = profile_data

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
