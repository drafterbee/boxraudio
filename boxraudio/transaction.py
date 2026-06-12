"""
transaction.py — operation logging and undo support for BoxR.
"""

import os
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


from boxraudio.constants import QUARANTINE_MAX_AGE_SECS

TRANSACTION_DB        = os.path.expanduser("~/.boxraudio_transactions.db")
QUARANTINE_DIR        = os.path.expanduser("~/.boxraudio_quarantine")
QUARANTINE_MAX_AGE    = QUARANTINE_MAX_AGE_SECS


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    status      TEXT NOT NULL DEFAULT 'open',
    description TEXT,
    profile     TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_session
    ON actions (session_id);
"""


class TransactionLog:
    def __init__(self, db_path: str = None, quarantine: str = None):
        self.db_path = db_path or TRANSACTION_DB
        self.quarantine = quarantine or QUARANTINE_DIR
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        os.makedirs(self.quarantine, exist_ok=True)
        self._init_db()
        self.session_id = None

    def _init_db(self):
        with self._connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def start_session(self, description: str = "", profile: str = None) -> int:
        now = int(time.time())
        with self._connection() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (started_at, description, profile, status) "
                "VALUES (?, ?, ?, 'open')",
                (now, description, profile),
            )
            self.session_id = cur.lastrowid
        return self.session_id

    def end_session(self, status: str = "complete"):
        if not self.session_id:
            return
        with self._connection() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, status = ? WHERE id = ?",
                (int(time.time()), status, self.session_id),
            )

    def _log_action(self, action_type: str, payload: dict):
        if not self.session_id:
            self.start_session()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO actions (session_id, action_type, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (self.session_id, action_type, json.dumps(payload), int(time.time())),
            )

    def log_move(self, src: str, dest: str):
        self._log_action("move", {"src": src, "dest": dest})

    def log_delete(self, filepath: str):
        if not self.session_id:
            self.start_session()
        quarantine_base = os.path.join(self.quarantine, f"session_{self.session_id}")
        os.makedirs(quarantine_base, exist_ok=True)

        # Check space before copying — refuse to add to quarantine if it would
        # fill the disk holding the quarantine directory
        try:
            file_size = os.path.getsize(filepath)
            disk_usage = shutil.disk_usage(self.quarantine)
            # Require 1GB free buffer + 2x the file size as safety margin
            if disk_usage.free < (file_size * 2 + 1_000_000_000):
                # Skip quarantine — log without backup
                self._log_action("delete", {
                    "original":   filepath,
                    "quarantine": None,
                    "note":       "quarantine skipped due to low disk space",
                })
                return
        except OSError:
            pass

        rel = filepath.lstrip("/")
        quarantine_path = os.path.join(quarantine_base, rel)
        os.makedirs(os.path.dirname(quarantine_path), exist_ok=True)

        try:
            shutil.copy2(filepath, quarantine_path)
        except OSError:
            quarantine_path = None

        self._log_action("delete", {
            "original":   filepath,
            "quarantine": quarantine_path,
        })

    def log_remove_directory(self, dirpath: str):
        if not self.session_id:
            self.start_session()
        quarantine_base = os.path.join(self.quarantine, f"session_{self.session_id}")
        os.makedirs(quarantine_base, exist_ok=True)
        rel = dirpath.lstrip("/")
        quarantine_path = os.path.join(quarantine_base, rel)
        try:
            if os.path.exists(dirpath):
                shutil.copytree(dirpath, quarantine_path, dirs_exist_ok=True)
        except OSError:
            quarantine_path = None

        self._log_action("remove_directory", {
            "original":   dirpath,
            "quarantine": quarantine_path,
        })

    def last_session(self):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE status = 'complete' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def list_sessions(self, limit: int = 20):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def session_actions(self, session_id: int):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM actions WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            return [{
                "id":          r["id"],
                "action_type": r["action_type"],
                "payload":     json.loads(r["payload"]),
                "created_at":  r["created_at"],
            } for r in rows]

    def undo_session(self, session_id: int):
        actions = self.session_actions(session_id)
        restored, errors = 0, 0

        for action in reversed(actions):
            try:
                t       = action["action_type"]
                payload = action["payload"]
                if t == "move":
                    if os.path.exists(payload["dest"]) and not os.path.exists(payload["src"]):
                        os.makedirs(os.path.dirname(payload["src"]), exist_ok=True)
                        shutil.move(payload["dest"], payload["src"])
                        restored += 1
                elif t == "delete":
                    if payload.get("quarantine") and os.path.exists(payload["quarantine"]):
                        os.makedirs(os.path.dirname(payload["original"]), exist_ok=True)
                        shutil.copy2(payload["quarantine"], payload["original"])
                        restored += 1
                elif t == "remove_directory":
                    if payload.get("quarantine") and os.path.exists(payload["quarantine"]):
                        os.makedirs(os.path.dirname(payload["original"]), exist_ok=True)
                        shutil.copytree(payload["quarantine"], payload["original"], dirs_exist_ok=True)
                        restored += 1
            except Exception:
                errors += 1

        with self._connection() as conn:
            conn.execute(
                "UPDATE sessions SET status = 'undone' WHERE id = ?",
                (session_id,),
            )

        return restored, errors

    def cleanup_old_quarantine(self):
        now = time.time()
        for entry in os.listdir(self.quarantine):
            full = os.path.join(self.quarantine, entry)
            if os.path.getmtime(full) < now - QUARANTINE_MAX_AGE:
                try:
                    shutil.rmtree(full)
                except OSError:
                    pass
