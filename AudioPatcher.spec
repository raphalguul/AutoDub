# -*- mode: python ; coding: utf-8 -*-
import os
import sys
sys.setrecursionlimit(sys.getrecursionlimit() * 5)

datas = [('VERSION', '.'), ('Icons\\AudioPatcher.ico', 'Icons'), ('Icons\\AutoDub.ico', 'Icons')]
binaries = []
hiddenimports = ['pkg_resources']

use_upx = os.environ.get('AUTODUB_NOUPX', '0') != '1'

block_cipher = None

a = Analysis(
    ['AudioPatcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pyi_rth_pkgres', 'pkg_resources',
        'matplotlib', 'pandas', 'scipy', 'PIL', 'Pillow',
        'cv2', 'tensorboard', 'PyQt5', 'PySide6', 'PySide2',
        'IPython', 'notebook', 'jedi', 'setuptools', 'pip',
        'torch', 'torchvision', 'torchaudio',
        'whisper', 'stable_whisper',
        'tests', 'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='AudioPatcher',
    icon='Icons\\AudioPatcher.ico',
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
    contents_directory='_internal_audiopatcher',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=use_upx,
    upx_exclude=[],
    name='AudioPatcher',
)
