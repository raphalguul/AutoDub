# -*- mode: python ; coding: utf-8 -*-
import os

use_upx = os.environ.get('AUTODUB_NOUPX', '0') != '1'

block_cipher = None

a = Analysis(
    ['wtdRenamer.py'],
    pathex=[],
    binaries=[],
    datas=[('Icons\\WTD File Renamer.ico', 'Icons')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pyi_rth_pkgres', 'matplotlib', 'scipy', 'PIL', 'Pillow', 'cv2', 'setuptools', 'pip'],
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
    name='wtdRenamer',
    icon='Icons\\WTD File Renamer.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=use_upx,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
