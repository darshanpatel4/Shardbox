"""The main "drive" window: header, folder sidebar, file list, details panel and transfers card."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..backup import IndexBackup
from ..config import Settings, app_data_dir, cache_dir
from ..db import FileRec, Folder, Index
from ..tg import TelegramService, display_name
from ..transfers import DONE, DOWNLOAD, FAILED, UPLOAD, Job, TransferManager
from . import theme
from .common import human_size, short_date, show_message, spawn
from .details_panel import PANEL_WIDTH, DetailsPanel
from .dialogs import MoveDialog, SettingsDialog
from .transfers_panel import TransfersCard

COL_NAME, COL_SIZE, COL_PARTS, COL_DATE, COL_LOCATION = range(5)
FOLDER, FILE = "folder", "file"
VIEW_FOLDER, VIEW_RECENT, VIEW_INCOMPLETE = "folder", "recent", "incomplete"
KEY_ROLE = Qt.ItemDataRole.UserRole
COUNT_ROLE = Qt.ItemDataRole.UserRole + 1


class RowDelegate(QStyledItemDelegate):
    """Gives every row a fixed, roomy height."""

    def __init__(self, height: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.height = height

    def sizeHint(self, option, index):  # noqa: N802 (Qt naming)
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self.height))
        return size


class NavDelegate(RowDelegate):
    """Sidebar rows; draws an amber count pill (unfinished uploads) on the right."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        count = index.data(COUNT_ROLE)
        if not count:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(option.font)
        font.setBold(True)
        font.setPixelSize(11)
        painter.setFont(font)
        text = str(count)
        width = max(20, painter.fontMetrics().horizontalAdvance(text) + 12)
        rect = QRect(option.rect.right() - width - 6, option.rect.center().y() - 9, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.AMBER_BG))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QColor(theme.AMBER_TEXT))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class EntryItem(QTreeWidgetItem):
    """A row in the file list; folders always sort before files."""

    def __init__(self, kind: str, entry_id: int, texts: list[str], keys: list):
        super().__init__(texts)
        self.kind = kind
        self.entry_id = entry_id
        self.keys = keys

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        if not isinstance(other, EntryItem):
            return super().__lt__(other)
        if self.kind != other.kind:
            ascending = self.treeWidget().header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            return (self.kind == FOLDER) == ascending
        col = self.treeWidget().sortColumn()
        return self.keys[col] < other.keys[col]


class FileTree(QTreeWidget):
    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.placeholder = ""
        self.setObjectName("FileList")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setIconSize(QSize(22, 22))
        self.setItemDelegate(RowDelegate(36, self))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            return super().dropEvent(event)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        event.acceptProposedAction()
        if paths:
            self.paths_dropped.emit(paths)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.topLevelItemCount() == 0 and self.placeholder:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)


class MainWindow(QMainWindow):
    def __init__(self, tg: TelegramService, index: Index, settings: Settings, on_closed: Callable[[], None]):
        super().__init__()
        self.tg = tg
        self.index = index
        self.settings = settings
        self.on_closed = on_closed
        self.view = VIEW_FOLDER
        self.current: int | None = None  # None = root ("My Drive")
        self.search_text = ""
        self._clearing_search = False
        self._nav_expanded: set[str] = {"root"}
        self._open_when_done: set[int] = set()
        self._closing = False
        self._can_close = False

        self.manager = TransferManager(tg, index, settings, cache_dir())
        self.backup = IndexBackup(tg, index, settings, self.manager, cache_dir())

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1280, 800)

        self._build_actions()
        self._build_menu()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_sidebar())
        self.splitter.addWidget(self._build_file_area())
        self.splitter.addWidget(self._build_details())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([230, 1280 - 230 - PANEL_WIDTH, PANEL_WIDTH])
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.transfers = TransfersCard(self.manager, self.file_area)
        self._build_statusbar()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh)

        index.add_listener(self._refresh_timer.start)
        self.manager.job_added.connect(lambda _job: self._refresh_timer.start())
        self.manager.job_finished.connect(self._on_job_finished)
        self.backup.status_changed.connect(self.backup_label.setText)
        theme.notifier().changed.connect(self._retheme)
        self.refresh()

    # ================================================================ building

    def _action(
        self, text: str, slot: Callable[[], None], shortcut: QKeySequence | str | None = None,
        icon: str | None = None, tip: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if icon is not None:
            self._action_icons[action] = icon
            action.setIcon(theme.icon(icon, theme.TEXT, 18))
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.triggered.connect(lambda _checked=False: slot())
        self.addAction(action)  # keyboard shortcuts work without a menu bar
        return action

    def _build_actions(self) -> None:
        self._action_icons: dict[QAction, str] = {}
        self.act_up = self._action("Up", self.go_up, "Alt+Up", "up", "Go to the parent folder")
        self.act_home = self._action("My Drive", self.go_home, "Alt+Home", "drive", "Go to the top folder")
        self.act_new_folder = self._action(
            "New folder", self.new_folder, "Ctrl+Shift+N", "new_folder", "Create a folder here"
        )
        self.act_upload = self._action("Upload files…", self.upload_files, "Ctrl+U", "upload", "Upload files here")
        self.act_upload_folder = self._action(
            "Upload folder…", self.upload_folder, None, "folder_open", "Upload a whole folder here"
        )
        self.act_download = self._action(
            "Download", self.download_selected, "Ctrl+D", "download", "Download the selected items"
        )
        self.act_open = self._action("Open", self.open_selected, None)
        self.act_rename = self._action("Rename", self.rename_selected, "F2", "rename")
        self.act_move = self._action("Move to…", self.move_selected, "Ctrl+M", "move")
        self.act_delete = self._action(
            "Delete", self.delete_selected, QKeySequence.StandardKey.Delete, "delete",
            "Delete the selected items from Telegram",
        )
        self.act_resume = self._action("Resume upload", self.resume_selected, None, "retry")
        self.act_transfers = self._action("Show transfers", lambda: self.transfers.show_card(), "Ctrl+J")
        self.act_prefs = self._action("Preferences…", self.open_preferences, "Ctrl+,", "settings")
        self.act_quit = self._action("Quit", self.close, "Ctrl+Q")
        self.act_backup = self._action("Back up index now", self.backup_index_now)
        self.act_restore = self._action("Restore index from Telegram…", self.restore_index)
        self.act_rebuild = self._action("Rebuild index from channel…", self.rebuild_index)
        self.act_logout = self._action("Log out…", self.log_out)
        self.act_data_dir = self._action("Open data folder", self.open_data_dir)
        self.act_about = self._action(f"About {APP_NAME}", self.about)

    def _build_menu(self) -> None:
        # Appearance: System default / Light / Dark
        self.appearance_menu = QMenu("Appearance", self)
        group = QActionGroup(self)
        group.setExclusive(True)
        self.appearance_actions: dict[str, QAction] = {}
        for mode in theme.MODES:
            action = QAction(theme.MODE_LABELS[mode], self, checkable=True)
            action.triggered.connect(lambda _checked=False, m=mode: self.set_appearance(m))
            group.addAction(action)
            self.appearance_menu.addAction(action)
            self.appearance_actions[mode] = action
        self.appearance_actions.get(self.settings.theme, self.appearance_actions["system"]).setChecked(True)

        m = self.main_menu = QMenu(self)
        m.addAction(self.act_prefs)
        m.addMenu(self.appearance_menu)
        m.addAction(self.act_transfers)
        m.addSeparator()
        index_menu = m.addMenu("Index")
        for a in (self.act_backup, self.act_restore, self.act_rebuild):
            index_menu.addAction(a)
        m.addAction(self.act_data_dir)
        m.addAction(self.act_about)
        m.addSeparator()
        m.addAction(self.act_logout)
        m.addAction(self.act_quit)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(4)

        logo = self.logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(28))
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        h.addWidget(logo)
        h.addSpacing(6)
        h.addWidget(title)
        h.addSpacing(20)

        upload = self.upload_button = QToolButton()
        upload.setObjectName("PrimaryTool")
        upload.setText("Upload")
        upload.setIcon(theme.icon("upload", theme.ON_ACCENT, 16, 2.2))
        upload.setIconSize(QSize(16, 16))
        upload.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        upload.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        upload.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_menu = QMenu(upload)
        upload_menu.addAction(self.act_upload)
        upload_menu.addAction(self.act_upload_folder)
        upload.setMenu(upload_menu)
        h.addWidget(upload)
        h.addSpacing(6)
        for action in (self.act_new_folder, self.act_download, self.act_delete):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(16, 16))
            h.addWidget(button)
        h.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search all files   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search_icon = self.search.addAction(
            theme.icon("search", theme.MUTED, 16), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.setFixedWidth(320)
        self.search.textChanged.connect(self._on_search)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self, self.search.setFocus)
        h.addWidget(self.search)
        h.addSpacing(6)

        mode_button = self.mode_button = QToolButton()
        mode_button.setIconSize(QSize(18, 18))
        mode_button.setToolTip("Appearance: light or dark")
        mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mode_button.setMenu(self.appearance_menu)
        h.addWidget(mode_button)

        more = self.more_button = QToolButton()
        more.setIcon(theme.icon("menu", theme.TEXT, 18))
        more.setIconSize(QSize(18, 18))
        more.setToolTip("Menu")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(self.main_menu)
        h.addWidget(more)
        self._set_mode_icon()
        return header

    # ================================================================ light / dark

    def set_appearance(self, mode: str) -> None:
        self.settings.theme = mode
        self.settings.save()
        if mode in self.appearance_actions:
            self.appearance_actions[mode].setChecked(True)
        theme.apply_theme(QApplication.instance(), mode)  # -> _retheme via notifier

    def _set_mode_icon(self) -> None:
        self.mode_button.setIcon(theme.icon("moon" if theme.is_dark() else "sun", theme.TEXT, 18))

    def _retheme(self) -> None:
        """Repaint everything that was drawn with the old colours."""
        for action, name in self._action_icons.items():
            action.setIcon(theme.icon(name, theme.TEXT, 18))
        self.logo.setPixmap(theme.logo_pixmap(28))
        self.upload_button.setIcon(theme.icon("upload", theme.ON_ACCENT, 16, 2.2))
        self.search_icon.setIcon(theme.icon("search", theme.MUTED, 16))
        self.more_button.setIcon(theme.icon("menu", theme.TEXT, 18))
        self._set_mode_icon()
        self.transfers.retheme()
        self.refresh()  # file list, sidebar and details panel are rebuilt with the new colours

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        side.setMinimumWidth(180)
        v = QVBoxLayout(side)
        v.setContentsMargins(8, 12, 8, 12)
        v.setSpacing(8)

        self.nav = QTreeWidget()
        self.nav.setObjectName("NavTree")
        self.nav.setHeaderHidden(True)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setIndentation(14)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setUniformRowHeights(True)
        self.nav.setItemDelegate(NavDelegate(32, self.nav))
        self.nav.currentItemChanged.connect(self._on_nav)
        self.nav.itemExpanded.connect(lambda item: self._nav_expanded.add(item.data(0, KEY_ROLE)))
        self.nav.itemCollapsed.connect(lambda item: self._nav_expanded.discard(item.data(0, KEY_ROLE)))
        v.addWidget(self.nav, 1)

        self.sidebar_info = QLabel()
        self.sidebar_info.setObjectName("SidebarInfo")
        self.sidebar_info.setWordWrap(True)
        self.sidebar_info.setContentsMargins(10, 6, 10, 0)
        v.addWidget(self.sidebar_info)
        return side

    def _build_file_area(self) -> QWidget:
        area = QWidget()
        area.setObjectName("FileArea")
        area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        v = QVBoxLayout(area)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("CrumbBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setFixedHeight(44)
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(10, 0, 16, 0)
        bh.setSpacing(2)
        self.crumbs = QWidget()
        self.crumbs_layout = QHBoxLayout(self.crumbs)
        self.crumbs_layout.setContentsMargins(0, 0, 0, 0)
        self.crumbs_layout.setSpacing(0)
        bh.addWidget(self.crumbs, 1)
        self.count_label = QLabel()
        self.count_label.setObjectName("ItemCount")
        bh.addWidget(self.count_label)
        v.addWidget(bar)

        self.tree = FileTree()
        self.tree.setHeaderLabels(["Name", "Size", "Pieces", "Uploaded", "Location"])
        header = self.tree.header()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        for col, width in ((COL_SIZE, 90), (COL_PARTS, 150), (COL_DATE, 110), (COL_LOCATION, 200)):
            self.tree.setColumnWidth(col, width)
        self.tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.tree.itemActivated.connect(lambda item, _col: self.open_item(item))
        self.tree.itemSelectionChanged.connect(self._update_actions)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.paths_dropped.connect(self._upload_paths)
        v.addWidget(self.tree, 1)
        self.file_area = area
        return area

    def _build_details(self) -> QWidget:
        self.details = DetailsPanel(self.index, {
            "download": self.act_download,
            "open": self.act_open,
            "resume": self.act_resume,
            "rename": self.act_rename,
            "move": self.act_move,
            "delete": self.act_delete,
        })
        return self.details

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.backup_label = QLabel()
        bar.addPermanentWidget(self.backup_label)

    # ================================================================ listing

    def _location_text(self, folder_id: int | None) -> str:
        return " / ".join(["My Drive"] + [f.name for f in self.index.folder_chain(folder_id)])

    def refresh(self) -> None:
        selected = {(i.kind, i.entry_id) for i in self._selected_items()}
        searching = bool(self.search_text)
        if self.current is not None and self.index.get_folder(self.current) is None:
            self.current = None  # the folder was deleted or the index was restored

        if searching:
            folders, files = self.index.search(self.search_text)
        elif self.view == VIEW_RECENT:
            folders, files = [], self.index.recent_files()
        elif self.view == VIEW_INCOMPLETE:
            folders, files = [], self.index.incomplete_files()
        else:
            folders, files = self.index.children(self.current)
        show_location = searching or self.view != VIEW_FOLDER
        part_counts = self.index.part_counts([f.id for f in files])
        sent_counts = self.index.uploaded_part_counts([f.id for f in files if not f.complete])
        uploading = self.manager.active_upload_file_ids()
        folder_icon = theme.folder_icon(22)
        amber = QColor(theme.AMBER_TEXT)
        bold = QFont(self.tree.font())
        bold.setBold(True)

        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        items: list[EntryItem] = []
        for folder in folders:
            location = self._location_text(folder.parent_id) if show_location else ""
            item = EntryItem(
                FOLDER, folder.id,
                [folder.name, "", "", short_date(folder.created_at), location],
                [folder.name.lower(), -1, -1, folder.created_at, location.lower()],
            )
            item.setIcon(COL_NAME, folder_icon)
            items.append(item)
        for rec in files:
            location = self._location_text(rec.folder_id) if show_location else ""
            pieces = part_counts.get(rec.id, 0)
            if rec.complete:
                pieces_text = str(pieces) if pieces else "—"
            elif rec.id in uploading:
                pieces_text = "Uploading…"
            else:
                pieces_text = f"Incomplete · {sent_counts.get(rec.id, 0)} of {pieces}"
            item = EntryItem(
                FILE, rec.id,
                [rec.name, human_size(rec.size), pieces_text, short_date(rec.created_at), location],
                [rec.name.lower(), rec.size, pieces, rec.created_at, location.lower()],
            )
            item.setIcon(COL_NAME, theme.badge_icon(rec.name, 22))
            item.setToolTip(COL_NAME, rec.name)
            if not rec.complete:
                item.setForeground(COL_PARTS, amber)
                item.setFont(COL_PARTS, bold)
                if rec.id not in uploading:
                    item.setToolTip(COL_PARTS, "This upload didn't finish. Select it and choose Resume upload, or delete it.")
            items.append(item)
        self.tree.addTopLevelItems(items)
        for item in items:
            if (item.kind, item.entry_id) in selected:
                item.setSelected(True)
        self.tree.setSortingEnabled(True)
        self.tree.blockSignals(False)
        self.tree.setColumnHidden(COL_LOCATION, not show_location)
        if searching:
            self.tree.placeholder = "Nothing matches your search."
        elif self.view == VIEW_RECENT:
            self.tree.placeholder = "Files you upload will show up here."
        elif self.view == VIEW_INCOMPLETE:
            self.tree.placeholder = "No unfinished uploads. Everything is safely stored."
        else:
            self.tree.placeholder = "This folder is empty.\nDrag files or folders here to upload them."
        self.tree.viewport().update()

        n = len(items)
        self.count_label.setText(f"{n} item{'' if n == 1 else 's'}")
        self._rebuild_crumbs()
        self._rebuild_nav()
        count, total = self.index.stats()
        channel = self.settings.channel_title or "storage channel"
        self.sidebar_info.setText(
            f"{count:,} file{'' if count == 1 else 's'} · {human_size(total)}\n"
            f"Channel: {channel}\nSigned in as {display_name(self.tg.me)}"
        )
        self._update_actions()

    def _rebuild_crumbs(self) -> None:
        while self.crumbs_layout.count():
            widget = self.crumbs_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()

        def crumb(text: str, folder_id: int | None, current: bool) -> QToolButton:
            button = QToolButton()
            button.setObjectName("Crumb")
            button.setProperty("current", current)
            button.setText(text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda: self.navigate(folder_id))
            return button

        def title(text: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName("Crumb")
            label.setProperty("current", True)
            return label

        if self.search_text:
            self.crumbs_layout.addWidget(title(f"Search results for “{self.search_text}”"))
        elif self.view == VIEW_RECENT:
            self.crumbs_layout.addWidget(title("Recent uploads"))
        elif self.view == VIEW_INCOMPLETE:
            self.crumbs_layout.addWidget(title("Unfinished uploads"))
        else:
            chain = self.index.folder_chain(self.current)
            self.crumbs_layout.addWidget(crumb("My Drive", None, not chain))
            for i, folder in enumerate(chain):
                sep = QLabel()
                sep.setObjectName("CrumbSep")
                sep.setPixmap(theme.icon_pixmap("chevron_right", theme.MUTED, 14, 2.0))
                self.crumbs_layout.addWidget(sep)
                self.crumbs_layout.addWidget(crumb(folder.name, folder.id, i == len(chain) - 1))
        self.crumbs_layout.addStretch(1)

    def _rebuild_nav(self) -> None:
        nav = self.nav
        nav.blockSignals(True)
        scroll = nav.verticalScrollBar().value()
        nav.clear()

        root = QTreeWidgetItem(["My Drive"])
        root.setData(0, KEY_ROLE, "root")
        root.setIcon(0, theme.icon("drive", theme.ACCENT, 16))
        nav.addTopLevelItem(root)
        by_key: dict[str, QTreeWidgetItem] = {"root": root}

        children: dict[int | None, list[Folder]] = defaultdict(list)
        for folder in self.index.all_folders():
            children[folder.parent_id].append(folder)
        folder_icon = theme.folder_icon(16)
        stack: list[tuple[QTreeWidgetItem, int | None]] = [(root, None)]
        seen: set[int] = set()
        while stack:
            parent_item, parent_id = stack.pop()
            for folder in children.get(parent_id, []):
                if folder.id in seen:
                    continue
                seen.add(folder.id)
                item = QTreeWidgetItem([folder.name])
                key = f"f:{folder.id}"
                item.setData(0, KEY_ROLE, key)
                item.setIcon(0, folder_icon)
                item.setToolTip(0, folder.name)
                parent_item.addChild(item)
                by_key[key] = item
                stack.append((item, folder.id))

        for key, text, icon_name in (("recent", "Recent", "clock"), ("incomplete", "Incomplete", "alert")):
            item = QTreeWidgetItem([text])
            item.setData(0, KEY_ROLE, key)
            item.setIcon(0, theme.icon(icon_name, theme.MUTED, 16))
            nav.addTopLevelItem(item)
            by_key[key] = item
        uploading = self.manager.active_upload_file_ids()
        stalled = sum(1 for f in self.index.incomplete_files() if f.id not in uploading)
        by_key["incomplete"].setData(0, COUNT_ROLE, stalled)

        if self.search_text:
            current_key = None
        elif self.view == VIEW_FOLDER:
            current_key = "root" if self.current is None else f"f:{self.current}"
            for folder in self.index.folder_chain(self.current)[:-1]:
                self._nav_expanded.add(f"f:{folder.id}")
        else:
            current_key = self.view
        for key in self._nav_expanded:
            if key in by_key:
                by_key[key].setExpanded(True)
        if current_key in by_key:
            nav.setCurrentItem(by_key[current_key])
        else:
            nav.setCurrentIndex(QModelIndex())
            nav.clearSelection()
        nav.verticalScrollBar().setValue(scroll)
        nav.blockSignals(False)

    def _on_nav(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            return
        key = current.data(0, KEY_ROLE)
        # Navigating rebuilds this tree, so do it after Qt finishes handling the click.
        if key == "root":
            QTimer.singleShot(0, lambda: self.navigate(None))
        elif key.startswith("f:"):
            folder_id = int(key[2:])
            QTimer.singleShot(0, lambda: self.navigate(folder_id))
        else:
            QTimer.singleShot(0, lambda: self.show_view(key))

    def _selected_items(self) -> list[EntryItem]:
        return [i for i in self.tree.selectedItems() if isinstance(i, EntryItem)]

    def _selected_files(self) -> list[FileRec]:
        recs = (self.index.get_file(i.entry_id) for i in self._selected_items() if i.kind == FILE)
        return [r for r in recs if r is not None]

    def _selected_folders(self) -> list[Folder]:
        folders = (self.index.get_folder(i.entry_id) for i in self._selected_items() if i.kind == FOLDER)
        return [f for f in folders if f is not None]

    def _update_actions(self) -> None:
        items = self._selected_items()
        uploading = self.manager.active_upload_file_ids()
        files = self._selected_files()
        folders = self._selected_folders()
        self.act_up.setEnabled(self.view == VIEW_FOLDER and self.current is not None and not self.search_text)
        self.act_new_folder.setEnabled(self.view == VIEW_FOLDER and not self.search_text)
        self.act_download.setEnabled(bool(folders) or any(f.complete for f in files))
        self.act_delete.setEnabled(bool(items))
        self.act_move.setEnabled(bool(items))
        self.act_rename.setEnabled(len(items) == 1)
        self.act_open.setEnabled(len(items) == 1)
        self.act_resume.setEnabled(any(not f.complete and f.id not in uploading for f in files))

        if len(files) == 1 and not folders:
            self.details.show_file(files[0], files[0].id in uploading)
        elif len(folders) == 1 and not files:
            self.details.show_folder(folders[0])
        elif files or folders:
            self.details.show_many(files, folders)
        else:
            n = self.tree.topLevelItemCount()
            self.details.show_nothing(f"{n} item{'' if n == 1 else 's'} here" if n else "")

    # ================================================================ navigation

    def _clear_search(self) -> None:
        if self.search_text or self.search.text():
            self._clearing_search = True
            self.search.clear()  # not blockSignals: the box needs the signal to hide its clear button
            self._clearing_search = False
            self.search_text = ""

    def navigate(self, folder_id: int | None) -> None:
        if self.view != VIEW_FOLDER:
            self.tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.view = VIEW_FOLDER
        self.current = folder_id
        self._clear_search()
        self.refresh()

    def show_view(self, view: str) -> None:
        """Show "Recent" or "Incomplete" (lists across all folders)."""
        self.view = view
        self.current = None  # uploads made from these views go to My Drive
        self._clear_search()
        self.tree.sortByColumn(COL_DATE, Qt.SortOrder.DescendingOrder)
        self.refresh()

    def go_up(self) -> None:
        if self.current is not None:
            folder = self.index.get_folder(self.current)
            self.navigate(folder.parent_id if folder else None)

    def go_home(self) -> None:
        self.navigate(None)

    def _on_search(self, text: str) -> None:
        if self._clearing_search:
            return
        self.search_text = text.strip()
        self.refresh()

    def open_selected(self) -> None:
        items = self._selected_items()
        if len(items) == 1:
            self.open_item(items[0])

    def open_item(self, item: QTreeWidgetItem) -> None:
        if not isinstance(item, EntryItem):
            return
        if item.kind == FOLDER:
            self.navigate(item.entry_id)
            return
        rec = self.index.get_file(item.entry_id)
        if rec is None:
            return
        if not rec.complete:
            self._resume([rec])
            return
        job = self.manager.add_download(rec, None, use_cache=True)
        self._open_when_done.add(job.id)
        self.statusBar().showMessage(f"Downloading {rec.name} to open it…", 4000)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if isinstance(item, EntryItem) and not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        items = self._selected_items()
        if items:
            if len(items) == 1:
                menu.addAction(self.act_open)
            menu.addAction(self.act_download)
            if any(not f.complete for f in self._selected_files()):
                menu.addAction(self.act_resume)
            menu.addSeparator()
            if len(items) == 1:
                menu.addAction(self.act_rename)
            menu.addAction(self.act_move)
            menu.addSeparator()
            menu.addAction(self.act_delete)
        else:
            menu.addAction(self.act_upload)
            menu.addAction(self.act_upload_folder)
            menu.addAction(self.act_new_folder)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ================================================================ actions

    def new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:", text="New folder")
        if not ok:
            return
        try:
            folder = self.index.create_folder(self.current, name)
        except ValueError as exc:
            QMessageBox.warning(self, "New folder", str(exc))
            return
        self.refresh()
        for item in self._all_items():
            if item.kind == FOLDER and item.entry_id == folder.id:
                self.tree.setCurrentItem(item)

    def _all_items(self) -> list[EntryItem]:
        return [
            item for i in range(self.tree.topLevelItemCount())
            if isinstance(item := self.tree.topLevelItem(i), EntryItem)
        ]

    def upload_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Upload files")
        self._upload_paths(paths)

    def upload_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Upload folder")
        if path:
            self._upload_paths([path])

    def _upload_paths(self, paths: list[str]) -> None:
        files = folders = 0
        for path in paths:
            if os.path.isdir(path):
                files += self.manager.add_folder_upload(path, self.current)
                folders += 1
            elif os.path.isfile(path):
                self.manager.add_upload(path, self.current)
                files += 1
        if files or folders:
            self.transfers.show_card()
            self.statusBar().showMessage(f"Queued {files} file(s) for upload", 4000)

    def download_selected(self) -> None:
        files = self._selected_files()
        folders = self._selected_folders()
        if not files and not folders:
            return
        dest = self.settings.download_dir
        if not dest or not os.path.isdir(dest):
            dest = QFileDialog.getExistingDirectory(self, "Download to", str(Path.home() / "Downloads"))
            if not dest:
                return
        queued = skipped = 0
        for rec in files:
            if rec.complete:
                self.manager.add_download(rec, dest)
                queued += 1
            else:
                skipped += 1
        for folder in folders:
            for rec in self.index.files_in_subtree(folder.id):
                if not rec.complete:
                    skipped += 1
                    continue
                chain = self.index.folder_chain(rec.folder_id)
                ids = [f.id for f in chain]
                relative = Path(*[f.name for f in chain[ids.index(folder.id):]])
                self.manager.add_download(rec, str(Path(dest) / relative))
                queued += 1
        self.transfers.show_card()
        message = f"Downloading {queued} file(s) to {dest}"
        if skipped:
            message += f" ({skipped} unfinished upload(s) skipped)"
        self.statusBar().showMessage(message, 6000)

    def rename_selected(self) -> None:
        items = self._selected_items()
        if len(items) != 1:
            return
        item = items[0]
        old = item.text(COL_NAME)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old)
        if not ok or name.strip() == old:
            return
        try:
            if item.kind == FOLDER:
                self.index.rename_folder(item.entry_id, name)
            else:
                self.index.rename_file(item.entry_id, name)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename", str(exc))

    def move_selected(self) -> None:
        files = self._selected_files()
        folders = self._selected_folders()
        if not files and not folders:
            return
        exclude: set[int] = set()
        for folder in folders:
            exclude.update(self.index.descendant_folder_ids(folder.id))
        dialog = MoveDialog(self.index, exclude, self)
        if not dialog.exec():
            return
        dest = dialog.destination()
        problems = []
        with self.index.batch():
            for folder in folders:
                try:
                    self.index.move_folder(folder.id, dest)
                except ValueError as exc:
                    problems.append(str(exc))
            for rec in files:
                try:
                    self.index.move_file(rec.id, dest)
                except ValueError as exc:
                    problems.append(str(exc))
        if problems:
            QMessageBox.warning(self, "Move", "Some items were not moved:\n\n" + "\n".join(problems))

    def delete_selected(self) -> None:
        files = self._selected_files()
        folders = self._selected_folders()
        if not files and not folders:
            return
        file_ids = {r.id for r in files}
        for folder in folders:
            file_ids.update(r.id for r in self.index.files_in_subtree(folder.id))
        busy = file_ids & self.manager.active_file_ids()
        if busy:
            QMessageBox.information(
                self, "Delete", "Some of these files are being transferred. Cancel those transfers or wait for them first."
            )
            return
        names = [f.name for f in folders] + [r.name for r in files]
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} items"
        extra = f"\n\nThis includes {len(file_ids)} file(s) inside the selected folders." if folders else ""
        answer = QMessageBox.question(
            self, "Delete",
            f"Delete {what} permanently?\n\nThe files will be deleted from your Telegram channel too. "
            f"This cannot be undone.{extra}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        spawn(self._delete(sorted(file_ids), [f.id for f in folders]), parent=self, error_title="Delete failed")

    async def _delete(self, file_ids: list[int], folder_ids: list[int]) -> None:
        self.statusBar().showMessage("Deleting…")
        message_ids = self.index.message_ids(file_ids)
        if message_ids:
            await self.tg.delete_messages(message_ids)
        with self.index.batch():
            for file_id in file_ids:
                self.index.delete_file(file_id)
            for folder_id in folder_ids:
                if self.index.get_folder(folder_id) is not None:
                    self.index.delete_folder(folder_id)
        self.statusBar().showMessage(f"Deleted {len(file_ids)} file(s)", 4000)

    def resume_selected(self) -> None:
        self._resume([r for r in self._selected_files() if not r.complete])

    def _resume(self, recs: list[FileRec]) -> None:
        uploading = self.manager.active_upload_file_ids()
        missing = []
        for rec in recs:
            if rec.id in uploading:
                continue
            if rec.source_path and os.path.isfile(rec.source_path):
                self.manager.add_upload(rec.source_path, rec.folder_id)
            else:
                missing.append(f"{rec.name}: original file not found ({rec.source_path or 'unknown location'})")
        if missing:
            QMessageBox.warning(
                self, "Resume upload",
                "These uploads can't be resumed because the original file is gone. Delete them and "
                "upload the file again:\n\n" + "\n".join(missing),
            )

    def _on_job_finished(self, job: Job) -> None:
        self._refresh_timer.start()
        if job.id in self._open_when_done:
            self._open_when_done.discard(job.id)
            if job.state == DONE and job.result_path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.result_path)))
        if job.state == FAILED:
            verb = "Upload" if job.kind == UPLOAD else "Download"
            self.statusBar().showMessage(f"{verb} of {job.name} failed: {job.error}", 10000)
        elif job.state == DONE and job.kind == DOWNLOAD and not job.use_cache and not self.manager.has_active():
            self.statusBar().showMessage("All downloads finished", 6000)

    # ================================================================ index menu

    def backup_index_now(self) -> None:
        spawn(self.backup.backup_now(), parent=self, error_title="Index backup failed")

    def restore_index(self) -> None:
        if self.manager.has_active():
            QMessageBox.information(self, "Restore index", "Wait for the transfers to finish first.")
            return
        answer = QMessageBox.question(
            self, "Restore index",
            "Replace this computer's index with the newest backup stored in your channel?\n\n"
            "Your current index will be kept as a .bak file in the data folder.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            spawn(self._restore(), parent=self, error_title="Restore failed")

    async def _restore(self) -> None:
        self.statusBar().showMessage("Looking for index backups in the channel…")
        safety_copy = await self.backup.restore_latest(full_scan=True)
        self.statusBar().clearMessage()
        if safety_copy is None:
            show_message(self, "Restore index", "No index backup was found in the channel.")
            return
        self.navigate(None)
        show_message(self, "Restore index", f"Index restored.\n\nThe previous index was saved as:\n{safety_copy}")

    def rebuild_index(self) -> None:
        if self.manager.has_active():
            QMessageBox.information(self, "Rebuild index", "Wait for the transfers to finish first.")
            return
        answer = QMessageBox.question(
            self, "Rebuild index",
            "Scan every message in the storage channel and add any stored file that's missing from the "
            "index?\n\nUse this if your index and its backups are lost. Files come back under the name and "
            "folder they were first uploaded with. Big channels can take a few minutes.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            spawn(self._rebuild(), parent=self, error_title="Rebuild failed")

    async def _rebuild(self) -> None:
        def progress(seen: int) -> None:
            self.statusBar().showMessage(f"Scanning channel… {seen} messages checked")

        added, incomplete = await self.backup.rebuild_from_channel(progress)
        self.statusBar().clearMessage()
        text = f"Added {added} file(s) to the index."
        if incomplete:
            text += f"\n\n{incomplete} file(s) were skipped because some of their pieces are missing from the channel."
        show_message(self, "Rebuild index", text)

    # ================================================================ other menus

    def open_preferences(self) -> None:
        before = self.settings.theme
        SettingsDialog(self.settings, self.tg.premium, self).exec()
        if self.settings.theme != before:
            self.set_appearance(self.settings.theme)
        self.manager.pump()  # "files at once" may have changed

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir())))

    def about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>A drive-style app that stores your files in your own private Telegram channel. "
            "Files over Telegram's size limit are split into pieces automatically.</p>"
            "<p>Open source under the MIT License.</p>",
        )

    def log_out(self) -> None:
        if self.manager.has_active():
            QMessageBox.information(self, "Log out", "Wait for the transfers to finish, or cancel them, first.")
            return
        answer = QMessageBox.question(
            self, "Log out",
            "Log out of Telegram on this computer and close the app?\n\n"
            "Your files stay in your channel. Your index stays on this computer and is backed up to the channel.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            spawn(self._log_out(), parent=self, error_title="Log out failed")

    async def _log_out(self) -> None:
        if self.backup.dirty:
            try:
                await self.backup.backup_now()
            except Exception:  # noqa: BLE001 - logging out matters more
                pass
        await self.tg.log_out()
        self._can_close = True
        self.close()

    # ================================================================ closing

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._can_close:
            event.accept()
            self.on_closed()
            return
        event.ignore()
        if self._closing:
            return
        if self.manager.has_active():
            answer = QMessageBox.question(
                self, "Quit",
                "Transfers are still running. Quit anyway?\n\n"
                "Unfinished uploads can be resumed next time (right-click → Resume upload).",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._closing = True
        spawn(self._shutdown(), on_error=lambda _e: self._finish_close())

    async def _shutdown(self) -> None:
        self.setEnabled(False)
        self.manager.stop_all()
        await self.manager.wait_idle(timeout=10)
        if self.backup.dirty and self.settings.auto_backup_index:
            self.statusBar().showMessage("Saving index backup to Telegram before quitting…")
            try:
                await asyncio.wait_for(self.backup.backup_now(), timeout=120)
            except Exception:  # noqa: BLE001 - quitting must not be blocked
                pass
        self._finish_close()

    def _finish_close(self) -> None:
        self._can_close = True
        self.close()
