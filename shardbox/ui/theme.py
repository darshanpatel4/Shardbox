"""The app's look: light and dark colours, the Qt stylesheet, line icons and file-type badges.

Both palettes follow Windows 11 File Explorer. Everything visual lives here so the windows
only set object names (``setObjectName("Header")``) and the stylesheet does the rest.

The colour names below (``theme.ACCENT``, ``theme.TEXT``…) always hold the colours of the
current mode. Widgets that paint icons themselves listen to ``notifier().changed`` and
repaint when the mode changes.
"""

from __future__ import annotations

import sys
from functools import lru_cache

from PySide6.QtCore import QByteArray, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "System default", "light": "Light", "dark": "Dark"}

# ---------------------------------------------------------------- colours

# Light: Windows 11 File Explorer, light theme. These are also the starting values.
ACCENT = "#005fb8"  # Windows accent blue
ACCENT_HOVER = "#196ebf"
ACCENT_PRESSED = "#004c93"
ACCENT_DISABLED = "#99c0e6"
ON_ACCENT = "#ffffff"  # text and icons on accent-coloured buttons
ACCENT_SOFT = "#cce8ff"  # Explorer's selected row
ACCENT_TEXT = "#1a1a1a"  # Explorer keeps selected text black
HOVER_BLUE = "#e5f3ff"  # Explorer's hovered row

TEXT = "#1a1a1a"
MUTED = "#5d5d5d"
FAINT = "#8a8a8a"  # icons only (too light for text)
DISABLED = "#a0a0a0"

BG = "#f9f9f9"  # sidebar, details panel, dialogs
SURFACE = "#ffffff"  # file list
CONTROL = "#ffffff"  # buttons and text boxes
CONTROL_HOVER = "#f5f5f5"
CONTROL_PRESSED = "#f0f0f0"
COMMAND_BAR = "#f3f3f3"  # the top bar
SUBTLE = "#f0f0f0"
HOVER = "#f5f5f5"
TOOL_HOVER = "#e6e6e6"
TOOL_PRESSED = "#dbdbdb"
BORDER = "#e5e5e5"
BORDER_STRONG = "#d1d1d1"
SCROLL = "#d1d1d1"
SCROLL_HOVER = "#a8a8a8"
TRACK = "#e6e6e6"  # empty part of progress bars
ROW_LINE = "#efefef"

AMBER_BG = "#fff4ce"  # Windows "caution" colours
AMBER_TEXT = "#9d5d00"
DANGER = "#c42b1c"  # Windows "critical" colours
DANGER_BORDER = "#f4bfba"
DANGER_SOFT = "#fde7e9"
SUCCESS = "#0f7b0f"

CARD_BG = "#ffffff"  # transfers card
CARD_HEADER = "#202020"
CARD_HEADER_HOVER = "#3a3a3a"

FOLDER_TILE = "#fff6dc"
FOLDER_BACK = "#e6a817"  # two-tone yellow folder, like Explorer's
FOLDER_FRONT = "#ffc83d"

_TOKENS = [name for name in dir() if name.isupper() and name not in ("MODES", "MODE_LABELS")]
LIGHT = {name: globals()[name] for name in _TOKENS}

# Dark: Windows 11 File Explorer, dark theme.
DARK = {
    **LIGHT,
    "ACCENT": "#60cdff",
    "ACCENT_HOVER": "#58b9e5",
    "ACCENT_PRESSED": "#4ea4cc",
    "ACCENT_DISABLED": "#3b5a6b",
    "ON_ACCENT": "#000000",
    "ACCENT_SOFT": "#2b4255",
    "ACCENT_TEXT": "#ffffff",
    "HOVER_BLUE": "#2d2d2d",
    "TEXT": "#ffffff",
    "MUTED": "#c8c8c8",
    "FAINT": "#8f8f8f",
    "DISABLED": "#6e6e6e",
    "BG": "#202020",
    "SURFACE": "#191919",
    "CONTROL": "#2d2d2d",
    "CONTROL_HOVER": "#323232",
    "CONTROL_PRESSED": "#272727",
    "COMMAND_BAR": "#2b2b2b",
    "SUBTLE": "#2b2b2b",
    "HOVER": "#262626",
    "TOOL_HOVER": "#383838",
    "TOOL_PRESSED": "#303030",
    "BORDER": "#333333",
    "BORDER_STRONG": "#474747",
    "SCROLL": "#4a4a4a",
    "SCROLL_HOVER": "#6a6a6a",
    "TRACK": "#3a3a3a",
    "ROW_LINE": "#333333",
    "AMBER_BG": "#433519",
    "AMBER_TEXT": "#fce100",
    "DANGER": "#ff99a4",
    "DANGER_BORDER": "#6e3a3f",
    "DANGER_SOFT": "#442726",
    "SUCCESS": "#6ccb5f",
    "CARD_BG": "#2b2b2b",
    "CARD_HEADER": "#3d3d3d",
    "CARD_HEADER_HOVER": "#4d4d4d",
    "FOLDER_TILE": "#3a3220",
}

MONO_FONTS = '"Cascadia Mono", Consolas, "JetBrains Mono", "DejaVu Sans Mono", monospace'

# ---------------------------------------------------------------- stylesheet


def _stylesheet() -> str:
    return f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog, QMessageBox {{ background: {BG}; }}
QToolTip {{ background: {CARD_HEADER}; color: #ffffff; border: none; padding: 5px 8px; }}

#Header {{ background: {COMMAND_BAR}; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 15px; font-weight: 700; }}
#Sidebar {{ background: {BG}; }}
#SidebarInfo {{ color: {MUTED}; font-size: 12px; }}
#FileArea {{ background: {SURFACE}; }}
#CrumbBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#Crumb {{ color: {MUTED}; padding: 4px 6px; }}
#Crumb[current="true"] {{ color: {TEXT}; font-weight: 600; }}
#CrumbSep, #ItemCount {{ color: {MUTED}; }}
#Details {{ background: {BG}; }}
QSplitter::handle {{ background: {BORDER}; }}

QStatusBar {{ background: {COMMAND_BAR}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}

QPushButton {{
    background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {CONTROL_HOVER}; }}
QPushButton:pressed {{ background: {CONTROL_PRESSED}; }}
QPushButton:disabled {{ color: {DISABLED}; border-color: {BORDER}; background: {CONTROL}; }}
QPushButton:default, QPushButton[primary="true"] {{
    background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; font-weight: 600;
}}
QPushButton:default:hover, QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton:default:pressed, QPushButton[primary="true"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton:default:disabled, QPushButton[primary="true"]:disabled {{
    background: {ACCENT_DISABLED}; border-color: {ACCENT_DISABLED}; color: {ON_ACCENT};
}}
QPushButton[danger="true"] {{ color: {DANGER}; border-color: {DANGER_BORDER}; }}
QPushButton[danger="true"]:hover {{ background: {DANGER_SOFT}; }}

QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 10px; color: {TEXT}; }}
QToolButton:hover {{ background: {TOOL_HOVER}; }}
QToolButton:pressed {{ background: {TOOL_PRESSED}; }}
QToolButton:disabled {{ color: {DISABLED}; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
#PrimaryTool {{ background: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; padding: 6px 14px; }}
#PrimaryTool:hover {{ background: {ACCENT_HOVER}; }}
#PrimaryTool:pressed {{ background: {ACCENT_PRESSED}; }}

QLineEdit, QSpinBox, QComboBox {{
    background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {SUBTLE}; color: {MUTED}; }}
#Search {{ background: {CONTROL}; border-color: {BORDER}; padding-left: 4px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_TEXT};
}}
QCheckBox, QRadioButton {{ spacing: 8px; }}

QTreeView, QTreeWidget, QListView, QTableView {{ background: {SURFACE}; color: {TEXT}; border: none; outline: 0; }}
QTreeView::item {{ padding: 0 4px; border: none; }}
QTreeView::item:hover {{ background: {HOVER_BLUE}; }}
QTreeView::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}
#NavTree {{ background: transparent; }}
#NavTree::item {{ border-radius: 4px; padding: 0 4px; }}
#NavTree::item:hover {{ background: {HOVER_BLUE}; }}
#NavTree::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}

QHeaderView {{ background: {SURFACE}; border: none; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; font-weight: 600; border: none;
    border-bottom: 1px solid {BORDER}; padding: 7px 8px;
}}
QHeaderView::section:hover {{ background: {HOVER_BLUE}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {SCROLL}; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 32px; }}
QScrollBar::handle:horizontal {{ min-width: 32px; }}
QScrollBar::handle:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {CARD_BG}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {TOOL_HOVER}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QProgressBar {{ background: {TRACK}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QProgressBar[state="done"]::chunk {{ background: {SUCCESS}; }}
QProgressBar[state="failed"]::chunk {{ background: {DANGER}; }}

#TransferCard {{ background: {CARD_BG}; border-radius: 14px; }}
#TransferHeader {{ background: {CARD_HEADER}; border-top-left-radius: 14px; border-top-right-radius: 14px; }}
#TransferHeader[collapsed="true"] {{ border-radius: 14px; }}
#TransferHeader QLabel {{ color: #ffffff; font-weight: 600; }}
#TransferHeader QToolButton {{ color: #ffffff; padding: 4px; border-radius: 14px; }}
#TransferHeader QToolButton:hover {{ background: {CARD_HEADER_HOVER}; }}
#TransferHeader #ClearButton {{ color: #d6d6d6; font-weight: 400; padding: 4px 8px; }}
#TransferBody {{ background: transparent; border: none; }}
#TransferRow {{ border-bottom: 1px solid {ROW_LINE}; }}
#TransferName {{ font-weight: 600; }}
#TransferPct, #TransferMeta {{ color: {MUTED}; font-size: 12px; }}
#TransferMore {{ color: {MUTED}; font-size: 12px; }}
#TransferMeta[state="failed"] {{ color: {DANGER}; }}
#RowAction {{ padding: 3px; border-radius: 11px; }}

#DetailTitle {{ font-size: 15px; font-weight: 700; }}
#Muted, #Placeholder {{ color: {MUTED}; }}
#SectionTitle {{ font-weight: 600; }}
#Mono {{ font-family: {MONO_FONTS}; font-size: 12px; }}
#PieceRow {{ background: {CONTROL}; border: 1px solid {BORDER}; border-radius: 8px; }}
#DetailsScroll {{ background: transparent; border: none; }}

#BrandHeader {{ border-bottom: 1px solid {BORDER}; }}
#BrandTitle {{ font-size: 17px; font-weight: 700; }}
#ErrorText {{ color: {DANGER}; }}
"""


# ---------------------------------------------------------------- switching modes


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None
_state = {"mode": "system", "dark": False, "hooked": False}


def notifier() -> _Notifier:
    """Emits ``changed`` after the colours switch between light and dark."""
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def current_mode() -> str:
    return _state["mode"]


def is_dark() -> bool:
    return _state["dark"]


def apply_theme(app: QApplication, mode: str = "system") -> None:
    """Use "light", "dark", or "system" (follow Windows and switch along with it)."""
    mode = mode if mode in MODES else "system"
    _state["mode"] = mode
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+: also gives a matching (dark) title bar
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(mode, Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)
    if not _state["hooked"] and hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(lambda _scheme: _on_system_change(app))
        _state["hooked"] = True
    _paint(app, _wants_dark(app))


def _on_system_change(app: QApplication) -> None:
    if _state["mode"] == "system":
        dark = _wants_dark(app)
        if dark != _state["dark"]:
            _paint(app, dark)


def _wants_dark(app: QApplication) -> bool:
    if _state["mode"] != "system":
        return _state["mode"] == "dark"
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return _windows_uses_dark()


def _windows_uses_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _paint(app: QApplication, dark: bool) -> None:
    _state["dark"] = dark
    globals().update(DARK if dark else LIGHT)
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.Base, CONTROL),
        (QPalette.ColorRole.AlternateBase, HOVER),
        (QPalette.ColorRole.Button, CONTROL),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.BrightText, TEXT),
        (QPalette.ColorRole.Highlight, ACCENT_SOFT),
        (QPalette.ColorRole.HighlightedText, ACCENT_TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
        (QPalette.ColorRole.ToolTipBase, CARD_HEADER),
        (QPalette.ColorRole.ToolTipText, "#ffffff"),
        (QPalette.ColorRole.Link, ACCENT),
        (QPalette.ColorRole.Light, BORDER),
        (QPalette.ColorRole.Midlight, BORDER),
        (QPalette.ColorRole.Mid, BORDER_STRONG),
        (QPalette.ColorRole.Dark, BORDER_STRONG),
        (QPalette.ColorRole.Shadow, "#000000"),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet() + _arrow_rules())
    notifier().changed.emit()


def _arrow_rules() -> str:
    """Spin box and drop-down arrows. Qt stylesheets need them as image files."""
    from ..config import cache_dir

    folder = cache_dir() / "theme"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("chevron_up", "chevron_down"):
        for state, color in (("", MUTED), ("_disabled", DISABLED)):
            path = folder / f"{name}-{color.lstrip('#')}.svg"
            path.write_text(_icon_svg(name, color, 2.4), encoding="utf-8")
            paths[name + state] = path.as_posix()
    return f"""
QSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 22px; border: none; border-left: 1px solid {BORDER}; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 8px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {SUBTLE}; }}
QSpinBox::up-arrow {{ image: url("{paths['chevron_up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{paths['chevron_down']}"); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url("{paths['chevron_up_disabled']}"); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url("{paths['chevron_down_disabled']}"); }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: none; }}
QComboBox::down-arrow {{ image: url("{paths['chevron_down']}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url("{paths['chevron_down_disabled']}"); }}
"""


def restyle(widget) -> None:
    """Re-apply the stylesheet after changing a dynamic property used in a selector."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ---------------------------------------------------------------- line icons

_STROKE = {
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    "new_folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    '<path d="M12 10v6M9 13h6"/>',
    "folder_open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3 7v10a2 2 0 0 0 2 2h12.5'
    'a2 2 0 0 0 1.9-1.4L21.5 12H7.3a2 2 0 0 0-1.9 1.4L3 19"/>',
    "delete": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1'
    'M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "drive": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "circle": '<circle cx="12" cy="12" r="7"/>',
    "retry": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "arrow_up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "arrow_down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
    'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "move": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    '<path d="M10 13h6M13 10l3 3-3 3"/>',
}
_FILLED = {
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
}

_SCALE = 2  # draw icons at 2x so they stay sharp on high-DPI screens


def _svg_pixmap(svg: str, size: int) -> QPixmap:
    pixmap = QPixmap(size * _SCALE, size * _SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * _SCALE, size * _SCALE))
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return pixmap


def _icon_svg(name: str, color: str, stroke: float) -> str:
    if name in _FILLED:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">{_FILLED[name]}</svg>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_STROKE[name]}</svg>'
    )


def _render_svg(painter: QPainter, svg: str, rect: QRectF) -> None:
    QSvgRenderer(QByteArray(svg.encode())).render(painter, rect)


@lru_cache(maxsize=512)
def _icon_pixmap(name: str, color: str, size: int, stroke: float) -> QPixmap:
    return _svg_pixmap(_icon_svg(name, color, stroke), size)


def icon_pixmap(name: str, color: str | None = None, size: int = 18, stroke: float = 1.9) -> QPixmap:
    """A line icon; ``color`` defaults to the current text colour."""
    return _icon_pixmap(name, color or TEXT, size, stroke)


def icon(name: str, color: str | None = None, size: int = 18, stroke: float = 1.9) -> QIcon:
    return QIcon(icon_pixmap(name, color, size, stroke))


# ---------------------------------------------------------------- the Shardbox icon

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def brand_svg(small: bool = False) -> str:
    """The app icon: a blue tile holding a white square split into three shards.

    ``small`` widens the gaps (and drops the offset of the last shard) so the cracks
    stay visible at 16-24 px.
    """
    lo, hi, c1, c2, gap, shift = (52, 204, 226, 290, 22, 0) if small else (64, 192, 226, 286, 10, 7)
    a, b = c1 - gap / 2, c1 + gap / 2  # first crack: x + y = c1
    c, d = c2 - gap / 2, c2 + gap / 2  # second crack: x + y = c2

    def pts(points):
        return " ".join(f"{x:g},{y:g}" for x, y in points)

    shard1 = [(lo, lo), (a - lo, lo), (lo, a - lo)]
    shard2 = [(b - lo, lo), (hi, lo), (hi, c - hi), (c - hi, hi), (lo, hi), (lo, b - lo)]
    shard3 = [(hi + shift, d - hi + shift), (hi + shift, hi + shift), (d - hi + shift, hi + shift)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#2b8be0"/><stop offset="1" stop-color="#004c99"/>'
        "</linearGradient></defs>"
        '<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#g)"/>'
        f'<polygon points="{pts(shard1)}" fill="#ffffff"/>'
        f'<polygon points="{pts(shard2)}" fill="#ffffff" fill-opacity="0.85"/>'
        f'<polygon points="{pts(shard3)}" fill="#ffffff" fill-opacity="0.7"/>'
        "</svg>"
    )


@lru_cache(maxsize=16)
def logo_pixmap(size: int = 28) -> QPixmap:
    """The Shardbox icon at ``size`` logical pixels (sharp on high-DPI screens)."""
    return _svg_pixmap(brand_svg(small=size <= 24), size)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Window/taskbar icon with every common size, so Windows never has to scale a blurry one."""
    result = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _render_svg(painter, brand_svg(small=size <= 24), QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result


# ---------------------------------------------------------------- file types

_KINDS: list[tuple[str, str, set[str]]] = [
    ("Video", "#7c3aed", {"mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv", "flv", "mpg", "mpeg", "3gp", "ts"}),
    ("Image", "#15803d", {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "svg", "raw", "cr2", "nef"}),
    ("Audio", "#be185d", {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "wma"}),
    ("Archive", "#b45309", {"zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "zst"}),
    ("PDF document", "#c62828", {"pdf"}),
    ("Document", "#1d4ed8", {"doc", "docx", "odt", "rtf", "txt", "md", "epub"}),
    ("Spreadsheet", "#047857", {"xls", "xlsx", "csv", "ods"}),
    ("Presentation", "#c2410c", {"ppt", "pptx", "odp", "key"}),
    ("Disk image", "#475569", {"iso", "img", "dmg", "vhd", "vhdx", "vmdk", "qcow2"}),
    ("Program", "#0e7490", {"exe", "msi", "apk", "appimage", "deb", "rpm", "pkg"}),
    ("Code", "#334155", {"py", "js", "ts", "html", "css", "json", "java", "c", "cpp", "h", "go", "rs", "sh", "sql", "xml", "yml", "yaml"}),
]
_OTHER_COLOR = "#6b7280"


def file_kind(name: str) -> tuple[str, str, str]:
    """(badge label, badge colour, description) for a file name."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""
    for description, color, exts in _KINDS:
        if ext in exts:
            return ext.upper()[:4], color, description
    if ext and len(ext) <= 4 and ext.isalnum():
        return ext.upper(), _OTHER_COLOR, f"{ext.upper()} file"
    return "FILE", _OTHER_COLOR, "File"


@lru_cache(maxsize=512)
def _badge(label: str, color: str, size: int) -> QPixmap:
    s = size * _SCALE
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    font = QFont(QApplication.font())
    font.setBold(True)
    font.setPixelSize(max(6, round(s * (0.36 if len(label) <= 3 else 0.29))))
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, label)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return pixmap


def badge_pixmap(name: str, size: int = 22) -> QPixmap:
    label, color, _ = file_kind(name)
    return _badge(label, color, size)


def badge_icon(name: str, size: int = 22) -> QIcon:
    return QIcon(badge_pixmap(name, size))


_FOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    f'<path fill="{FOLDER_BACK}" d="M2.5 6.5a2 2 0 0 1 2-2h4.2l2 2h8.8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2z"/>'
    f'<path fill="{FOLDER_FRONT}" d="M2.5 9.5a1.5 1.5 0 0 1 1.5-1.5h16a1.5 1.5 0 0 1 1.5 1.5V17a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2z"/>'
    "</svg>"
)


@lru_cache(maxsize=8)
def folder_icon(size: int = 20) -> QIcon:
    return QIcon(_svg_pixmap(_FOLDER_SVG, size))


def folder_tile(size: int = 56) -> QPixmap:
    """A big folder picture for the details panel."""
    return _folder_tile(size, FOLDER_TILE)


@lru_cache(maxsize=16)
def _folder_tile(size: int, background: str) -> QPixmap:
    s = size * _SCALE
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    pad = s * 0.18
    _render_svg(painter, _FOLDER_SVG, QRectF(pad, pad, s - 2 * pad, s - 2 * pad))
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return pixmap
