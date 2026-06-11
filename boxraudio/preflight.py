"""
preflight.py — pre-flight checks before destructive operations.
"""

import os
import shutil
import psutil
from pathlib import Path

from boxraudio import ui


def get_disk_free(path: str) -> int:
    if not os.path.exists(path):
        p = Path(path)
        while not p.exists() and p != p.parent:
            p = p.parent
        path = str(p)
    usage = shutil.disk_usage(path)
    return usage.free


def get_total_size(filepaths) -> int:
    total = 0
    for fp in filepaths:
        try:
            total += os.path.getsize(fp)
        except OSError:
            pass
    return total


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def check_disk_space(destination: str, needed_bytes: int, safety_margin: float = 1.1):
    if not os.path.exists(destination):
        return True, 0, needed_bytes
    free = get_disk_free(destination)
    required = int(needed_bytes * safety_margin)
    return free >= required, free, required


def preflight_summary(plan: dict):
    table_data = {}

    source_files = plan.get("source_files", [])
    if source_files:
        size = get_total_size(source_files)
        table_data["Source files to process"] = f"{len(source_files):,} ({format_bytes(size)})"

    if plan.get("backup_target"):
        table_data["Backup target"] = plan["backup_target"]

    if plan.get("destination_target"):
        table_data["Destination target"] = plan["destination_target"]

    if plan.get("mp3_to_delete"):
        size = get_total_size(plan["mp3_to_delete"])
        table_data["Files to delete"] = f"{len(plan['mp3_to_delete']):,} ({format_bytes(size)})"

    if plan.get("empty_dirs"):
        table_data["Empty dirs to remove"] = f"{len(plan['empty_dirs']):,}"

    if plan.get("sync_source") and plan.get("sync_destination"):
        table_data["Sync"] = f"{plan['sync_source']} → {plan['sync_destination']}"

    if not table_data:
        ui.warning("No planned actions to summarize.")
        return

    ui.kv_table("PRE-FLIGHT SUMMARY", table_data, color="bright_yellow")


def verify_disk_space_for_plan(plan: dict) -> bool:
    ok = True

    source_files = plan.get("source_files", [])
    source_size  = get_total_size(source_files) if source_files else 0

    backup = plan.get("backup_target")
    if backup and source_size:
        has, free, required = check_disk_space(backup, source_size)
        if not has:
            ui.error(
                f"Backup target needs {format_bytes(required)} but only "
                f"{format_bytes(free)} free at {backup}"
            )
            ok = False
        else:
            ui.success(
                f"Backup space OK: {format_bytes(free)} free, "
                f"~{format_bytes(required)} needed at {backup}"
            )

    destination = plan.get("destination_target") or plan.get("sync_destination")
    if destination and source_size:
        has, free, required = check_disk_space(destination, source_size)
        if not has:
            ui.error(
                f"Destination needs {format_bytes(required)} but only "
                f"{format_bytes(free)} free at {destination}"
            )
            ok = False
        else:
            ui.success(
                f"Destination space OK: {format_bytes(free)} free, "
                f"~{format_bytes(required)} needed at {destination}"
            )

    return ok
