# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\evana\\OneDrive\\Documents\\oracle-radio\\oradio_player.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\evana\\OneDrive\\Documents\\oracle-radio\\plugins', 'plugins'), ('C:\\Users\\evana\\OneDrive\\Documents\\oracle-radio\\spec', 'spec')],
    hiddenimports=['oradio_runtime', 'loom_player_ui', 'descriptor_club_gate', 'pydantic', 'cv2', 'PIL._tkinter_finder'],
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
    name='loom',
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
    name='loom',
)
