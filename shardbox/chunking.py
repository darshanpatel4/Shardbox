"""Splitting files into Telegram-sized pieces, and hashing them."""

from __future__ import annotations

import hashlib
import threading
from typing import BinaryIO, Callable

# Telegram uploads/downloads files in 512 KiB parts.
PART_SIZE = 512 * 1024
# A single Telegram document may have at most this many parts.
MAX_PARTS_FREE = 4000  # = 2000 MiB
MAX_PARTS_PREMIUM = 8000  # = 4000 MiB

HASH_BLOCK = 4 * 1024 * 1024


class Cancelled(Exception):
    """Raised by blocking helpers when the job was cancelled."""


def max_split_bytes(premium: bool) -> int:
    return (MAX_PARTS_PREMIUM if premium else MAX_PARTS_FREE) * PART_SIZE


def plan_parts(size: int, split_size: int) -> list[tuple[int, int]]:
    """Return (offset, length) for each piece. An empty file has no pieces."""
    if split_size <= 0:
        raise ValueError("split_size must be positive")
    return [(off, min(split_size, size - off)) for off in range(0, size, split_size)]


def hash_file(
    path: str,
    plan: list[tuple[int, int]],
    progress: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[str, list[str]]:
    """SHA-256 of the whole file and of every piece, in a single read pass."""
    full = hashlib.sha256()
    piece_hashes: list[str] = []
    with open(path, "rb") as fh:
        for offset, length in plan:
            piece = hashlib.sha256()
            fh.seek(offset)
            remaining = length
            while remaining:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                data = fh.read(min(HASH_BLOCK, remaining))
                if not data:
                    raise OSError(f"{path} got shorter while it was being read")
                full.update(data)
                piece.update(data)
                remaining -= len(data)
                if progress:
                    progress(len(data))
            piece_hashes.append(piece.hexdigest())
    return full.hexdigest(), piece_hashes


def hash_range(path: str, offset: int, length: int, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        fh.seek(offset)
        remaining = length
        while remaining:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            data = fh.read(min(HASH_BLOCK, remaining))
            if not data:
                break
            h.update(data)
            remaining -= len(data)
    return h.hexdigest()


def read_at(fh: BinaryIO, offset: int, length: int) -> bytes:
    fh.seek(offset)
    data = fh.read(length)
    if len(data) != length:
        raise OSError("File got shorter while it was being uploaded")
    return data


def write_at(fh: BinaryIO, offset: int, data: bytes) -> None:
    fh.seek(offset)
    fh.write(data)
