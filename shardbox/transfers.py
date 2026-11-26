"""Upload/download queue.

Upload: hash the file (whole + every piece) → record it in the index → upload each piece
as its own Telegram document, saving the message id as soon as each piece is posted.
If the app stops halfway, uploading the same file again resumes from the next piece.

Download: fetch every piece into a temporary file, check each piece's SHA-256, then rename.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .captions import part_caption
from .chunking import Cancelled, hash_file, hash_range, plan_parts
from .config import MiB, Settings
from .db import STATUS_COMPLETE, FileRec, Index, sanitize_name, split_ext
from .tg import IO_POOL, TelegramService

log = logging.getLogger(__name__)

QUEUED = "Queued"
HASHING = "Checking file"
UPLOADING = "Uploading"
DOWNLOADING = "Downloading"
VERIFYING = "Verifying"
DONE = "Done"
FAILED = "Failed"
CANCELLED = "Cancelled"

UPLOAD = "upload"
DOWNLOAD = "download"

PARTIAL_SUFFIX = ".shardbox-partial"


class Job:
    _ids = itertools.count(1)

    def __init__(self, kind: str, name: str):
        self.id = next(Job._ids)
        self.kind = kind
        self.name = name
        self.state = QUEUED
        self.total = 0
        self.done = 0
        self.error = ""
        self.piece = 0  # piece being transferred (1-based), 0 = not started
        self.pieces = 0  # how many pieces the file has
        self.task: asyncio.Task | None = None
        self.cancel_event = threading.Event()
        self.keep_partial = False  # on cancel, keep uploaded pieces (used when quitting)
        self.started_at: float | None = None
        self.finished_at: float | None = None
        # upload
        self.local_path: str | None = None
        self.folder_id: int | None = None
        # upload + download
        self.file_id: int | None = None
        # download
        self.dest_dir: str | None = None
        self.use_cache = False  # download into the cache (for "Open") instead of a chosen folder
        self.result_path: Path | None = None

    @property
    def finished(self) -> bool:
        return self.state in (DONE, FAILED, CANCELLED)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.finished


def unique_local_path(path: Path) -> Path:
    if not path.exists() and not path.with_name(path.name + PARTIAL_SUFFIX).exists():
        return path
    stem, ext = split_ext(path.name)
    for n in itertools.count(1):
        candidate = path.with_name(f"{stem} ({n}){ext}")
        if not candidate.exists() and not candidate.with_name(candidate.name + PARTIAL_SUFFIX).exists():
            return candidate
    raise AssertionError("unreachable")


def scan_folder(root: Path) -> list[tuple[Path, list[str], list[str]]]:
    """(folder, its subfolders, its files) for every folder under root, sorted. Safe on any thread."""
    tree = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        tree.append((Path(dirpath), list(dirnames), sorted(filenames)))
    return tree


class TransferManager(QObject):
    job_added = Signal(object)
    job_finished = Signal(object)

    def __init__(self, tg: TelegramService, index: Index, settings: Settings, cache_dir: Path):
        super().__init__()
        self.tg = tg
        self.index = index
        self.settings = settings
        self.cache_dir = cache_dir
        self.jobs: list[Job] = []
        self._waiting: deque[Job] = deque()  # jobs not started yet, oldest first
        self._running = 0
        self._hold_pump = False

    # ------------------------------------------------------------ queue

    def _add(self, job: Job) -> Job:
        self.jobs.append(job)
        self._waiting.append(job)
        self.job_added.emit(job)
        if not self._hold_pump:
            self.pump()
        return job

    def pump(self) -> None:
        """Start waiting jobs until "files at once" are running. Cheap even with thousands queued."""
        limit = max(1, self.settings.parallel_files)
        while self._running < limit and self._waiting:
            job = self._waiting.popleft()
            if job.state != QUEUED or job.task is not None:
                continue  # cancelled while waiting
            self._running += 1
            job.task = asyncio.ensure_future(self._run(job))

    def add_upload(self, local_path: str, folder_id: int | None) -> Job:
        job = Job(UPLOAD, Path(local_path).name)
        job.local_path = local_path
        job.folder_id = folder_id
        return self._add(job)

    def add_folder_upload(self, local_dir: str, parent_id: int | None) -> int:
        """Recreate a local folder tree in the index and queue every file in it."""
        return self._queue_scanned(Path(local_dir), scan_folder(Path(local_dir)), parent_id)

    async def add_folder_upload_async(self, local_dir: str, parent_id: int | None) -> int:
        """Same, but reads the folder tree on a background thread so the window never freezes."""
        root = Path(local_dir)
        tree = await asyncio.get_running_loop().run_in_executor(IO_POOL, scan_folder, root)
        return self._queue_scanned(root, tree, parent_id)

    def _queue_scanned(self, root: Path, tree: list[tuple[Path, list[str], list[str]]], parent_id: int | None) -> int:
        count = 0
        self._hold_pump = True  # start uploads once at the end, not after every file
        try:
            with self.index.batch():
                top = self.index.create_folder(parent_id, sanitize_name(root.name), make_unique=True)
                folder_ids = {root: top.id}
                for here, dirnames, filenames in tree:
                    fid = folder_ids.get(here)
                    if fid is None:
                        continue
                    for d in dirnames:
                        folder_ids[here / d] = self.index.create_folder(fid, sanitize_name(d), make_unique=True).id
                    for f in filenames:
                        self.add_upload(str(here / f), fid)
                        count += 1
        finally:
            self._hold_pump = False
        self.pump()
        return count

    def add_download(self, rec: FileRec, dest_dir: str | None, *, use_cache: bool = False) -> Job:
        job = Job(DOWNLOAD, rec.name)
        job.file_id = rec.id
        job.dest_dir = dest_dir
        job.use_cache = use_cache
        job.total = rec.size
        return self._add(job)

    def cancel(self, job: Job) -> None:
        if job.finished:
            return
        job.cancel_event.set()
        if job.task is None:
            job.state = CANCELLED
            job.finished_at = time.time()
            self.job_finished.emit(job)
        else:
            job.task.cancel()

    def cancel_all(self) -> None:
        for job in list(self.jobs):
            self.cancel(job)

    def stop_all(self) -> None:
        """Stop everything (when quitting) but keep uploaded pieces so uploads can be resumed."""
        for job in list(self.jobs):
            job.keep_partial = True
            self.cancel(job)

    def retry(self, job: Job) -> Job | None:
        if not job.finished or job.state == DONE:
            return None
        if job.kind == UPLOAD and job.local_path:
            return self.add_upload(job.local_path, job.folder_id)
        if job.kind == DOWNLOAD and job.file_id is not None:
            rec = self.index.get_file(job.file_id)
            if rec:
                return self.add_download(rec, job.dest_dir, use_cache=job.use_cache)
        return None

    def clear_finished(self) -> None:
        self.jobs = [j for j in self.jobs if not j.finished]

    def has_active(self) -> bool:
        return any(not j.finished for j in self.jobs)

    def active_upload_file_ids(self) -> set[int]:
        return {j.file_id for j in self.jobs if j.kind == UPLOAD and not j.finished and j.file_id is not None}

    def active_file_ids(self) -> set[int]:
        return {j.file_id for j in self.jobs if not j.finished and j.file_id is not None}

    async def wait_idle(self, timeout: float) -> None:
        tasks = [j.task for j in self.jobs if j.task is not None and not j.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    # ------------------------------------------------------------ running

    async def _run(self, job: Job) -> None:
        job.started_at = time.time()
        try:
            if job.kind == UPLOAD:
                await self._upload(job)
            else:
                await self._download(job)
            job.state = DONE
        except (asyncio.CancelledError, Cancelled):
            job.state = CANCELLED
            if not job.keep_partial:
                await self._cleanup_cancelled(job)
        except Exception as exc:  # noqa: BLE001 - every failure is shown to the user
            log.exception("%s of %s failed", job.kind, job.name)
            job.state = FAILED
            if isinstance(exc, FileNotFoundError):
                job.error = f"File not found: {exc.filename}"
            elif isinstance(exc, PermissionError):
                job.error = f"No permission to access {exc.filename}"
            else:
                job.error = str(exc) or type(exc).__name__
        finally:
            job.finished_at = time.time()
            self._running -= 1
            self.job_finished.emit(job)
            self.pump()

    async def _cleanup_cancelled(self, job: Job) -> None:
        try:
            if job.kind == UPLOAD and job.file_id is not None:
                rec = self.index.get_file(job.file_id)
                if rec and rec.status != STATUS_COMPLETE:
                    ids = self.index.message_ids([rec.id])
                    if ids:
                        await self.tg.delete_messages(ids)
                    self.index.delete_file(rec.id)
            elif job.kind == DOWNLOAD and job.result_path is not None:
                partial = job.result_path.with_name(job.result_path.name + PARTIAL_SUFFIX)
                partial.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            log.exception("cleanup after cancelling %s failed", job.name)

    async def _upload(self, job: Job) -> None:
        assert job.local_path is not None
        path = Path(job.local_path)
        stat = path.stat()
        size = stat.st_size
        split = min(max(1, self.settings.split_size_mb) * MiB, self.tg.max_file_bytes)
        plan = plan_parts(size, split)
        loop = asyncio.get_running_loop()

        job.state = HASHING
        job.total = size
        job.done = 0
        job.pieces = len(plan)

        def on_hashed(n: int) -> None:
            job.done += n

        full_sha, piece_shas = await loop.run_in_executor(
            IO_POOL, hash_file, str(path), plan, on_hashed, job.cancel_event
        )
        after = path.stat()
        if after.st_size != size or after.st_mtime_ns != stat.st_mtime_ns:
            raise RuntimeError("The file changed while it was being read. Try again once it stops changing.")

        index = self.index
        rec = index.find_resumable(job.folder_id, str(path), size, full_sha, split)
        if rec is not None and [p.sha256 for p in index.parts(rec.id)] != piece_shas:
            rec = None
        if rec is None:
            name = index.unique_name(job.folder_id, sanitize_name(path.name))
            rec = index.create_file(
                job.folder_id,
                name,
                size,
                full_sha,
                split,
                [(off, length, h) for (off, length), h in zip(plan, piece_shas)],
                source_path=str(path),
            )
        job.file_id = rec.id
        job.name = rec.name

        parts = index.parts(rec.id)
        folder_path = index.folder_path(rec.folder_id)
        job.state = UPLOADING
        job.done = sum(p.size for p in parts if p.message_id)

        def on_sent(n: int) -> None:
            job.done += n

        job.pieces = len(parts)
        for part in parts:
            if part.message_id:
                continue
            job.piece = part.idx + 1
            piece_name = rec.name if len(parts) == 1 else f"{rec.name}.part{part.idx + 1:03d}"
            caption = part_caption(
                uid=rec.uid,
                name=rec.name,
                path=folder_path,
                size=rec.size,
                sha256=rec.sha256,
                index=part.idx,
                count=len(parts),
                offset=part.byte_offset,
                part_size=part.size,
                part_sha256=part.sha256,
                split_size=rec.split_size,
            )
            before = job.done
            try:
                message_id = await self.tg.upload_range(
                    str(path), part.byte_offset, part.size, piece_name, caption,
                    self.settings.upload_workers, on_sent,
                )
            except BaseException:
                job.done = before
                raise
            index.set_part_message(rec.id, part.idx, message_id)

        index.set_status(rec.id, STATUS_COMPLETE)

    async def _download(self, job: Job) -> None:
        assert job.file_id is not None
        rec = self.index.get_file(job.file_id)
        if rec is None:
            raise RuntimeError("This file is no longer in the index.")
        if not rec.complete:
            raise RuntimeError("This file hasn't finished uploading.")
        parts = self.index.parts(rec.id)
        loop = asyncio.get_running_loop()

        if job.use_cache:
            dest_dir = self.cache_dir / rec.uid
            final = dest_dir / rec.name
            if final.exists() and final.stat().st_size == rec.size:
                job.done = rec.size
                job.result_path = final
                return
        else:
            dest_dir = Path(job.dest_dir or ".")
            final = None
        dest_dir.mkdir(parents=True, exist_ok=True)
        if final is None:
            final = unique_local_path(dest_dir / rec.name)
        job.result_path = final
        partial = final.with_name(final.name + PARTIAL_SUFFIX)

        job.total = rec.size
        job.done = 0
        job.state = DOWNLOADING
        with open(partial, "wb") as fh:
            fh.truncate(rec.size)

        def on_received(n: int) -> None:
            job.done += n

        job.pieces = len(parts)
        try:
            for part in parts:
                job.piece = part.idx + 1
                if part.message_id is None:
                    raise RuntimeError(f"Piece {part.idx + 1} was never uploaded.")
                await self.tg.download_range(
                    part.message_id, str(partial), part.byte_offset, part.size,
                    self.settings.download_workers, on_received,
                )
                job.state = VERIFYING
                digest = await loop.run_in_executor(
                    IO_POOL, hash_range, str(partial), part.byte_offset, part.size, job.cancel_event
                )
                if digest != part.sha256:
                    raise RuntimeError(f"Piece {part.idx + 1} failed the checksum check. The download is corrupt.")
                job.state = DOWNLOADING
            os.replace(partial, final)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
