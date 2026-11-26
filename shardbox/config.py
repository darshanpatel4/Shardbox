"""Where the app keeps its data, and the user's settings."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

MiB = 1024 * 1024


def _platform_dirs() -> tuple[Path, Path]:
    """(data folder, the folder used before the app was renamed from "Unlimited Storage")."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Shardbox", base / "UnlimitedStorage"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        return base / "Shardbox", base / "UnlimitedStorage"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "shardbox", base / "unlimited-storage"


_data_dir: Path | None = None


def app_data_dir() -> Path:
    """Per-user data folder. Set SHARDBOX_HOME to override (e.g. for a portable install)."""
    global _data_dir
    override = os.environ.get("SHARDBOX_HOME") or os.environ.get("USTORAGE_HOME")
    if override:
        path = Path(override)
    elif _data_dir is not None:
        return _data_dir
    else:
        path, old = _platform_dirs()
        if old.is_dir() and not path.exists():
            # Renamed app: move the old data (login, settings, index) so nothing is lost.
            try:
                old.rename(path)
            except OSError:
                path = old  # e.g. the old version is still running; keep using its folder
        _data_dir = path
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def session_path() -> Path:
    # Telethon adds the ".session" extension itself.
    return app_data_dir() / "account"


def index_path(channel_id: int) -> Path:
    # One index per storage channel: message ids only make sense inside their own channel.
    return app_data_dir() / f"index-{channel_id}.db"


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return app_data_dir() / "app.log"


@dataclass
class Settings:
    # Telegram API credentials from https://my.telegram.org (each user brings their own).
    api_id: int = 0
    api_hash: str = ""

    # The private channel that holds the files.
    channel_id: int = 0
    channel_title: str = ""

    # Files bigger than this are split into several Telegram documents.
    # Telegram allows 2000 MiB per file (4000 MiB with Premium).
    split_size_mb: int = 1900

    parallel_files: int = 2  # how many files transfer at the same time
    upload_workers: int = 4  # parallel connections per uploaded file
    download_workers: int = 4  # parallel connections per downloaded file

    download_dir: str = ""
    theme: str = "system"  # "system" (follow Windows), "light" or "dark"
    auto_backup_index: bool = True
    index_backup_msg_id: int = 0

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = settings_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_id and self.api_hash)
