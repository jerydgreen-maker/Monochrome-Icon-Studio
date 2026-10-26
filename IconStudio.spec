# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["icon_studio.py"],
    pathex=[],
    binaries=[],
    datas=[("logo.jpg", "."), ("app_icon.ico", ".")],
    hiddenimports=["PIL._tkinter_finder", "icon_gen"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IconStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="app_icon.ico",
)
