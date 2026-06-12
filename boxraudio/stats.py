"""
stats.py — library statistics and history tracking.

Records every run in a persistent SQLite database, enabling:
- Library size growth over time
- Files added/removed per session
- Format distribution trends
- Sync duration history
- Error/failure counts
"""

import os
import time
import sqlite3
import json
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path

from boxraudio import ui


STATS_DB = os.path.expanduser("~/.boxraudio_stats.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    duration_secs   REAL,
    profile         TEXT,
    source          TEXT,
    backup          TEXT,
    destination     TEXT,
    files_moved     INTEGER DEFAULT 0,
    files_deleted   INTEGER DEFAULT 0,
    dirs_removed    INTEGER DEFAULT 0,
    files_synced    INTEGER DEFAULT 0,
    bytes_synced    INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    status          TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS library_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     INTEGER NOT NULL,
    target_path     TEXT NOT NULL,
    total_files     INTEGER NOT NULL,
    total_bytes     INTEGER NOT NULL,
    format_data     TEXT NOT NULL,
    bitrate_data    TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started
    ON runs (started_at);

CREATE INDEX IF NOT EXISTS idx_snap_target_time
    ON library_snapshots (target_path, captured_at);
"""


class StatsTracker:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or STATS_DB
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()
        self.current_run_id = None

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

    def start_run(self, profile: str = None, source: str = None,
                  backup: str = None, destination: str = None) -> int:
        with self._connection() as conn:
            cur = conn.execute(
                """INSERT INTO runs
                       (started_at, profile, source, backup, destination, status)
                   VALUES (?, ?, ?, ?, ?, 'in_progress')""",
                (int(time.time()), profile, source, backup, destination),
            )
            self.current_run_id = cur.lastrowid
        return self.current_run_id

    def record_metrics(self, **kwargs):
        """Increment metrics on the current run."""
        if not self.current_run_id:
            return
        allowed = {
            "files_moved", "files_deleted", "dirs_removed",
            "files_synced", "bytes_synced", "errors",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v}
        if not updates:
            return
        # Use SQL increment so multiple steps add to existing values
        set_clause = ", ".join(f"{k} = COALESCE({k}, 0) + ?" for k in updates)
        params = list(updates.values()) + [self.current_run_id]
        with self._connection() as conn:
            conn.execute(f"UPDATE runs SET {set_clause} WHERE id = ?", params)

    def end_run(self, status: str = "complete", notes: str = None):
        if not self.current_run_id:
            return
        now = int(time.time())
        with self._connection() as conn:
            row = conn.execute(
                "SELECT started_at FROM runs WHERE id = ?",
                (self.current_run_id,),
            ).fetchone()
            duration = (now - row["started_at"]) if row else None
            conn.execute(
                """UPDATE runs
                       SET ended_at = ?, duration_secs = ?, status = ?, notes = ?
                       WHERE id = ?""",
                (now, duration, status, notes, self.current_run_id),
            )

    def snapshot_library(self, target_path: str, scan_results: dict):
        """
        Record a snapshot of the library state from a scan result.
        scan_results is a dict like { 'all': [entry, ...] } from scan_files_parallel.
        """
        if not target_path or "all" not in scan_results:
            return

        total_files = len(scan_results["all"])
        total_bytes = 0
        by_format   = defaultdict(int)
        by_bitrate  = defaultdict(int)

        for entry in scan_results["all"]:
            total_bytes += entry.get("size", 0)
            fmt = entry.get("format") or "unknown"
            by_format[fmt] += 1
            br = entry.get("bitrate")
            if br:
                # Round to nearest 32 for bucketing
                bucket = (br // 32) * 32
                by_bitrate[bucket] += 1

        with self._connection() as conn:
            conn.execute(
                """INSERT INTO library_snapshots
                       (captured_at, target_path, total_files, total_bytes,
                        format_data, bitrate_data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    int(time.time()),
                    target_path,
                    total_files,
                    total_bytes,
                    json.dumps(dict(by_format)),
                    json.dumps(dict(by_bitrate)),
                ),
            )

    # ── Reporting ──────────────────────────────────────────────────────────────

    def all_runs(self, limit: int = 50):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def aggregate_totals(self):
        with self._connection() as conn:
            row = conn.execute(
                """SELECT
                        COUNT(*)              AS total_runs,
                        SUM(files_moved)      AS files_moved,
                        SUM(files_deleted)    AS files_deleted,
                        SUM(dirs_removed)     AS dirs_removed,
                        SUM(files_synced)     AS files_synced,
                        SUM(bytes_synced)     AS bytes_synced,
                        SUM(errors)           AS errors,
                        AVG(duration_secs)    AS avg_duration,
                        MIN(started_at)       AS first_run,
                        MAX(started_at)       AS last_run
                   FROM runs
                   WHERE status = 'complete'"""
            ).fetchone()
            return dict(row) if row else {}

    def runs_by_status(self):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM runs GROUP BY status",
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}

    def library_history(self, target_path: str, limit: int = 30):
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM library_snapshots
                       WHERE target_path = ?
                       ORDER BY captured_at DESC
                       LIMIT ?""",
                (target_path, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def all_tracked_libraries(self):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT target_path FROM library_snapshots",
            ).fetchall()
            return [r["target_path"] for r in rows]


# ─── Display helpers ─────────────────────────────────────────────────────────

def _fmt_bytes(n):
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _fmt_duration(secs):
    if not secs:
        return "—"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _fmt_time(ts):
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def show_stats(db_path: str = None):
    """Print an aggregate stats summary."""
    tracker = StatsTracker(db_path)

    totals = tracker.aggregate_totals()
    statuses = tracker.runs_by_status()

    ui.section("BoxR Statistics")

    if not totals.get("total_runs"):
        ui.warning("No runs recorded yet.")
        return

    overview = {
        "Total runs (completed)":  f"{totals['total_runs']:,}",
        "First run":               _fmt_time(totals.get("first_run")),
        "Most recent run":         _fmt_time(totals.get("last_run")),
        "Avg duration per run":    _fmt_duration(totals.get("avg_duration")),
    }
    ui.kv_table("Overview", overview)

    if statuses:
        ui.kv_table("Status breakdown", {k: f"{v:,}" for k, v in statuses.items()})

    activity = {
        "Files moved (total)":   f"{totals.get('files_moved') or 0:,}",
        "Files deleted (total)": f"{totals.get('files_deleted') or 0:,}",
        "Dirs removed (total)":  f"{totals.get('dirs_removed') or 0:,}",
        "Files synced (total)":  f"{totals.get('files_synced') or 0:,}",
        "Bytes synced (total)":  _fmt_bytes(totals.get("bytes_synced")),
        "Errors encountered":    f"{totals.get('errors') or 0:,}",
    }
    ui.kv_table("Activity totals", activity)

    libraries = tracker.all_tracked_libraries()
    if libraries:
        ui.section("Tracked libraries")
        for lib in libraries:
            history = tracker.library_history(lib, limit=2)
            if not history:
                continue
            latest = history[0]
            prior  = history[1] if len(history) > 1 else None

            growth_files = "—"
            growth_bytes = "—"
            if prior:
                df = latest["total_files"] - prior["total_files"]
                db = latest["total_bytes"] - prior["total_bytes"]
                sign_f = "+" if df >= 0 else ""
                sign_b = "+" if db >= 0 else ""
                growth_files = f"{sign_f}{df:,}"
                growth_bytes = f"{sign_b}{_fmt_bytes(db)}"

            ui.kv_table(lib, {
                "Latest snapshot": _fmt_time(latest["captured_at"]),
                "Total files":     f"{latest['total_files']:,} ({growth_files})",
                "Total size":      f"{_fmt_bytes(latest['total_bytes'])} ({growth_bytes})",
            })


def show_history_detailed(db_path: str = None, limit: int = 20):
    """Show recent runs in a detailed table."""
    tracker = StatsTracker(db_path)
    runs = tracker.all_runs(limit=limit)

    ui.section(f"Run history (last {limit})")
    if not runs:
        ui.warning("No runs recorded yet.")
        return

    for run in runs:
        when = _fmt_time(run["started_at"])
        dur = _fmt_duration(run["duration_secs"])
        status = run["status"] or "?"
        status_color = {"complete": "green", "in_progress": "yellow",
                        "rsync_failed": "red", "user_aborted": "yellow"}.get(status, "white")

        details = {
            "Status":         f"[{status_color}]{status}[/{status_color}]",
            "Started":        when,
            "Duration":       dur,
            "Profile":        run.get("profile") or "(ad-hoc)",
            "Files moved":    f"{run.get('files_moved') or 0:,}",
            "Files deleted":  f"{run.get('files_deleted') or 0:,}",
            "Dirs removed":   f"{run.get('dirs_removed') or 0:,}",
            "Files synced":   f"{run.get('files_synced') or 0:,}",
            "Bytes synced":   _fmt_bytes(run.get("bytes_synced")),
            "Errors":         f"{run.get('errors') or 0:,}",
        }
        ui.kv_table(f"Run #{run['id']}", details)


# ─── Convenience functions used by pipeline ──────────────────────────────────

def record_run(*args, **kwargs):
    """Kept for backwards compat — pipeline uses StatsTracker directly."""
    pass


def show_history(*args, **kwargs):
    """Alias for show_history_detailed."""
    show_history_detailed(*args, **kwargs)
