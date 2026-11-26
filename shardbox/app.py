"""Startup: API keys → Telegram login → storage channel → main window."""

from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__
from .config import Settings, cache_dir, index_path, log_path, session_path

log = logging.getLogger("shardbox")


def _setup_logging() -> None:
    handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:  # no console in a windowed .exe
        root.addHandler(logging.StreamHandler())
    logging.getLogger("telethon").setLevel(logging.WARNING)

    def excepthook(exc_type, exc, tb):
        log.critical("unhandled error", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook


async def _start(close_event: asyncio.Event) -> None:
    from telethon import errors

    from .db import Index
    from .tg import TelegramService
    from PySide6.QtWidgets import QMessageBox

    from .ui.common import ask, run_dialog, show_error
    from .ui.main_window import MainWindow
    from .ui.setup_dialogs import ApiCredentialsDialog, ChannelDialog, LoginDialog

    async def fatal(text: str) -> None:
        await run_dialog(QMessageBox(QMessageBox.Icon.Critical, APP_NAME, text))

    settings = Settings.load()

    # 1. API credentials (each user creates their own at my.telegram.org)
    if not settings.has_credentials:
        dialog = ApiCredentialsDialog()
        if not await run_dialog(dialog):
            return
        settings.api_id, settings.api_hash = dialog.values()
        settings.save()

    # 2. Connect and log in
    tg = TelegramService(settings.api_id, settings.api_hash, session_path())
    try:
        await tg.connect()
        authorized = await tg.is_authorized()
    except (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError):
        settings.api_id, settings.api_hash = 0, ""
        settings.save()
        await fatal("Telegram rejected the API ID / hash. Start the app again and re-enter them.")
        return
    except OSError as exc:
        await fatal(f"Couldn't connect to Telegram. Check your internet connection.\n\n{exc}")
        return

    try:
        if not authorized:
            login = LoginDialog(tg)
            if not await run_dialog(login):
                if login.api_keys_rejected:
                    settings.api_id, settings.api_hash = 0, ""
                    settings.save()
                    await fatal("Telegram rejected your API ID / hash. Start the app again and re-enter them.")
                return

        # 3. Storage channel
        opened = False
        if settings.channel_id:
            try:
                settings.channel_title = await tg.open_channel(settings.channel_id)
                opened = True
            except (ValueError, errors.RPCError):
                log.warning("saved channel %s is not available", settings.channel_id, exc_info=True)
                show_error(
                    None, APP_NAME,
                    "The storage channel saved in your settings can't be opened with this account. "
                    "Choose or create a channel.",
                )
        if not opened:
            dialog = ChannelDialog(tg)
            if not await run_dialog(dialog):
                return
            settings.channel_id = dialog.channel_id
            settings.index_backup_msg_id = 0
            settings.channel_title = await tg.open_channel(dialog.channel_id)
        settings.save()

        # 4. Index (offer to restore it from the channel on a new computer)
        index = Index(index_path(settings.channel_id))
        window = MainWindow(tg, index, settings, close_event.set)
        window.show()
        if index.is_empty():
            backups = await tg.find_index_backups()
            if backups and await ask(
                window, "Restore index?",
                "This computer has no files in its index yet, but your channel has an index backup. "
                "Restore it now to see your files?",
            ):
                await window.backup.restore_latest()
                window.navigate(None)

        await close_event.wait()
        index.close()
    finally:
        await tg.disconnect()


def _set_windows_app_id() -> None:
    """Make Windows group the app under its own taskbar icon (not python.exe's)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Shardbox.Shardbox")
    except (AttributeError, OSError):
        pass


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"{APP_NAME} needs Python 3.11 or newer.")

    _setup_logging()
    log.info("%s %s starting", APP_NAME, __version__)

    import qasync
    from PySide6.QtWidgets import QApplication

    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    from .ui.theme import app_icon, apply_theme

    apply_theme(app, Settings.load().theme)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)  # closing is handled by the main window
    cache_dir()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    close_event = asyncio.Event()
    app.aboutToQuit.connect(close_event.set)

    with loop:
        loop.run_until_complete(_start(close_event))
    log.info("bye")


if __name__ == "__main__":
    main()
