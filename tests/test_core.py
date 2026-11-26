"""Tests that run without a Telegram account.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never touch the real data folder (login, settings, index) while testing.
os.environ["SHARDBOX_HOME"] = tempfile.mkdtemp(prefix="shardbox-test-")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from shardbox.backup import IndexBackup  # noqa: E402
from shardbox.captions import CAPTION_LIMIT, index_caption, parse_part_caption, part_caption  # noqa: E402
from shardbox.chunking import PART_SIZE, hash_file, max_split_bytes, plan_parts  # noqa: E402
from shardbox.config import MiB, Settings  # noqa: E402
from shardbox.db import STATUS_COMPLETE, Index, sanitize_name, validate_name  # noqa: E402
from shardbox.transfers import DONE, FAILED, TransferManager  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


class FakeTelegram:
    """Stores 'messages' in memory instead of a Telegram channel."""

    premium = False
    max_file_bytes = max_split_bytes(False)

    def __init__(self) -> None:
        self.messages: dict[int, tuple[bytes, str]] = {}
        self.next_id = 100
        self.uploads = 0
        self.fail_upload_numbers: set[int] = set()

    async def upload_range(self, path, offset, size, file_name, caption, workers, progress) -> int:
        self.uploads += 1
        if self.uploads in self.fail_upload_numbers:
            raise ConnectionError("simulated network failure")
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(size)
        await asyncio.sleep(0)
        progress(size)
        self.next_id += 1
        self.messages[self.next_id] = (data, caption)
        return self.next_id

    async def download_range(self, message_id, dest_path, dest_offset, size, workers, progress) -> None:
        data, _ = self.messages[message_id]
        with open(dest_path, "r+b") as fh:
            fh.seek(dest_offset)
            fh.write(data)
        progress(len(data))

    async def delete_messages(self, ids) -> None:
        for i in ids:
            self.messages.pop(i, None)

    async def scan_part_captions(self, progress):
        found = []
        for message_id, (data, caption) in self.messages.items():
            meta = parse_part_caption(caption)
            if meta and len(data) == meta["ps"]:
                found.append((message_id, meta))
        progress(len(self.messages))
        return found


class ChunkingTests(unittest.TestCase):
    def test_plan_parts(self):
        self.assertEqual(plan_parts(0, 10), [])
        self.assertEqual(plan_parts(10, 10), [(0, 10)])
        self.assertEqual(plan_parts(25, 10), [(0, 10), (10, 10), (20, 5)])

    def test_free_limit_is_2000_mib(self):
        self.assertEqual(max_split_bytes(False), 2000 * MiB)
        self.assertEqual(max_split_bytes(True), 4000 * MiB)
        self.assertEqual(PART_SIZE, 512 * 1024)

    def test_hash_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.bin"
            path.write_bytes(b"abc" * 1000)
            full, pieces = hash_file(str(path), plan_parts(3000, 1000))
            import hashlib

            self.assertEqual(full, hashlib.sha256(b"abc" * 1000).hexdigest())
            self.assertEqual(len(pieces), 3)
            self.assertEqual(pieces[0], hashlib.sha256((b"abc" * 1000)[:1000]).hexdigest())


class CaptionTests(unittest.TestCase):
    def _caption(self, name="movie.mkv", path="/Videos"):
        return part_caption(
            uid="a" * 32, name=name, path=path, size=5_000_000_000, sha256="f" * 64, index=1, count=3,
            offset=1900 * MiB, part_size=1900 * MiB, part_sha256="e" * 64, split_size=1900 * MiB,
        )

    def test_round_trip(self):
        meta = parse_part_caption(self._caption())
        self.assertEqual(meta["name"], "movie.mkv")
        self.assertEqual(meta["path"], "/Videos")
        self.assertEqual(meta["i"], 1)
        self.assertEqual(meta["n"], 3)

    def test_long_names_fit(self):
        text = self._caption(name="x" * 250 + ".mkv", path="/" + "/".join(["folder" * 10] * 10))
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, CAPTION_LIMIT)
        self.assertIsNotNone(parse_part_caption(text))

    def test_index_caption_is_not_a_part(self):
        self.assertIsNone(parse_part_caption(index_caption(3, 100)))
        self.assertIsNone(parse_part_caption("hello"))
        self.assertIsNone(parse_part_caption(None))


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.index = Index(Path(self.tmp.name) / "index.db")

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def test_folders_and_names(self):
        a = self.index.create_folder(None, "Photos")
        with self.assertRaises(ValueError):
            self.index.create_folder(None, "photos")  # case-insensitive clash
        self.assertEqual(self.index.unique_name(None, "Photos"), "Photos (1)")
        b = self.index.create_folder(a.id, "2024")
        self.assertEqual(self.index.folder_path(b.id), "/Photos/2024")
        with self.assertRaises(ValueError):
            self.index.move_folder(a.id, b.id)  # into its own child
        self.assertEqual(self.index.ensure_path("/Photos/2024/June"), self.index.children(b.id)[0][0].id)

    def test_name_validation(self):
        with self.assertRaises(ValueError):
            validate_name("a/b")
        with self.assertRaises(ValueError):
            validate_name("  ")
        self.assertEqual(sanitize_name('what?.txt'), "what_.txt")

    def test_delete_folder_cascades(self):
        folder = self.index.create_folder(None, "Docs")
        rec = self.index.create_file(folder.id, "a.txt", 3, "x", 10, [(0, 3, "y")])
        self.index.set_part_message(rec.id, 0, 55)
        self.assertEqual(self.index.message_ids([r.id for r in self.index.files_in_subtree(folder.id)]), [55])
        self.index.delete_folder(folder.id)
        self.assertIsNone(self.index.get_file(rec.id))
        self.assertEqual(self.index.parts(rec.id), [])

    def test_backup_and_replace(self):
        self.index.create_folder(None, "Keep")
        copy = Path(self.tmp.name) / "copy.db"
        self.index.backup_to(copy)
        self.index.create_folder(None, "Later")
        safety = self.index.replace_with(copy)
        self.assertTrue(safety.exists())
        self.assertEqual([f.name for f in self.index.children(None)[0]], ["Keep"])


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.index = Index(self.root / "index.db")
        self.tg = FakeTelegram()
        self.settings = Settings(split_size_mb=1, parallel_files=2)
        self.manager = TransferManager(self.tg, self.index, self.settings, self.root / "cache")
        self.source = self.root / "big.bin"
        self.data = os.urandom(int(2.5 * MiB))
        self.source.write_bytes(self.data)

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def run_job(self, make_job):
        async def go():
            job = make_job()
            await job.task
            return job

        return asyncio.run(go())

    def test_split_upload_and_download(self):
        job = self.run_job(lambda: self.manager.add_upload(str(self.source), None))
        self.assertEqual(job.state, DONE, job.error)
        rec = self.index.get_file(job.file_id)
        self.assertEqual(rec.status, STATUS_COMPLETE)
        self.assertIsNone(rec.source_path)
        self.assertEqual(len(self.index.parts(rec.id)), 3)  # 1 + 1 + 0.5 MiB

        out = self.root / "out"
        job = self.run_job(lambda: self.manager.add_download(rec, str(out)))
        self.assertEqual(job.state, DONE, job.error)
        self.assertEqual((out / "big.bin").read_bytes(), self.data)

        # A second download of the same file must not overwrite the first.
        job = self.run_job(lambda: self.manager.add_download(rec, str(out)))
        self.assertEqual(job.result_path.name, "big (1).bin")

    def test_resume_after_failure(self):
        self.tg.fail_upload_numbers = {2}
        job = self.run_job(lambda: self.manager.add_upload(str(self.source), None))
        self.assertEqual(job.state, FAILED)
        rec = self.index.get_file(job.file_id)
        self.assertNotEqual(rec.status, STATUS_COMPLETE)
        self.assertEqual([p.message_id is not None for p in self.index.parts(rec.id)], [True, False, False])

        job = self.run_job(lambda: self.manager.retry(job))
        self.assertEqual(job.state, DONE, job.error)
        self.assertEqual(job.file_id, rec.id)  # same entry, resumed
        self.assertEqual(self.tg.uploads, 4)  # 1 ok + 1 failed + 2 remaining
        self.assertEqual(len(self.index.children(None)[1]), 1)

    def test_corruption_is_detected(self):
        job = self.run_job(lambda: self.manager.add_upload(str(self.source), None))
        rec = self.index.get_file(job.file_id)
        last = self.index.parts(rec.id)[-1].message_id
        data, caption = self.tg.messages[last]
        self.tg.messages[last] = (b"\0" * len(data), caption)
        out = self.root / "out"
        job = self.run_job(lambda: self.manager.add_download(rec, str(out)))
        self.assertEqual(job.state, FAILED)
        self.assertIn("checksum", job.error)
        self.assertEqual(list(out.iterdir()), [])  # no partial file left behind

    def test_empty_file(self):
        empty = self.root / "empty.txt"
        empty.write_bytes(b"")
        job = self.run_job(lambda: self.manager.add_upload(str(empty), None))
        self.assertEqual(job.state, DONE, job.error)
        rec = self.index.get_file(job.file_id)
        job = self.run_job(lambda: self.manager.add_download(rec, str(self.root / "out")))
        self.assertEqual(job.state, DONE, job.error)
        self.assertEqual((self.root / "out" / "empty.txt").read_bytes(), b"")

    def test_rebuild_index_from_captions(self):
        folder = self.index.create_folder(None, "Backups")
        job = self.run_job(lambda: self.manager.add_upload(str(self.source), folder.id))
        uid = self.index.get_file(job.file_id).uid

        fresh = Index(self.root / "fresh.db")
        try:
            backup = IndexBackup(self.tg, fresh, self.settings, self.manager, self.root)
            added, incomplete = asyncio.run(backup.rebuild_from_channel(lambda n: None))
            self.assertEqual((added, incomplete), (1, 0))
            folders, _ = fresh.children(None)
            self.assertEqual([f.name for f in folders], ["Backups"])
            rec = fresh.children(folders[0].id)[1][0]
            self.assertEqual((rec.uid, rec.name, rec.size), (uid, "big.bin", len(self.data)))

            manager = TransferManager(self.tg, fresh, self.settings, self.root / "cache")

            async def go():
                j = manager.add_download(rec, str(self.root / "rebuilt"))
                await j.task
                return j

            j = asyncio.run(go())
            self.assertEqual(j.state, DONE, j.error)
            self.assertEqual((self.root / "rebuilt" / "big.bin").read_bytes(), self.data)
        finally:
            fresh.close()



class DataFolderTests(unittest.TestCase):
    def test_old_unlimited_storage_folder_is_moved(self):
        from unittest import mock

        from shardbox import config

        with tempfile.TemporaryDirectory() as tmp:
            new, old = Path(tmp) / "Shardbox", Path(tmp) / "UnlimitedStorage"
            old.mkdir()
            (old / "account.session").write_text("login")
            (old / "settings.json").write_text("{}")
            env = {k: v for k, v in os.environ.items() if k not in ("SHARDBOX_HOME", "USTORAGE_HOME")}
            with mock.patch.dict(os.environ, env, clear=True),                     mock.patch.object(config, "_platform_dirs", return_value=(new, old)),                     mock.patch.object(config, "_data_dir", None):
                self.assertEqual(config.app_data_dir(), new)
            self.assertFalse(old.exists())
            self.assertEqual((new / "account.session").read_text(), "login")

    def test_existing_new_folder_is_kept(self):
        from unittest import mock

        from shardbox import config

        with tempfile.TemporaryDirectory() as tmp:
            new, old = Path(tmp) / "Shardbox", Path(tmp) / "UnlimitedStorage"
            new.mkdir()
            old.mkdir()
            env = {k: v for k, v in os.environ.items() if k not in ("SHARDBOX_HOME", "USTORAGE_HOME")}
            with mock.patch.dict(os.environ, env, clear=True),                     mock.patch.object(config, "_platform_dirs", return_value=(new, old)),                     mock.patch.object(config, "_data_dir", None):
                self.assertEqual(config.app_data_dir(), new)
            self.assertTrue(old.exists())  # never merged or deleted


if __name__ == "__main__":
    unittest.main()
