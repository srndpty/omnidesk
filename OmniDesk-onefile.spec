# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None

app_root = Path.cwd()

a = Analysis(
    ["main.py"],
    pathex=[str(app_root)],
    binaries=[],
    datas=[
        (str(app_root / "resources"), "resources"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OmniDesk-onefile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    icon=str(app_root / "resources" / "icons" / "app_icon.ico"),
)
