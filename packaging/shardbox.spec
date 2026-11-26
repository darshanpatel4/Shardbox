# PyInstaller recipe for the Windows build of Shardbox.
# Don't run this directly; use:  python tools/build_windows.py
# (it first writes build/version_info.txt, which this file needs).

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    hiddenimports=[],
    excludes=[
        # Not used by Shardbox; leaving them out keeps the download smaller.
        "tkinter",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtPdf",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shardbox",
    console=False,  # a normal windowed app: no black console window
    icon=str(ROOT / "assets" / "shardbox.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="Shardbox", upx=False)
