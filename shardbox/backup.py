"""Keeping the index safe.

1. Backup: a copy of index.db is uploaded to the channel shortly after every change
   (the previous backup message is deleted, so only the newest one stays).
2. Restore: download the newest backup from the channel, e.g. on a new computer.
3. Rebuild: if every backup is gone, scan the captions of the stored pieces and
   re-create the entries (files come back under the name and folder they were uploaded with).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .captions import index_caption
from .config import Settings
from .db import STATUS_COMPLETE, Index, sanitize_name
from .tg import TelegramService
from .transfers import TransferManager

log = logging.getLogger(__name__)

BACKUP_DELAY_MS = 20_000


class IndexBackup(QObject):
    status_changed = Signal(str)

    def __init__(
        self, tg: TelegramService, index: Index, settings: Settings, manager: TransferManager, work_dir: Path
    ):
        super().__init__()
        self.tg = tg
        self.index = index
        self.settings = settings
        self.manager = manager
        self.work_dir = work_dir
        self.dirty = False
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(BACKUP_DELAY_MS)
        self._timer.timeout.connect(self._on_timer)
        index.add_listener(self._on_index_changed)

    def _on_index_changed(self) -> None:
        self.dirty = True
        if self.settings.auto_backup_index:
            self._timer.start()

    def _on_timer(self) -> None:
        # Wait until transfers finish so we don't back up after every single piece.
        if self.manager.has_active() or self._running:
            self._timer.start()
            return
        asyncio.ensure_future(self._backup_quietly())

    async def _backup_quietly(self) -> None:
        try:
            await self.backup_now()
        except Exception:  # noqa: BLE001 - shown in the status bar instead
            log.exception("automatic index backup failed")

    async def backup_now(self) -> None:
        if self._running:
            return
        self._running = True
        self.status_changed.emit("Backing up index…")
        try:
            snapshot = self.work_dir / "index-backup.db"
            self.index.backup_to(snapshot)
            self.dirty = False
            count, total = self.index.stats()
            name = f"shardbox-index-{time.strftime('%Y%m%d-%H%M%S')}.db"
            message_id = await self.tg.upload_range(
                str(snapshot), 0, snapshot.stat().st_size, name, index_caption(count, total), 2, lambda n: None
            )
            old = self.settings.index_backup_msg_id
            self.settings.index_backup_msg_id = message_id
            self.settings.save()
            if old and old != message_id:
                try:
                    await self.tg.delete_messages([old])
                except Exception:  # noqa: BLE001 - an old backup left behind is harmless
                    log.warning("could not delete the previous index backup", exc_info=True)
            snapshot.unlink(missing_ok=True)
            self.status_changed.emit(f"Index backed up at {time.strftime('%H:%M')}")
        except Exception as exc:
            self.dirty = True
            self.status_changed.emit(f"Index backup failed: {exc}")
            raise
        finally:
            self._running = False

    async def restore_latest(self, full_scan: bool = False) -> Path | None:
        """Replace the local index with the newest backup in the channel.

        Returns the path of the safety copy of the old index, or None if no backup exists.
        """
        backups = await self.tg.find_index_backups(full_scan=full_scan)
        if not backups:
            return None
        latest = backups[0]
        download = self.work_dir / "index-restore.db"
        download.unlink(missing_ok=True)
        await self.tg.download_message_to(latest, download)
        safety_copy = self.index.replace_with(download)
        download.unlink(missing_ok=True)
        self.settings.index_backup_msg_id = latest.id
        self.settings.save()
        self.dirty = False
        self._timer.stop()
        return safety_copy

    async def rebuild_from_channel(self, progress: Callable[[int], None]) -> tuple[int, int]:
        """Add every complete file found in the channel that the index doesn't know about.

        Returns (files added, files skipped because some pieces are missing).
        """
        found = await self.tg.scan_part_captions(progress)
        groups: dict[str, dict[int, tuple[int, dict]]] = defaultdict(dict)
        for message_id, meta in found:
            pieces = groups[meta["uid"]]
            # If a piece was posted twice (e.g. the app crashed right after sending), keep the newest.
            if meta["i"] not in pieces or pieces[meta["i"]][0] < message_id:
                pieces[meta["i"]] = (message_id, meta)

        added = incomplete = 0
        with self.index.batch():
            for uid, pieces in groups.items():
                if self.index.has_uid(uid):
                    continue
                first = pieces[min(pieces)][1]
                if set(pieces) != set(range(first["n"])):
                    incomplete += 1
                    continue
                folder_id = self.index.ensure_path(first["path"])
                name = self.index.unique_name(folder_id, sanitize_name(first["name"]))
                ordered = [pieces[i] for i in range(first["n"])]
                rec = self.index.create_file(
                    folder_id,
                    name,
                    first["size"],
                    first["sha"],
                    first["ss"],
                    [(m["off"], m["ps"], m["psha"]) for _, m in ordered],
                    uid=uid,
                )
                for i, (message_id, _) in enumerate(ordered):
                    self.index.set_part_message(rec.id, i, message_id)
                self.index.set_status(rec.id, STATUS_COMPLETE)
                added += 1
        return added, incomplete
