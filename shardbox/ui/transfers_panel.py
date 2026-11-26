"""The floating "Uploading 2 items · downloading 1" card in the corner of the file list."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..transfers import (
    CANCELLED,
    DONE,
    DOWNLOADING,
    FAILED,
    HASHING,
    QUEUED,
    UPLOAD,
    UPLOADING,
    VERIFYING,
    Job,
    TransferManager,
)
from . import theme
from .common import human_duration, human_size

CARD_WIDTH = 380
MARGIN = 16
MAX_BODY_HEIGHT = 300
MAX_WAITING_ROWS = 5  # rows for jobs that haven't started yet
MAX_FAILED_ROWS = 20
MAX_DONE_ROWS = 10


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "…" when there's no room."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class TransferRow(QWidget):
    def __init__(self, job: Job, card: "TransfersCard"):
        super().__init__()
        self.job = job
        self.card = card
        self._state = ""
        self._action = ""
        self.setObjectName("TransferRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        arrow = self.arrow = QLabel()
        self._paint_arrow()
        arrow.setToolTip("Upload" if job.kind == UPLOAD else "Download")
        self.name = ElidedLabel(job.name)
        self.name.setObjectName("TransferName")
        self.pct = QLabel()
        self.pct.setObjectName("TransferPct")

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)

        self.meta = ElidedLabel()
        self.meta.setObjectName("TransferMeta")
        self.action = QToolButton()
        self.action.setObjectName("RowAction")
        self.action.setIconSize(QSize(14, 14))
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._on_action)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(arrow)
        top.addWidget(self.name, 1)
        top.addWidget(self.pct)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.meta, 1)
        bottom.addWidget(self.action)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        layout.addLayout(bottom)

    def _paint_arrow(self) -> None:
        name = "arrow_up" if self.job.kind == UPLOAD else "arrow_down"
        self.arrow.setPixmap(theme.icon_pixmap(name, theme.MUTED, 14, 2.2))

    def retheme(self) -> None:
        self._paint_arrow()
        self._action = ""  # forces the action icon to be redrawn on the next update

    # ------------------------------------------------------------ updating

    def update_from_job(self, speed: float) -> None:
        job = self.job
        self.name.setText(job.name)

        if job.state == DONE:
            fraction = 1.0
        elif job.state in (UPLOADING, DOWNLOADING, VERIFYING) and job.total:
            fraction = job.done / job.total
        else:
            fraction = 0.0
        self.bar.setValue(int(fraction * 1000))
        self.pct.setText(f"{int(fraction * 100)}%" if job.state not in (QUEUED, HASHING, FAILED, CANCELLED) else "")
        self.meta.setText(self._detail(speed))

        state = "failed" if job.state == FAILED else "done" if job.state == DONE else ""
        if state != self._state:
            self._state = state
            for widget in (self.bar, self.meta):
                widget.setProperty("state", state)
                theme.restyle(widget)

        if not job.finished:
            action = "cancel"
        elif job.state in (FAILED, CANCELLED):
            action = "retry"
        elif job.kind != UPLOAD and not job.use_cache and job.result_path is not None:
            action = "show"
        else:
            action = ""
        if action != self._action:
            self._action = action
            self.action.setVisible(bool(action))
            if action == "cancel":
                self.action.setIcon(theme.icon("close", theme.MUTED, 14, 2.2))
                self.action.setToolTip("Cancel")
            elif action == "retry":
                self.action.setIcon(theme.icon("retry", theme.MUTED, 14, 2.2))
                self.action.setToolTip("Try again")
            elif action == "show":
                self.action.setIcon(theme.icon("folder_open", theme.MUTED, 14, 2.0))
                self.action.setToolTip("Show in folder")

    def _detail(self, speed: float) -> str:
        job = self.job
        pieces = job.pieces > 1 and job.piece > 0
        if job.state == QUEUED:
            return "Waiting to start"
        if job.state == HASHING:
            pct = int(job.done / job.total * 100) if job.total else 0
            return f"Checking file · {pct}%"
        if job.state in (UPLOADING, DOWNLOADING):
            parts = []
            if pieces:
                parts.append(f"Piece {job.piece} of {job.pieces}")
            if speed > 0:
                parts.append(f"{human_size(speed)}/s")
                parts.append(f"{human_duration(max(0, job.total - job.done) / speed)} left")
            return " · ".join(parts) or "Starting…"
        if job.state == VERIFYING:
            return f"Checking piece {job.piece} of {job.pieces}" if pieces else "Checking download"
        if job.state == DONE:
            if job.kind == UPLOAD:
                return f"Done · {human_size(job.total)}"
            return "Done · opening" if job.use_cache else f"Done · saved to {job.result_path.parent.name if job.result_path else 'folder'}"
        if job.state == FAILED:
            return f"Failed: {job.error}" if job.error else "Failed"
        return "Cancelled"

    def _on_action(self) -> None:
        manager = self.card.manager
        if self._action == "cancel":
            manager.cancel(self.job)
        elif self._action == "retry":
            manager.retry(self.job)
        elif self._action == "show" and self.job.result_path is not None:
            show_in_folder(str(self.job.result_path))
        self.card.refresh()


def show_in_folder(path: str) -> None:
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])  # noqa: S603, S607
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class TransfersCard(QFrame):
    """Floats over the bottom-right corner of ``anchor`` and lists uploads and downloads.

    A folder can queue thousands of files, so the card never makes a row for every job:
    it shows everything that's running plus a few waiting, failed and finished ones, and a
    "N more waiting" line. Updates are batched, so a burst of new or finished jobs costs one
    redraw instead of one per job.
    """

    def __init__(self, manager: TransferManager, anchor: QWidget):
        super().__init__(anchor)
        self.manager = manager
        self.anchor = anchor
        self.rows: dict[int, TransferRow] = {}  # only the jobs currently shown
        self._order: list[int] = []
        self._speed: dict[int, tuple[float, int, float]] = {}  # job id -> (time, bytes, speed)
        self._collapsed = False

        self.setObjectName("TransferCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(16, 24, 40, 60))
        self.setGraphicsEffect(shadow)

        header = self.header = QWidget()
        header.setObjectName("TransferHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        header.setFixedHeight(50)
        self.title = ElidedLabel()
        self.clear_button = QToolButton()
        self.clear_button.setObjectName("ClearButton")
        self.clear_button.setText("Clear")
        self.clear_button.setToolTip("Remove finished transfers from this list")
        self.clear_button.clicked.connect(self.clear_finished)
        self.collapse_button = QToolButton()
        self.collapse_button.setIconSize(QSize(18, 18))
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        close_button = QToolButton()
        close_button.setIcon(theme.icon("close", "#ffffff", 16, 2.2))
        close_button.setIconSize(QSize(16, 16))
        close_button.setToolTip("Hide (transfers keep running)")
        close_button.clicked.connect(self._close_clicked)
        h = QHBoxLayout(header)
        h.setContentsMargins(18, 0, 8, 0)
        h.setSpacing(2)
        h.addWidget(self.title, 1)
        h.addWidget(self.clear_button)
        h.addWidget(self.collapse_button)
        h.addWidget(close_button)

        self.list = QWidget()
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.more = QLabel()
        self.more.setObjectName("TransferMore")
        self.more.setContentsMargins(18, 10, 12, 10)
        self.more.hide()
        self.list_layout.addWidget(self.more)
        self.list_layout.addStretch(1)
        self.body = QScrollArea()
        self.body.setObjectName("TransferBody")
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.viewport().setAutoFillBackground(False)
        self.list.setAutoFillBackground(False)
        self.body.setWidget(self.list)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.body)

        # New or finished jobs only schedule an update; many at once still cost one redraw.
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(100)
        self._soon.timeout.connect(self.refresh)
        manager.job_added.connect(self._on_job_added)
        manager.job_finished.connect(lambda _job: self._schedule())
        anchor.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self._set_collapse_icon()
        self.hide()

    def retheme(self) -> None:
        for row in self.rows.values():
            row.retheme()
        self.refresh()

    # ------------------------------------------------------------ showing

    def show_card(self) -> None:
        self.show()
        self.refresh()
        self.raise_()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.header.setProperty("collapsed", self._collapsed)
        theme.restyle(self.header)
        self.layout().setContentsMargins(0, 0, 0, 0 if self._collapsed else 6)
        self._set_collapse_icon()
        self._place()

    def _set_collapse_icon(self) -> None:
        name = "chevron_up" if self._collapsed else "chevron_down"
        self.collapse_button.setIcon(theme.icon(name, "#ffffff", 18, 2.2))
        self.collapse_button.setToolTip("Expand" if self._collapsed else "Collapse")

    def _close_clicked(self) -> None:
        if not self.manager.has_active():
            self.clear_finished()
        self.hide()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.anchor and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self) -> None:
        width = max(220, min(CARD_WIDTH, self.anchor.width() - 2 * MARGIN))
        height = 50 if self._collapsed else 56
        more_shown = not self.more.isHidden()
        if not self._collapsed and (self.rows or more_shown):
            rows_height = sum(row.sizeHint().height() for row in self.rows.values())
            if more_shown:
                rows_height += self.more.sizeHint().height()
            room = self.anchor.height() - 2 * MARGIN - height
            height += max(60, min(rows_height, MAX_BODY_HEIGHT, room))
        self.setGeometry(self.anchor.width() - width - MARGIN, self.anchor.height() - height - MARGIN, width, height)

    # ------------------------------------------------------------ rows

    def _on_job_added(self, _job: Job) -> None:
        if not self.isVisible():
            if self._collapsed:
                self.toggle_collapsed()
            self.show()
            self.raise_()
        self._schedule()

    def _schedule(self) -> None:
        if not self._soon.isActive():
            self._soon.start()

    def clear_finished(self) -> None:
        self.manager.clear_finished()
        self.refresh()

    @staticmethod
    def _pick(jobs: list[Job]) -> list[Job]:
        """The jobs worth a row: all running, plus a few waiting, failed and finished."""
        running: list[Job] = []
        waiting: list[Job] = []
        failed: list[Job] = []
        done: list[Job] = []
        for job in jobs:
            if job.state == QUEUED:
                if len(waiting) < MAX_WAITING_ROWS:
                    waiting.append(job)
            elif job.state in (FAILED, CANCELLED):
                failed.append(job)
            elif job.state == DONE:
                done.append(job)
            else:
                running.append(job)
        return running + waiting + failed[-MAX_FAILED_ROWS:] + done[-MAX_DONE_ROWS:][::-1]

    def _sync_rows(self, shown: list[Job]) -> None:
        keep = {job.id for job in shown}
        for job_id in [i for i in self.rows if i not in keep]:
            row = self.rows.pop(job_id)
            self.list_layout.removeWidget(row)
            row.deleteLater()
            self._speed.pop(job_id, None)
        for position, job in enumerate(shown):
            row = self.rows.get(job.id)
            if row is None:
                row = self.rows[job.id] = TransferRow(job, self)
            else:
                self.list_layout.removeWidget(row)
            self.list_layout.insertWidget(position, row)
        self._order = [job.id for job in shown]

    def _rate(self, job: Job) -> float:
        now = time.monotonic()
        last = self._speed.get(job.id)
        if last is None or job.state not in (UPLOADING, DOWNLOADING):
            self._speed[job.id] = (now, job.done, 0.0)
            return 0.0
        t, done, speed = last
        dt = now - t
        if dt < 0.4:
            return speed
        instant = max(0, job.done - done) / dt
        speed = instant if speed == 0 else speed * 0.7 + instant * 0.3
        self._speed[job.id] = (now, job.done, speed)
        return speed

    def refresh(self) -> None:
        if not self.isVisible():
            return
        jobs = self.manager.jobs
        shown = self._pick(jobs)
        changed = [job.id for job in shown] != self._order
        if changed:
            self._sync_rows(shown)
        for row in self.rows.values():
            row.update_from_job(0.0 if row.job.finished else self._rate(row.job))

        uploading = downloading = waiting = done = failed = 0
        for job in jobs:
            if job.state == DONE:
                done += 1
            elif job.state in (FAILED, CANCELLED):
                failed += job.state == FAILED
            elif job.kind == UPLOAD:
                uploading += 1
            else:
                downloading += 1
            waiting += job.state == QUEUED

        parts = []
        if uploading:
            parts.append(f"Uploading {uploading:,} item{'s' if uploading != 1 else ''}")
        if downloading:
            word = "downloading" if parts else "Downloading"
            parts.append(f"{word} {downloading:,}" + ("" if parts else f" item{'s' if downloading != 1 else ''}"))
        if not parts:
            parts.append("All transfers finished" if jobs else "No transfers")
        elif done:
            parts.append(f"{done:,} done")
        if failed:
            parts.append(f"{failed:,} failed")
        self.title.setText(" · ".join(parts))
        self.clear_button.setVisible(len(jobs) > uploading + downloading)

        hidden_waiting = waiting - sum(1 for job in shown if job.state == QUEUED)
        hidden_done = done - sum(1 for job in shown if job.state == DONE)
        more = []
        if hidden_waiting > 0:
            more.append(f"{hidden_waiting:,} more waiting")
        if hidden_done > 0:
            more.append(f"{hidden_done:,} more finished")
        had_more = not self.more.isHidden()
        self.more.setText(" · ".join(more))
        self.more.setVisible(bool(more))
        if changed or had_more != bool(more):
            self._place()
