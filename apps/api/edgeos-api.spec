# -*- mode: python ; coding: utf-8 -*-
"""One-file Windows bundle for the localhost-only EDGEOS desktop API."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


api_root = Path(SPECPATH)
datas = collect_data_files("edgeos") + copy_metadata("edgeos-api")
hiddenimports = collect_submodules("edgeos") + collect_submodules("uvicorn")

analysis = Analysis(
    [str(api_root / "edgeos" / "desktop.py")],
    pathex=[str(api_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mypy", "pytest", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

# Keep this as a console executable.  The Electron host launches it with
# windowsHide=true, while retaining stdout/stderr for diagnostics and allowing
# Ctrl+C when an operator runs it directly.
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="edgeos-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
