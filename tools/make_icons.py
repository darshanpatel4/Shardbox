"""Regenerate the icon files in assets/ from the icon drawn in shardbox/ui/theme.py.

    python tools/make_icons.py

Writes:
  assets/shardbox.svg        the icon (vector)
  assets/shardbox.ico        Windows icon with every size from 16 to 256 px (used for the .exe)
  assets/shardbox-<n>.png    PNGs for the README, GitHub and Linux desktops
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from shardbox.ui.theme import ICON_SIZES, brand_svg  # noqa: E402

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512)


def render_png(size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(brand_svg(small=size <= 24).encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    """An .ico holding one PNG per size (supported since Windows Vista)."""
    images = [(size, render_png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.write_bytes(header + entries + blobs)


def main() -> None:
    QGuiApplication([])
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    (out / "shardbox.svg").write_text(brand_svg() + "\n", encoding="utf-8")
    write_ico(out / "shardbox.ico", ICON_SIZES)
    for size in PNG_SIZES:
        (out / f"shardbox-{size}.png").write_bytes(render_png(size))
    print("icons written to", out)


if __name__ == "__main__":
    main()
