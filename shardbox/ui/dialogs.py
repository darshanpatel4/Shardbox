"""Preferences and "Move to…" dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..db import Index
from .theme import MODE_LABELS, MODES


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, premium: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(480)

        max_split = 4000 if premium else 2000
        self.split = QSpinBox()
        self.split.setRange(50, max_split)
        self.split.setSuffix(" MB")
        self.split.setValue(min(settings.split_size_mb, max_split))
        self.split.setToolTip(
            "Files bigger than this are cut into pieces. Telegram allows 2000 MB per file "
            "(4000 MB with Premium)."
        )
        self.parallel_files = QSpinBox()
        self.parallel_files.setRange(1, 6)
        self.parallel_files.setValue(settings.parallel_files)
        self.upload_workers = QSpinBox()
        self.upload_workers.setRange(1, 8)
        self.upload_workers.setValue(settings.upload_workers)
        self.download_workers = QSpinBox()
        self.download_workers.setRange(1, 8)
        self.download_workers.setValue(settings.download_workers)

        self.download_dir = QLineEdit(settings.download_dir)
        self.download_dir.setPlaceholderText("Ask every time")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.download_dir, 1)
        dir_row.addWidget(browse)

        self.auto_backup = QCheckBox("Back up the index to the channel automatically after changes")
        self.auto_backup.setChecked(settings.auto_backup_index)

        self.appearance = QComboBox()
        for mode in MODES:
            self.appearance.addItem(MODE_LABELS[mode], mode)
        self.appearance.setCurrentIndex(max(0, self.appearance.findData(settings.theme)))
        self.appearance.setToolTip("“System default” follows your Windows light/dark setting.")

        form = QFormLayout()
        form.addRow("Appearance", self.appearance)
        form.addRow("Split files bigger than", self.split)
        form.addRow("Files transferred at once", self.parallel_files)
        form.addRow("Connections per upload", self.upload_workers)
        form.addRow("Connections per download", self.download_workers)
        form.addRow("Default download folder", dir_row)
        form.addRow(self.auto_backup)

        note = QLabel(
            "More connections can be faster, but Telegram may slow down accounts that use too many. "
            "4 is a safe default."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Default download folder", self.download_dir.text())
        if path:
            self.download_dir.setText(path)

    def _save(self) -> None:
        s = self.settings
        s.split_size_mb = self.split.value()
        s.parallel_files = self.parallel_files.value()
        s.upload_workers = self.upload_workers.value()
        s.download_workers = self.download_workers.value()
        s.download_dir = self.download_dir.text().strip()
        s.auto_backup_index = self.auto_backup.isChecked()
        s.theme = self.appearance.currentData()
        s.save()
        self.accept()


class MoveDialog(QDialog):
    """Pick a destination folder."""

    ROOT = -1

    def __init__(self, index: Index, exclude_folder_ids: set[int], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Move to…")
        self.resize(380, 440)
        self.index = index
        self.exclude = exclude_folder_ids

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        root = QTreeWidgetItem(["My Drive"])
        root.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
        root.setData(0, Qt.ItemDataRole.UserRole, self.ROOT)
        self.tree.addTopLevelItem(root)
        self._fill(root, None, icon)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Move here")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)

    def _fill(self, item: QTreeWidgetItem, folder_id: int | None, icon) -> None:
        folders, _ = self.index.children(folder_id)
        for folder in folders:
            if folder.id in self.exclude:
                continue  # can't move a folder into itself
            child = QTreeWidgetItem([folder.name])
            child.setIcon(0, icon)
            child.setData(0, Qt.ItemDataRole.UserRole, folder.id)
            item.addChild(child)
            self._fill(child, folder.id, icon)

    def destination(self) -> int | None:
        item = self.tree.currentItem()
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else self.ROOT
        return None if value == self.ROOT else value
