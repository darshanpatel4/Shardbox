"""The local index: folders, files, and which Telegram messages hold each piece.

Telegram itself has no folders, so this SQLite database is what turns a channel full of
documents into a drive. It is backed up to the channel automatically (see backup.py).
"""

from __future__ import annotations

import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    uid         TEXT NOT NULL UNIQUE,
    folder_id   INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    split_size  INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'uploading',
    source_path TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS parts (
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    message_id  INTEGER,
    PRIMARY KEY (file_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id);
"""

STATUS_UPLOADING = "uploading"
STATUS_COMPLETE = "complete"

_BAD_CHARS = set('/\\:*?"<>|')


def validate_name(name: str) -> str:
    """Names must be valid file names on Windows, macOS and Linux."""
    name = name.strip()
    if not name or name in (".", ".."):
        raise ValueError("The name cannot be empty.")
    bad = _BAD_CHARS & set(name)
    if bad:
        raise ValueError("The name cannot contain any of these characters: " + " ".join(sorted(bad)))
    if any(ord(c) < 32 for c in name):
        raise ValueError("The name cannot contain control characters.")
    if name.endswith("."):
        raise ValueError("The name cannot end with a dot.")
    return name


def sanitize_name(name: str) -> str:
    """Turn any local file name into a valid one instead of rejecting it."""
    cleaned = "".join("_" if (c in _BAD_CHARS or ord(c) < 32) else c for c in name).strip().rstrip(".")
    return cleaned or "unnamed"


def split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, "." + ext


@dataclass(frozen=True)
class Folder:
    id: int
    parent_id: int | None
    name: str
    created_at: float


@dataclass(frozen=True)
class FileRec:
    id: int
    uid: str
    folder_id: int | None
    name: str
    size: int
    sha256: str
    split_size: int
    status: str
    source_path: str | None
    created_at: float

    @property
    def complete(self) -> bool:
        return self.status == STATUS_COMPLETE


@dataclass(frozen=True)
class PartRec:
    file_id: int
    idx: int
    byte_offset: int
    size: int
    sha256: str
    message_id: int | None


class Index:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._listeners: list[Callable[[], None]] = []
        self._batch_depth = 0
        self._pending_notify = False
        self.conn = self._connect(self.path)

    # ----------------------------------------------------------------- plumbing

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        conn.commit()
        return conn

    def close(self) -> None:
        self.conn.close()

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _changed(self) -> None:
        self.conn.commit()
        if self._batch_depth:
            self._pending_notify = True
            return
        for callback in list(self._listeners):
            callback()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group many changes into a single change notification."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._pending_notify:
                self._pending_notify = False
                self._changed()

    @staticmethod
    def _folder(row: sqlite3.Row) -> Folder:
        return Folder(row["id"], row["parent_id"], row["name"], row["created_at"])

    @staticmethod
    def _file(row: sqlite3.Row) -> FileRec:
        return FileRec(
            row["id"], row["uid"], row["folder_id"], row["name"], row["size"], row["sha256"],
            row["split_size"], row["status"], row["source_path"], row["created_at"],
        )

    # ----------------------------------------------------------------- reading

    def get_folder(self, folder_id: int) -> Folder | None:
        row = self.conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        return self._folder(row) if row else None

    def get_file(self, file_id: int) -> FileRec | None:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return self._file(row) if row else None

    def children(self, folder_id: int | None) -> tuple[list[Folder], list[FileRec]]:
        folders = [
            self._folder(r)
            for r in self.conn.execute(
                "SELECT * FROM folders WHERE parent_id IS ? ORDER BY name COLLATE NOCASE", (folder_id,)
            )
        ]
        files = [
            self._file(r)
            for r in self.conn.execute(
                "SELECT * FROM files WHERE folder_id IS ? ORDER BY name COLLATE NOCASE", (folder_id,)
            )
        ]
        return folders, files

    def folder_chain(self, folder_id: int | None) -> list[Folder]:
        """Folders from the root down to folder_id (the root itself is not a row)."""
        chain: list[Folder] = []
        seen: set[int] = set()
        while folder_id is not None and folder_id not in seen:
            seen.add(folder_id)
            folder = self.get_folder(folder_id)
            if folder is None:
                break
            chain.append(folder)
            folder_id = folder.parent_id
        return list(reversed(chain))

    def folder_path(self, folder_id: int | None) -> str:
        return "/" + "/".join(f.name for f in self.folder_chain(folder_id))

    def descendant_folder_ids(self, folder_id: int) -> list[int]:
        rows = self.conn.execute(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT ?
                UNION ALL
                SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
            )
            SELECT id FROM sub
            """,
            (folder_id,),
        )
        return [r[0] for r in rows]

    def files_in_subtree(self, folder_id: int) -> list[FileRec]:
        ids = self.descendant_folder_ids(folder_id)
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM files WHERE folder_id IN ({marks})", ids)
        return [self._file(r) for r in rows]

    def parts(self, file_id: int) -> list[PartRec]:
        rows = self.conn.execute("SELECT * FROM parts WHERE file_id = ? ORDER BY idx", (file_id,))
        return [
            PartRec(r["file_id"], r["idx"], r["byte_offset"], r["size"], r["sha256"], r["message_id"])
            for r in rows
        ]

    def message_ids(self, file_ids: list[int]) -> list[int]:
        out: list[int] = []
        for start in range(0, len(file_ids), 500):
            chunk = file_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT message_id FROM parts WHERE file_id IN ({marks}) AND message_id IS NOT NULL", chunk
            )
            out.extend(r[0] for r in rows)
        return out

    def part_counts(self, file_ids: list[int]) -> dict[int, int]:
        if not file_ids:
            return {}
        marks = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT file_id, COUNT(*) FROM parts WHERE file_id IN ({marks}) GROUP BY file_id", file_ids
        )
        return {r[0]: r[1] for r in rows}

    def search(self, text: str, limit: int = 500) -> tuple[list[Folder], list[FileRec]]:
        pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        folders = [
            self._folder(r)
            for r in self.conn.execute(
                "SELECT * FROM folders WHERE name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE LIMIT ?",
                (pattern, limit),
            )
        ]
        files = [
            self._file(r)
            for r in self.conn.execute(
                "SELECT * FROM files WHERE name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE LIMIT ?",
                (pattern, limit),
            )
        ]
        return folders, files

    def stats(self) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE status = ?", (STATUS_COMPLETE,)
        ).fetchone()
        return row[0], row[1]

    def all_folders(self) -> list[Folder]:
        rows = self.conn.execute("SELECT * FROM folders ORDER BY name COLLATE NOCASE")
        return [self._folder(r) for r in rows]

    def recent_files(self, limit: int = 100) -> list[FileRec]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE status = ? ORDER BY created_at DESC LIMIT ?", (STATUS_COMPLETE, limit)
        )
        return [self._file(r) for r in rows]

    def incomplete_files(self) -> list[FileRec]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE status != ? ORDER BY created_at DESC", (STATUS_COMPLETE,)
        )
        return [self._file(r) for r in rows]

    def incomplete_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files WHERE status != ?", (STATUS_COMPLETE,)).fetchone()[0]

    def uploaded_part_counts(self, file_ids: list[int]) -> dict[int, int]:
        """How many pieces of each file have reached Telegram."""
        if not file_ids:
            return {}
        marks = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT file_id, COUNT(*) FROM parts WHERE file_id IN ({marks}) AND message_id IS NOT NULL "
            "GROUP BY file_id",
            file_ids,
        )
        return {r[0]: r[1] for r in rows}

    def folder_stats(self, folder_id: int) -> tuple[int, int]:
        """(number of files, total bytes) in a folder and everything below it."""
        files = self.files_in_subtree(folder_id)
        return len(files), sum(f.size for f in files)

    def has_uid(self, uid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM files WHERE uid = ?", (uid,)).fetchone() is not None

    def is_empty(self) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM files LIMIT 1").fetchone() is None
            and self.conn.execute("SELECT 1 FROM folders LIMIT 1").fetchone() is None
        )

    def find_resumable(
        self, folder_id: int | None, source_path: str, size: int, sha256: str, split_size: int
    ) -> FileRec | None:
        row = self.conn.execute(
            """SELECT * FROM files WHERE folder_id IS ? AND source_path = ? AND size = ? AND sha256 = ?
               AND split_size = ? AND status = ? ORDER BY id DESC LIMIT 1""",
            (folder_id, source_path, size, sha256, split_size, STATUS_UPLOADING),
        ).fetchone()
        return self._file(row) if row else None

    # ----------------------------------------------------------------- names

    def name_taken(
        self, folder_id: int | None, name: str, *, skip_file: int | None = None, skip_folder: int | None = None
    ) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM folders WHERE parent_id IS ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
            (folder_id, name, skip_folder),
        ).fetchone()
        if row:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM files WHERE folder_id IS ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
            (folder_id, name, skip_file),
        ).fetchone()
        return row is not None

    def unique_name(self, folder_id: int | None, name: str) -> str:
        if not self.name_taken(folder_id, name):
            return name
        stem, ext = split_ext(name)
        n = 1
        while True:
            candidate = f"{stem} ({n}){ext}"
            if not self.name_taken(folder_id, candidate):
                return candidate
            n += 1

    # ----------------------------------------------------------------- writing

    def create_folder(self, parent_id: int | None, name: str, *, make_unique: bool = False) -> Folder:
        name = validate_name(name)
        if make_unique:
            name = self.unique_name(parent_id, name)
        elif self.name_taken(parent_id, name):
            raise ValueError(f"“{name}” already exists here.")
        cur = self.conn.execute(
            "INSERT INTO folders(parent_id, name, created_at) VALUES (?, ?, ?)", (parent_id, name, time.time())
        )
        self._changed()
        return self.get_folder(cur.lastrowid)  # type: ignore[return-value]

    def ensure_path(self, path: str) -> int | None:
        """Return the folder for a path like "/a/b", creating missing folders."""
        folder_id: int | None = None
        for raw in path.strip("/").split("/"):
            if not raw:
                continue
            name = sanitize_name(raw)
            row = self.conn.execute(
                "SELECT id FROM folders WHERE parent_id IS ? AND name = ? COLLATE NOCASE", (folder_id, name)
            ).fetchone()
            if row:
                folder_id = row[0]
            else:
                folder_id = self.create_folder(folder_id, name, make_unique=True).id
        return folder_id

    def rename_folder(self, folder_id: int, new_name: str) -> None:
        folder = self.get_folder(folder_id)
        if folder is None:
            raise ValueError("That folder no longer exists.")
        new_name = validate_name(new_name)
        if self.name_taken(folder.parent_id, new_name, skip_folder=folder_id):
            raise ValueError(f"“{new_name}” already exists here.")
        self.conn.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name, folder_id))
        self._changed()

    def rename_file(self, file_id: int, new_name: str) -> None:
        rec = self.get_file(file_id)
        if rec is None:
            raise ValueError("That file no longer exists.")
        new_name = validate_name(new_name)
        if self.name_taken(rec.folder_id, new_name, skip_file=file_id):
            raise ValueError(f"“{new_name}” already exists here.")
        self.conn.execute("UPDATE files SET name = ? WHERE id = ?", (new_name, file_id))
        self._changed()

    def move_file(self, file_id: int, dest_folder: int | None) -> None:
        rec = self.get_file(file_id)
        if rec is None or rec.folder_id == dest_folder:
            return
        if self.name_taken(dest_folder, rec.name):
            raise ValueError(f"“{rec.name}” already exists in the destination folder.")
        self.conn.execute("UPDATE files SET folder_id = ? WHERE id = ?", (dest_folder, file_id))
        self._changed()

    def move_folder(self, folder_id: int, dest_folder: int | None) -> None:
        folder = self.get_folder(folder_id)
        if folder is None or folder.parent_id == dest_folder:
            return
        if dest_folder is not None and dest_folder in self.descendant_folder_ids(folder_id):
            raise ValueError("A folder cannot be moved into itself.")
        if self.name_taken(dest_folder, folder.name):
            raise ValueError(f"“{folder.name}” already exists in the destination folder.")
        self.conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (dest_folder, folder_id))
        self._changed()

    def create_file(
        self,
        folder_id: int | None,
        name: str,
        size: int,
        sha256: str,
        split_size: int,
        parts: list[tuple[int, int, str]],
        *,
        source_path: str | None = None,
        uid: str | None = None,
        status: str = STATUS_UPLOADING,
    ) -> FileRec:
        """parts: (byte_offset, size, sha256) for each piece."""
        name = validate_name(name)
        cur = self.conn.execute(
            """INSERT INTO files(uid, folder_id, name, size, sha256, split_size, status, source_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid or uuid.uuid4().hex, folder_id, name, size, sha256, split_size, status, source_path, time.time()),
        )
        file_id = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO parts(file_id, idx, byte_offset, size, sha256) VALUES (?, ?, ?, ?, ?)",
            [(file_id, i, off, sz, h) for i, (off, sz, h) in enumerate(parts)],
        )
        self._changed()
        return self.get_file(file_id)  # type: ignore[return-value]

    def set_part_message(self, file_id: int, idx: int, message_id: int) -> None:
        self.conn.execute(
            "UPDATE parts SET message_id = ? WHERE file_id = ? AND idx = ?", (message_id, file_id, idx)
        )
        self._changed()

    def set_status(self, file_id: int, status: str) -> None:
        if status == STATUS_COMPLETE:
            # The local path is only needed to resume an upload; don't keep it afterwards.
            self.conn.execute("UPDATE files SET status = ?, source_path = NULL WHERE id = ?", (status, file_id))
        else:
            self.conn.execute("UPDATE files SET status = ? WHERE id = ?", (status, file_id))
        self._changed()

    def delete_file(self, file_id: int) -> None:
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self._changed()

    def delete_folder(self, folder_id: int) -> None:
        # Files and sub-folders go with it through ON DELETE CASCADE.
        self.conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._changed()

    # ----------------------------------------------------------------- backup

    def backup_to(self, dest: Path | str) -> None:
        dest = Path(dest)
        if dest.exists():
            dest.unlink()
        target = sqlite3.connect(dest)
        try:
            self.conn.backup(target)
        finally:
            target.close()

    @staticmethod
    def validate_file(path: Path | str) -> None:
        """Raise ValueError if path is not an index database."""
        try:
            conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Not a valid index file: {exc}") from exc
        if not {"folders", "files", "parts"} <= tables:
            raise ValueError("Not a valid index file (tables are missing).")

    def replace_with(self, src: Path | str) -> Path:
        """Swap in another index file. The current one is kept as a .bak copy, whose path is returned."""
        self.validate_file(src)
        backup = self.path.with_name(f"{self.path.stem}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        self.backup_to(backup)
        self.conn.close()
        shutil.copyfile(src, self.path)
        self.conn = self._connect(self.path)
        self._changed()
        return backup
