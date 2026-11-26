"""Small helpers shared by the windows and dialogs.

The app runs Qt and asyncio on one thread (via qasync). Blocking dialogs (``exec()``)
must never be started from inside a coroutine, so coroutines use ``run_dialog`` and
``ask`` below, which show the dialog and wait for it without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()
_open_boxes: set[QMessageBox] = set()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def short_date(ts: float) -> str:
    """'Nov 25' this year, 'Nov 25, 2024' otherwise."""
    t = time.localtime(ts)
    text = f"{time.strftime('%b', t)} {t.tm_mday}"
    return text if t.tm_year == time.localtime().tm_year else f"{text}, {t.tm_year}"


def long_date(ts: float) -> str:
    t = time.localtime(ts)
    return f"{time.strftime('%b', t)} {t.tm_mday}, {t.tm_year} · {time.strftime('%H:%M', t)}"


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{max(1, seconds)} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def show_message(parent: QWidget | None, title: str, text: str, icon=QMessageBox.Icon.Information) -> None:
    """Non-blocking message box, safe to call from anywhere."""
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    _open_boxes.add(box)
    box.finished.connect(lambda _r: _open_boxes.discard(box))
    box.open()


def show_error(parent: QWidget | None, title: str, text: str) -> None:
    show_message(parent, title, text, QMessageBox.Icon.Critical)


def spawn(
    coro: Coroutine[Any, Any, Any],
    *,
    parent: QWidget | None = None,
    error_title: str = "Something went wrong",
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Task:
    """Start a coroutine from a Qt slot; any error is shown to the user."""
    task = asyncio.ensure_future(coro)
    _tasks.add(task)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        log.error("background task failed", exc_info=exc)
        if on_error is not None:
            on_error(exc)
        else:
            show_error(parent, error_title, str(exc) or type(exc).__name__)

    task.add_done_callback(done)
    return task


async def run_dialog(dialog: QDialog) -> bool:
    """Show a dialog and wait for it to close. Returns True if it was accepted."""
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    def finished(result: int) -> None:
        if not future.done():
            future.set_result(result)

    dialog.finished.connect(finished)
    dialog.open()
    return await future == 1  # QDialog.Accepted


async def ask(parent: QWidget | None, title: str, text: str) -> bool:
    box = QMessageBox(
        QMessageBox.Icon.Question,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    await run_dialog(box)
    return box.clickedButton() is box.button(QMessageBox.StandardButton.Yes)
