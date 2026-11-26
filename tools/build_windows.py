"""Build Shardbox for Windows: the app, the installer and a zip, ready for a GitHub Release.

    pip install -r requirements-build.txt
    python tools/build_windows.py

Needs Inno Setup 6 for the installer (https://jrsoftware.org/isinfo.php, or
`winget install JRSoftware.InnoSetup`). Without it, only the app folder and zip are made.

Output in dist/:
  Shardbox/                       the app (Shardbox.exe + its files)
  Shardbox-<version>-Setup.exe    the installer
  Shardbox-<version>-win64.zip    the same app as a zip (unzip and run Shardbox.exe)
  SHA256SUMS.txt                  checksums to paste into the release notes
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from shardbox import __version__

    return __version__


def write_version_info(ver: str) -> None:
    """File properties shown by Windows (right-click Shardbox.exe → Properties → Details)."""
    nums = [int(p) for p in (ver.split(".") + ["0", "0", "0"])[:4]]
    tup = ", ".join(str(n) for n in nums)
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'The Shardbox contributors'),
      StringStruct('FileDescription', 'Shardbox'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'Shardbox'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'Shardbox.exe'),
      StringStruct('ProductName', 'Shardbox'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def find_iscc() -> Path | None:
    candidates = [
        os.environ.get("ISCC", ""),
        shutil.which("iscc") or "",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def run(cmd: list[str]) -> None:
    print("\n>", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Run this on Windows.")
    ver = version()
    print(f"Building Shardbox {ver}")

    run([sys.executable, "tools/make_icons.py"])
    write_version_info(ver)
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
        "packaging/shardbox.spec",
    ])
    app_dir = DIST / "Shardbox"
    if not (app_dir / "Shardbox.exe").is_file():
        sys.exit("PyInstaller did not produce dist/Shardbox/Shardbox.exe")

    outputs: list[Path] = []
    zip_base = DIST / f"Shardbox-{ver}-win64"
    print(f"\n> zip {zip_base.name}.zip")
    outputs.append(Path(shutil.make_archive(str(zip_base), "zip", root_dir=DIST, base_dir="Shardbox")))

    iscc = find_iscc()
    if iscc is None:
        print("\nInno Setup not found: skipping the installer. Install it with: winget install JRSoftware.InnoSetup")
    else:
        run([
            str(iscc), "/Q",
            f"/DAppVersion={ver}", f"/DSourceDir={app_dir}", f"/DOutputDir={DIST}",
            str(ROOT / "packaging" / "shardbox.iss"),
        ])
        outputs.append(DIST / f"Shardbox-{ver}-Setup.exe")

    sums = "".join(f"{sha256(p)}  {p.name}\n" for p in outputs)
    (DIST / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    print("\nDone:")
    for p in outputs:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size / 1024 / 1024:.1f} MB)")
    print("  dist/SHA256SUMS.txt")


if __name__ == "__main__":
    main()
