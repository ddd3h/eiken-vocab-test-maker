# -*- mode: python ; coding: utf-8 -*-

import sys

ICON_DIR = "assets/icon"
# Windows exe は .ico、macOS .app は .icns。Linuxはアイコン埋め込み非対応のため付けない。
EXE_ICON = f"{ICON_DIR}/EikenVocabTestMaker.ico" if sys.platform == "win32" else None
BUNDLE_ICON = f"{ICON_DIR}/EikenVocabTestMaker.icns" if sys.platform == "darwin" else None

a = Analysis(
    ['vocab_test_maker.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('data/eiken2_pass_tan_1700.csv', 'data'),
        ('data/target_1900.csv', 'data'),
        ('data/target_1200.csv', 'data'),
        ('data/eiken_pre1_pass_tan_1900.csv', 'data'),
        ('assets/icon/EikenVocabTestMaker-256.png', 'assets/icon'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EikenVocabTestMaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=EXE_ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EikenVocabTestMaker',
)
app = BUNDLE(
    coll,
    name='EikenVocabTestMaker.app',
    icon=BUNDLE_ICON,
    bundle_identifier=None,
)
