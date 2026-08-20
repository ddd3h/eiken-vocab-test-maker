# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['vocab_test_maker.py'],
    pathex=[],
    binaries=[],
    datas=[('data/eiken2_pass_tan_1700.csv', 'data')],
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
    icon=None,
    bundle_identifier=None,
)
