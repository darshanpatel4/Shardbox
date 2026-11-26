"""Metadata written into each Telegram message's caption.

Every uploaded piece carries enough information (file id, name, folder, size, hashes,
piece number) that the whole index can be rebuilt by scanning the channel, even if
the local index and its backups are lost.
"""

from __future__ import annotations

import json
import re

# These tags are part of the stored data format, not branding: messages already in people's
# channels carry them, so they must never change (the app was first called "Unlimited Storage").
FILE_TAG = "#ustorage"
INDEX_TAG = "#ustorage_index"
CAPTION_LIMIT = 1000  # Telegram allows 1024 UTF-16 code units for free accounts

_PART_RE = re.compile(r"#ustorage (\{.*\})\s*$", re.S)
_REQUIRED = {"v", "uid", "name", "path", "size", "sha", "i", "n", "off", "ps", "psha", "ss"}


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _compose(head: str, meta: dict) -> str:
    return f"{head}\n{FILE_TAG} {json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}"


def _shorten(name: str, keep: int) -> str:
    if len(name) <= keep:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 10:
        return stem[: max(1, keep - len(ext) - 2)] + "…." + ext
    return name[: keep - 1] + "…"


def part_caption(
    *,
    uid: str,
    name: str,
    path: str,
    size: int,
    sha256: str,
    index: int,
    count: int,
    offset: int,
    part_size: int,
    part_sha256: str,
    split_size: int,
) -> str:
    meta = {
        "v": 1,
        "uid": uid,
        "name": name,
        "path": path,
        "size": size,
        "sha": sha256,
        "i": index,
        "n": count,
        "off": offset,
        "ps": part_size,
        "psha": part_sha256,
        "ss": split_size,
    }
    suffix = f" — part {index + 1}/{count}" if count > 1 else ""
    head = f"📄 {_shorten(name, 60)}{suffix}"
    text = _compose(head, meta)
    if _utf16_len(text) > CAPTION_LIMIT:
        meta["path"] = _shorten(path, 120)
        text = _compose(head, meta)
    if _utf16_len(text) > CAPTION_LIMIT:
        meta["path"] = "/"
        meta["name"] = _shorten(name, 150)
        text = _compose(head, meta)
    return text


def parse_part_caption(text: str | None) -> dict | None:
    if not text:
        return None
    match = _PART_RE.search(text)
    if not match:
        return None
    try:
        meta = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(meta, dict) or not _REQUIRED <= meta.keys() or meta.get("v") != 1:
        return None
    return meta


def index_caption(file_count: int, total_size: int) -> str:
    meta = json.dumps({"v": 1, "files": file_count, "bytes": total_size}, separators=(",", ":"))
    return f"🗂 Shardbox index backup\n{INDEX_TAG} {meta}"


def is_index_caption(text: str | None) -> bool:
    return bool(text) and INDEX_TAG in text
