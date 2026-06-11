"""
resume.py — interrupt-safe checkpointing and resume support.

The pipeline writes checkpoint files at key points in its execution.
If killed (Ctrl+C, power loss, crash), the next run can detect the
unfinished checkpoint and offer to resume from where it left off.

Checkpoints are stored as JSON files in ~/.boxraudio_checkpoints/.
Each session gets one checkpoint file named after its session ID.
"""

import os
import json
import time
import signal
from pathlib import Path


CHECKPOINT_DIR = os.path.expanduser("~/.boxraudio_checkpoints")
CHECKPOINT_MAX_AGE = 7 * 24 * 3600   # 7 days


class Checkpoint:
    """
    A pipeline checkpoint. Records the current step, progress within the step,
    and enough context to resume.
    """
    def __init__(self, session_id: int, profile: str = None,
                 directory: str = None):
        self.session_id = session_id
        self.directory = directory or CHECKPOINT_DIR
        os.makedirs(self.directory, exist_ok=True)
        self.path = os.path.join(self.directory, f"session_{session_id}.json")
        self.data = {
            "session_id":     session_id,
            "profile":        profile,
            "started_at":     int(time.time()),
            "current_step":   None,
            "step_progress":  {},
            "completed_steps": [],
            "args":           {},
            "status":         "active",
        }
        self._install_signal_handlers()

    def _install_signal_handlers(self):
        """Save checkpoint on SIGINT and SIGTERM."""
        def handler(signum, frame):
            self.mark_interrupted(reason=f"signal_{signum}")
            # Re-raise so default handling still happens
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, OSError):
            # Signal handlers can fail in non-main threads
            pass

    def set_args(self, args: dict):
        """Store the pipeline args for resume."""
        # Don't store sensitive or non-serializable items
        safe = {}
        for k, v in args.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                continue
        self.data["args"] = safe
        self.save()

    def begin_step(self, step_name: str, total: int = None):
        """Mark the start of a new step."""
        self.data["current_step"] = step_name
        self.data["step_progress"] = {
            "name":  step_name,
            "total": total,
            "completed": 0,
            "items_done": [],
        }
        self.save()

    def update_progress(self, completed: int = None, item_done: str = None):
        """Update progress within the current step."""
        progress = self.data["step_progress"]
        if completed is not None:
            progress["completed"] = completed
        if item_done is not None and len(progress.get("items_done", [])) < 1000:
            progress.setdefault("items_done", []).append(item_done)
        self.save()

    def complete_step(self, step_name: str):
        """Mark a step as complete."""
        if step_name not in self.data["completed_steps"]:
            self.data["completed_steps"].append(step_name)
        self.data["current_step"] = None
        self.save()

    def mark_interrupted(self, reason: str = "unknown"):
        """Mark the checkpoint as interrupted."""
        self.data["status"] = "interrupted"
        self.data["interrupted_at"] = int(time.time())
        self.data["interrupt_reason"] = reason
        self.save()

    def mark_complete(self):
        """Mark the entire pipeline as complete and remove the checkpoint."""
        self.data["status"] = "complete"
        try:
            os.remove(self.path)
        except OSError:
            pass

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass


def find_resumable() -> list:
    """Return a list of resumable checkpoint data dicts (most recent first)."""
    if not os.path.isdir(CHECKPOINT_DIR):
        return []
    found = []
    for fn in os.listdir(CHECKPOINT_DIR):
        if not fn.startswith("session_") or not fn.endswith(".json"):
            continue
        path = os.path.join(CHECKPOINT_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") == "interrupted":
                data["_checkpoint_path"] = path
                found.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    found.sort(key=lambda d: d.get("interrupted_at", 0), reverse=True)
    return found


def remove_checkpoint(checkpoint_path: str):
    """Delete a checkpoint file."""
    try:
        os.remove(checkpoint_path)
    except OSError:
        pass


def cleanup_old_checkpoints():
    """Delete checkpoints older than CHECKPOINT_MAX_AGE."""
    if not os.path.isdir(CHECKPOINT_DIR):
        return
    now = time.time()
    for fn in os.listdir(CHECKPOINT_DIR):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(CHECKPOINT_DIR, fn)
        try:
            age = now - os.path.getmtime(path)
            if age > CHECKPOINT_MAX_AGE:
                os.remove(path)
        except OSError:
            continue
