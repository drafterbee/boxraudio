"""
cache.py — SQLite-backed tag cache for BoxR.
"""

import os
import sqlite3
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    filepath    TEXT PRIMARY KEY,
    mtime       INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    artist      TEXT,
    album       TEXT,
    title       TEXT,
    bitrate     INTEGER,
    duration    REAL,
    format      TEXT,
    scanned_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_tags
    ON files (artist, album, title);
"""


class TagCache:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get(self, filepath: str, mtime: int, size: int):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE filepath = ? AND mtime = ? AND size = ?",
                (filepath, mtime, size),
            ).fetchone()
            return dict(row) if row else None

    def put(self, filepath: str, mtime: int, size: int,
            artist: str, album: str, title: str,
            bitrate: int = None, duration: float = None, format: str = None,
            scanned_at: int = 0):
        import time
        if scanned_at == 0:
            scanned_at = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO files
                    (filepath, mtime, size, artist, album, title,
                     bitrate, duration, format, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (filepath, mtime, size, artist, album, title,
                 bitrate, duration, format, scanned_at),
            )

    def put_many(self, entries):
        import time
        now = int(time.time())
        with self._connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO files
                    (filepath, mtime, size, artist, album, title,
                     bitrate, duration, format, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e["filepath"], e["mtime"], e["size"],
                        e.get("artist"), e.get("album"), e.get("title"),
                        e.get("bitrate"), e.get("duration"), e.get("format"),
                        e.get("scanned_at", now),
                    )
                    for e in entries
                ],
            )

    def find_by_tags(self, artist: str, album: str, title: str):
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT filepath FROM files
                 WHERE LOWER(artist) = LOWER(?)
                   AND LOWER(album)  = LOWER(?)
                   AND LOWER(title)  = LOWER(?)
                """,
                (artist or "", album or "", title or ""),
            ).fetchall()
            return [r["filepath"] for r in rows]

    def delete(self, filepath: str):
        with self._connection() as conn:
            conn.execute("DELETE FROM files WHERE filepath = ?", (filepath,))

    def prune(self, valid_filepaths: set):
        with self._connection() as conn:
            existing = conn.execute("SELECT filepath FROM files").fetchall()
            to_delete = [r["filepath"] for r in existing
                         if r["filepath"] not in valid_filepaths]
            if to_delete:
                conn.executemany(
                    "DELETE FROM files WHERE filepath = ?",
                    [(p,) for p in to_delete],
                )
            return len(to_delete)

    def count(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()
            return row["c"]

    def all_paths(self):
        with self._connection() as conn:
            rows = conn.execute("SELECT filepath FROM files").fetchall()
            return [r["filepath"] for r in rows]

    def vacuum(self):
        with self._connection() as conn:
            conn.execute("VACUUM")
