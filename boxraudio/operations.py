"""
operations.py — file operation primitives (move, delete, sync).
"""

import os
import shutil
import subprocess
from pathlib import Path

from boxraudio import ui
from boxraudio.scanner import AUDIO_EXTENSIONS
from boxraudio.transaction import TransactionLog


def directory_has_audio(dirpath: str) -> bool:
    for root, dirs, files in os.walk(dirpath):
        for filename in files:
            if Path(filename).suffix.lower() in AUDIO_EXTENSIONS:
                return True
    return False


def get_empty_dirs(base_dir: str):
    if not os.path.isdir(base_dir):
        return []
    empty    = []
    base_dir = str(Path(base_dir).resolve())
    for root, dirs, files in os.walk(base_dir, topdown=False):
        if root == base_dir:
            continue
        if not directory_has_audio(root):
            empty.append(root)
    empty.sort(key=lambda p: p.count(os.sep), reverse=True)
    deduplicated = []
    for path in empty:
        already_covered = any(
            listed.startswith(path + os.sep) for listed in deduplicated
        )
        if not already_covered:
            deduplicated.append(path)
    return deduplicated


def merge_move(src_dir: str, dest_dir: str, tx: TransactionLog,
               dry_run: bool = True):
    moved, errors = 0, 0
    if not os.path.isdir(src_dir):
        return 0, 0

    items = [i for i in os.listdir(src_dir) if not i.startswith(".")]
    if not items:
        return 0, 0

    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Moving items", total=len(items))

        for item in items:
            src_path  = os.path.join(src_dir, item)
            dest_path = os.path.join(dest_dir, item)

            if os.path.isfile(src_path):
                if dry_run:
                    ui.dim(f"WOULD MOVE FILE: {item}")
                else:
                    try:
                        if os.path.exists(dest_path):
                            ui.warning(f"SKIP (exists): {item}")
                        else:
                            shutil.move(src_path, dest_path)
                            tx.log_move(src_path, dest_path)
                            moved += 1
                    except Exception as e:
                        ui.error(f"{src_path} — {e}")
                        errors += 1
                progress.advance(task)
                continue

            if not os.path.isdir(dest_path):
                if dry_run:
                    ui.dim(f"WOULD CREATE+MOVE folder: {item}")
                else:
                    try:
                        shutil.move(src_path, dest_path)
                        tx.log_move(src_path, dest_path)
                        moved += 1
                    except Exception as e:
                        ui.error(f"{src_path} — {e}")
                        errors += 1
            else:
                ui.info(f"MERGING into existing: {item}")
                for sub in os.listdir(src_path):
                    if sub.startswith("."):
                        continue
                    sub_src  = os.path.join(src_path, sub)
                    sub_dest = os.path.join(dest_path, sub)
                    if dry_run:
                        if os.path.exists(sub_dest):
                            ui.dim(f"  WOULD SKIP (exists): {sub}")
                        else:
                            ui.dim(f"  WOULD MOVE: {sub}")
                    else:
                        try:
                            if os.path.exists(sub_dest):
                                ui.warning(f"  SKIP (exists): {sub}")
                            else:
                                shutil.move(sub_src, sub_dest)
                                tx.log_move(sub_src, sub_dest)
                                moved += 1
                        except Exception as e:
                            ui.error(f"  {sub_src} — {e}")
                            errors += 1
                if not dry_run:
                    try:
                        if not os.listdir(src_path):
                            os.rmdir(src_path)
                    except OSError:
                        pass
            progress.advance(task)

    return moved, errors


def delete_files(filepaths, tx: TransactionLog,
                 dry_run: bool = True, description: str = "Deleting"):
    deleted, errors = 0, 0
    if not filepaths:
        return 0, 0

    if dry_run:
        for fp in filepaths:
            ui.dim(f"WOULD DELETE: {fp}")
        return 0, 0

    progress = ui.make_progress()
    with progress:
        task = progress.add_task(description, total=len(filepaths))
        for fp in filepaths:
            try:
                tx.log_delete(fp)
                os.remove(fp)
                deleted += 1
            except Exception as e:
                ui.error(f"{fp} — {e}")
                errors += 1
            progress.advance(task)

    return deleted, errors


def remove_empty_directories(dirpaths, tx: TransactionLog,
                             dry_run: bool = True):
    removed, errors = 0, 0
    if not dirpaths:
        return 0, 0

    if dry_run:
        for d in dirpaths:
            ui.dim(f"WOULD REMOVE DIR: {d}")
        return 0, 0

    progress = ui.make_progress()
    with progress:
        task = progress.add_task("Removing directories", total=len(dirpaths))
        for d in dirpaths:
            try:
                tx.log_remove_directory(d)
                for root, sub_dirs, files in os.walk(d, topdown=False):
                    for f in files:
                        os.remove(os.path.join(root, f))
                    for sub in sub_dirs:
                        sub_path = os.path.join(root, sub)
                        try:
                            os.rmdir(sub_path)
                        except OSError:
                            pass
                os.rmdir(d)
                removed += 1
            except Exception as e:
                ui.error(f"{d} — {e}")
                errors += 1
            progress.advance(task)

    return removed, errors


def rsync_to_destination(source: str, destination: str,
                         mirror: bool = False, size_only: bool = True,
                         extra_flags: list = None,
                         dry_run: bool = True) -> int:
    cmd = [
        "rsync", "-rv",
        "--no-perms", "--no-owner", "--no-group", "--inplace",
        "--progress",
    ]
    if mirror:
        cmd.append("--delete-during")
    if size_only:
        cmd.append("--size-only")
    if extra_flags:
        cmd.extend(extra_flags)
    if dry_run:
        cmd.append("--dry-run")

    cmd.append(f"{source}/")
    cmd.append(f"{destination}/")

    ui.info(f"rsync command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        return e.returncode
