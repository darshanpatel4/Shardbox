"""The right-hand panel: details of the selected file or folder, and its pieces."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..db import FileRec, Folder, Index
from . import theme
from .common import human_size, long_date

PANEL_WIDTH = 320
ZERO_WIDTH_SPACE = "​"  # invisible; lets Qt wrap a long name at that point


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class DetailsPanel(QWidget):
    """Rebuilt from scratch whenever the selection changes (it's small, so that's cheap)."""

    def __init__(self, index: Index, actions: dict[str, QAction], parent: QWidget | None = None):
        super().__init__(parent)
        self.index = index
        self.actions = actions
        self.setObjectName("Details")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setMinimumWidth(260)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("DetailsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.footer = QWidget()
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(20, 10, 20, 16)
        self.footer_layout.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.footer)

    # ------------------------------------------------------------ helpers

    def _button(self, text: str, action: str, primary: bool = False, danger: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setProperty("primary", True)
        if danger:
            button.setProperty("danger", True)
        act = self.actions[action]
        button.setEnabled(act.isEnabled())
        button.clicked.connect(lambda: act.trigger())
        return button

    def _set_content(self, widget: QWidget, footer: list[QPushButton]) -> None:
        widget.setAutoFillBackground(False)
        self.scroll.setWidget(widget)  # the previous content widget is deleted
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for button in footer:
            self.footer_layout.addWidget(button)
        self.footer_layout.addStretch(1)
        self.footer.setVisible(bool(footer))

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 12)
        layout.setSpacing(16)
        return page, layout

    def _heading(self, pixmap, title: str, subtitle: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        picture = QLabel()
        picture.setPixmap(pixmap)
        picture.setFixedSize(56, 56)
        h.addWidget(picture, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = _label(title, "DetailTitle", wrap=True)
        name.setToolTip(title)
        # let long names without spaces (e.g. "ubuntu-24.04-desktop-amd64.iso") wrap after - _ .
        if " " not in title and len(title) > 24:
            name.setText(re.sub(r"([-_.])", lambda m: m.group(1) + ZERO_WIDTH_SPACE, title))
        text.addWidget(name)
        text.addWidget(_label(subtitle, "Muted", wrap=True))
        text.addStretch(1)
        h.addLayout(text, 1)
        return row

    @staticmethod
    def _facts(rows: list[tuple[str, str, str | None]]) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for i, (key, value, tip) in enumerate(rows):
            grid.addWidget(_label(key, "Muted"), i, 0, Qt.AlignmentFlag.AlignTop)
            value_label = _label(value, "Mono" if key == "SHA-256" else None, wrap=True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if tip:
                value_label.setToolTip(tip)
            grid.addWidget(value_label, i, 1)
        return box

    def _location(self, folder_id: int | None) -> str:
        return " / ".join(["My Drive"] + [f.name for f in self.index.folder_chain(folder_id)])

    # ------------------------------------------------------------ pages

    def show_nothing(self, summary: str) -> None:
        page, layout = self._page()
        layout.addStretch(1)
        icon = QLabel()
        icon.setPixmap(theme.icon_pixmap("drive", theme.FAINT, 40, 1.6))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        text = _label("Select a file or folder to see its details.", "Placeholder", wrap=True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text)
        if summary:
            extra = _label(summary, "Muted", wrap=True)
            extra.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(extra)
        layout.addStretch(2)
        self._set_content(page, [])

    def show_file(self, rec: FileRec, uploading: bool) -> None:
        _, _, kind = theme.file_kind(rec.name)
        parts = self.index.parts(rec.id)
        sent = sum(1 for p in parts if p.message_id)
        page, layout = self._page()
        layout.addWidget(self._heading(theme.badge_pixmap(rec.name, 56), rec.name, f"{human_size(rec.size)} · {kind}"))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if rec.complete:
            buttons.addWidget(self._button("Download", "download", primary=True), 1)
            buttons.addWidget(self._button("Open", "open"), 1)
        elif uploading:
            buttons.addWidget(_label("Uploading now…", "Muted"))
        else:
            buttons.addWidget(self._button("Resume upload", "resume", primary=True), 1)
        layout.addLayout(buttons)

        sha = rec.sha256
        layout.addWidget(self._facts([
            ("Uploaded" if rec.complete else "Started", long_date(rec.created_at), None),
            ("Location", self._location(rec.folder_id), None),
            ("SHA-256", f"{sha[:8]}…{sha[-8:]}" if len(sha) > 20 else sha, sha),
        ]))

        if rec.complete:
            title = "Stored as 1 piece" if len(parts) == 1 else f"Stored as {len(parts)} pieces"
        else:
            title = f"Uploaded {sent} of {len(parts)} pieces"
        if parts:
            layout.addWidget(_label(title, "SectionTitle"))
            pieces = QVBoxLayout()
            pieces.setSpacing(6)
            for part in parts:
                pieces.addWidget(self._piece_row(part.idx, part.size, part.message_id, len(parts)))
            layout.addLayout(pieces)
        layout.addStretch(1)
        self._set_content(page, [
            self._button("Rename", "rename"),
            self._button("Move", "move"),
            self._button("Delete", "delete", danger=True),
        ])

    @staticmethod
    def _piece_row(idx: int, size: int, message_id: int | None, count: int) -> QWidget:
        row = QFrame()
        row.setObjectName("PieceRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(10)
        mark = QLabel()
        if message_id:
            mark.setPixmap(theme.icon_pixmap("check", theme.SUCCESS, 16, 2.4))
            row.setToolTip(f"Saved in Telegram (message #{message_id})")
        else:
            mark.setPixmap(theme.icon_pixmap("circle", theme.FAINT, 16, 2.0))
            row.setToolTip("Not uploaded yet")
        h.addWidget(mark)
        h.addWidget(_label(f"part{idx + 1:03d}" if count > 1 else "whole file", "Mono"), 1)
        h.addWidget(_label(human_size(size), "Muted"))
        return row

    def show_folder(self, folder: Folder) -> None:
        count, total = self.index.folder_stats(folder.id)
        page, layout = self._page()
        subtitle = f"{count} file{'s' if count != 1 else ''} · {human_size(total)}" if count else "Empty folder"
        layout.addWidget(self._heading(theme.folder_tile(56), folder.name, subtitle))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._button("Open", "open", primary=True), 1)
        buttons.addWidget(self._button("Download", "download"), 1)
        layout.addLayout(buttons)
        layout.addWidget(self._facts([
            ("Created", long_date(folder.created_at), None),
            ("Location", self._location(folder.parent_id), None),
        ]))
        layout.addStretch(1)
        self._set_content(page, [
            self._button("Rename", "rename"),
            self._button("Move", "move"),
            self._button("Delete", "delete", danger=True),
        ])

    def show_many(self, files: list[FileRec], folders: list[Folder]) -> None:
        count = len(files)
        total = sum(f.size for f in files)
        for folder in folders:
            n, size = self.index.folder_stats(folder.id)
            count += n
            total += size
        page, layout = self._page()
        items = len(files) + len(folders)
        layout.addWidget(self._heading(
            theme.folder_tile(56), f"{items} items selected",
            f"{count} file{'s' if count != 1 else ''} · {human_size(total)}",
        ))
        layout.addWidget(self._button("Download all", "download", primary=True))
        layout.addStretch(1)
        self._set_content(page, [
            self._button("Move", "move"),
            self._button("Delete", "delete", danger=True),
        ])
